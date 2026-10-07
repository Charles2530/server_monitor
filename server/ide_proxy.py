"""An isolated localhost origin: editor credentials never reach the browser."""
import asyncio
import json
from html import escape
from contextlib import AsyncExitStack, nullcontext
import logging
from pathlib import Path
import re
import secrets
import socket
import time
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from websockets.asyncio.client import connect
from .ide_assets import EditorAssetCache

logger = logging.getLogger(__name__)

TRANSPORT_PING_INTERVAL = 10
TRANSPORT_PING_TIMEOUT = 8

HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}
APPEARANCE_ROOT = Path(__file__).resolve().parent.parent / 'static/editor-appearance'
FONT_ASSETS = {'fonts.css': 'text/css', **{
    'fonts/JetBrainsMono-' + name + '.woff2': 'font/woff2'
    for name in ('Regular', 'Italic', 'Bold', 'BoldItalic')
}}


async def workbench_response(upstream, headers, parent_origin):
    # Decode only the workbench document; never rewrite webviews, JS or extension resources.
    if upstream.headers.get('content-encoding', '').lower() not in ('', 'identity', 'gzip', 'deflate'):
        # Some installations do not have HTTPX's optional Brotli decoder. Preserve
        # unsupported encodings byte-for-byte instead of emitting compressed HTML as text.
        return StreamingResponse(upstream.aiter_raw(), headers=headers, background=BackgroundTask(upstream.aclose))
    headers = {k: v for k, v in headers.items() if k.lower() not in ('content-length', 'content-encoding', 'etag', 'content-md5')}
    chunks, total = [], 0
    stream = upstream.aiter_bytes()
    async for chunk in stream:
        chunks.append(chunk)
        total += len(chunk)
        if total > 2 * 1024 * 1024:
            async def remainder():
                for part in chunks: yield part
                async for part in stream: yield part
            return StreamingResponse(remainder(), headers=headers, background=BackgroundTask(upstream.aclose))
    await upstream.aclose()
    content = b''.join(chunks)
    if b'id="vscode-workbench-web-configuration"' in content and b'</head>' in content:
        extras = ('<link rel="stylesheet" href="/_ilab_appearance/fonts.css">'
                  '<script src="/_ilab_frame_status.js" data-parent-origin="' + escape(parent_origin, quote=True) + '"></script>')
        content = content.replace(b'</head>', extras.encode() + b'</head>', 1)
    return Response(content, headers=headers)


class LocalProxyServer(uvicorn.Server):
    # The enclosing monitor owns process signals; child listeners must not replace them.
    def capture_signals(self):
        return nullcontext()


class EditorProxy:
    def __init__(self, upstream_port, cookie, parent_origin, *, transport=None, cache_dir=None, repair=None, origin_registry=None, origin_identity=None):
        self.upstream = 'http://127.0.0.1:' + str(upstream_port)
        self.cookie = cookie
        self.parent_origin = parent_origin
        self.host = urlsplit(parent_origin).hostname
        self.port = None
        self.origin_registry, self.origin_identity = origin_registry, origin_identity
        self.listener = None
        self.cookie_name = 'ilab_editor_' + secrets.token_hex(12)
        self.browser_keys = {}
        self.tickets = {}
        self.touched = time.monotonic()
        self.upstream_activity = (None, float('-inf'))
        self.active_sockets = 0
        self.assets = EditorAssetCache(cache_dir) if cache_dir is not None else None
        self.repair = repair
        self.repair_task = None
        self.repair_started = float('-inf')
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False, transport=transport, trust_env=False)
        self.server = None
        self.server_task = None
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.add_api_route('/{path:path}', self.http, methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
        self.app.add_api_websocket_route('/{path:path}', self.websocket)

    @property
    def origin(self):
        return 'http://' + self.host + ':' + str(self.port)

    def ticket(self):
        now = time.monotonic()
        self.tickets = {key: stamp for key, stamp in self.tickets.items() if now - stamp < 60}
        if len(self.tickets) >= 16: self.tickets.pop(next(iter(self.tickets)))
        key = secrets.token_urlsafe(32)
        self.tickets[key] = now
        return key

    def allowed(self, request, *, entry=False):
        if request.headers.get('host') != self.host + ':' + str(self.port): return False
        origin = request.headers.get('origin')
        if origin and origin != self.origin: return False
        if request.headers.get('sec-fetch-site') == 'cross-site': return False
        return entry or request.cookies.get(self.cookie_name) in self.browser_keys

    def headers(self, request):
        excluded = HOP | {'host', 'cookie', 'authorization', 'origin', 'referer', 'forwarded', 'accept-encoding'}
        excluded |= {x.strip().lower() for x in request.headers.get('connection', '').split(',')}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in excluded and not k.lower().startswith('x-forwarded-')}
        headers.update({'host': self.host + ':' + str(self.port), 'cookie': self.cookie, 'accept-encoding': request.headers.get('accept-encoding', 'identity')})
        return headers

    async def http(self, request: Request):
        path = request.url.path
        if path.startswith('/_ilab_open/'):
            if not self.allowed(request, entry=True) or request.method != 'GET': return PlainTextResponse('Forbidden', 403)
            stamp = self.tickets.pop(path.rsplit('/', 1)[-1], None)
            entry_headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
            if stamp is None or time.monotonic() - stamp >= 60:
                # Browser history may restore an already-consumed iframe URL.
                # Existing authentication suffices; never reauthorize from a used ticket.
                if self.allowed(request):
                    return RedirectResponse('/', status_code=303, headers=entry_headers)
                nonce = secrets.token_urlsafe(24)
                parent = json.dumps(self.parent_origin).replace('<', '\\u003c')
                message = json.dumps({'type': 'ilab-editor-entry-expired', 'entry': path}).replace('<', '\\u003c')
                body = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                        '<title>编辑器入口已过期</title><p>入口已过期，请点击上方“重新连接”。</p>'
                        f'<script nonce="{nonce}">window.parent.postMessage('
                        + message + ',' + parent + ');</script></html>')
                return HTMLResponse(body, status_code=403, headers={**entry_headers,
                    'Content-Security-Policy': f"default-src 'none'; script-src 'nonce-{nonce}'; frame-ancestors 'self' {self.parent_origin}",
                    'X-Content-Type-Options': 'nosniff'})
            key = secrets.token_urlsafe(32)
            self.browser_keys[key] = time.monotonic()
            if len(self.browser_keys) > 32: self.browser_keys.pop(next(iter(self.browser_keys)))
            if request.query_params.get('reauth') == '1':
                nonce = secrets.token_urlsafe(24)
                parent = json.dumps(self.parent_origin).replace('<', '\\u003c')
                message = json.dumps({'type': 'ilab-editor-authenticated', 'entry': path}).replace('<', '\\u003c')
                response = HTMLResponse('<!doctype html><meta charset="utf-8">'
                    f'<script nonce="{nonce}">window.parent.postMessage('
                    + message + ',' + parent + ');</script>', headers={
                        'Content-Security-Policy': f"default-src 'none'; script-src 'nonce-{nonce}'; frame-ancestors 'self' {self.parent_origin}",
                        'X-Content-Type-Options': 'nosniff'})
            else:
                response = RedirectResponse('/', status_code=303)
            response.set_cookie(self.cookie_name, key, httponly=True, samesite='strict', path='/')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            return response
        if not self.allowed(request): return PlainTextResponse('Forbidden', 403)
        if path == '/_ilab_frame_status.js':
            if request.method not in ('GET', 'HEAD'): return PlainTextResponse('Method not allowed', 405)
            return FileResponse(APPEARANCE_ROOT.parent / 'ide-frame-status.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache'})
        if path.startswith('/_ilab_appearance/'):
            if request.method not in ('GET', 'HEAD'): return PlainTextResponse('Method not allowed', 405)
            asset = path.removeprefix('/_ilab_appearance/')
            if asset not in FONT_ASSETS or not (APPEARANCE_ROOT / asset).is_file(): return PlainTextResponse('Not found', 404)
            self.touched = time.monotonic()
            return FileResponse(APPEARANCE_ROOT / asset, media_type=FONT_ASSETS[asset], headers={'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff'})
        if path.rstrip('/') in ('/login', '/logout'): return PlainTextResponse('请返回监控网站重新连接编辑器。', 502)
        self.touched = time.monotonic()
        cache_key = self.assets.key(request) if self.assets else None
        cached = await self.assets.get(cache_key) if self.assets else None
        if cached is not None:
            body, headers = cached
            directives = [x.strip() for x in headers.get('content-security-policy', '').split(';')
                          if x.strip() and not x.strip().startswith('frame-ancestors')]
            directives.append("frame-ancestors 'self' " + self.parent_origin)
            headers['content-security-policy'] = '; '.join(directives)
            headers['referrer-policy'] = 'no-referrer'
            if headers.get('etag') and request.headers.get('if-none-match') == headers['etag']:
                return Response(status_code=304, headers={k: v for k, v in headers.items() if k != 'content-length'})
            return Response(b'' if request.method == 'HEAD' else body, headers=headers)
        failed_upstream = self.upstream
        try:
            url = self.upstream + request.scope['raw_path'].decode('ascii')
            if request.url.query: url += '?' + request.url.query
            # Passing an async stream for a bodyless GET makes HTTPX add
            # Transfer-Encoding: chunked. code-server's webview/static routes
            # reject that framing (400 or a closed connection before headers).
            bodyless = (request.method in ('GET', 'HEAD')
                        and request.headers.get('content-length', '0') == '0'
                        and 'transfer-encoding' not in request.headers)
            content = None if bodyless else request.stream()
            outgoing_headers = self.headers(request)
            if path == '/' and request.method == 'GET': outgoing_headers['accept-encoding'] = 'gzip'
            resource = (path.startswith('/_static/') or
                        re.match(r'^/stable-[a-f0-9]{40}/(?:static/|vscode-remote-resource$)', path))
            retryable = bodyless and (resource or path == '/')
            for attempt in range(2):
                failed_upstream = self.upstream
                url = failed_upstream + request.scope['raw_path'].decode('ascii')
                if request.url.query: url += '?' + request.url.query
                outgoing_headers['cookie'] = self.cookie
                outgoing = self.client.build_request(request.method, url, headers=outgoing_headers, content=content)
                try:
                    upstream = await self.client.send(outgoing, stream=True)
                except (httpx.HTTPError, OSError) as exc:
                    if attempt or not retryable: raise
                    # A stale keep-alive can be retried directly. Other transport
                    # failures need a repaired tunnel before retrying a safe read.
                    if not (isinstance(exc, httpx.RemoteProtocolError) and resource):
                        if not await self.repair_connection(failed_upstream): raise
                    continue
                expired = upstream.status_code in (401, 403) or urlsplit(upstream.headers.get('location', '')).path.rstrip('/') == '/login'
                if not attempt and retryable and expired and self.repair is not None:
                    await upstream.aclose()
                    if await self.repair_connection(failed_upstream): continue
                break
            location = upstream.headers.get('location', '')
            target = urlsplit(location)
            if upstream.status_code in (401, 403) or target.path.rstrip('/') == '/login':
                await upstream.aclose()
                return PlainTextResponse('编辑器连接已过期，请返回重新连接。', 502)
            excluded = HOP | {'set-cookie', 'x-frame-options', 'content-security-policy', 'content-security-policy-report-only'}
            excluded |= {x.strip().lower() for x in upstream.headers.get('connection', '').split(',')}
            headers = {k: v for k, v in upstream.headers.items() if k.lower() not in excluded}
            if location and (target.netloc or target.scheme):
                if target.netloc not in (urlsplit(self.upstream).netloc, self.host + ':' + str(self.port)):
                    await upstream.aclose()
                    return PlainTextResponse('编辑器返回了不允许的跳转。', 502)
                headers['location'] = target.path + ('?' + target.query if target.query else '')
            csp = upstream.headers.get('content-security-policy', '')
            directives = [x.strip() for x in csp.split(';') if x.strip() and not x.strip().startswith('frame-ancestors')]
            directives.append("frame-ancestors 'self' " + self.parent_origin)
            headers['content-security-policy'] = '; '.join(directives)
            headers['referrer-policy'] = 'no-referrer'
            if path == '/' and request.method == 'GET' and upstream.status_code == 200 and upstream.headers.get('content-type', '').split(';')[0] == 'text/html':
                return await workbench_response(upstream, headers, self.parent_origin)
            stream = self.assets.stream(cache_key, upstream) if self.assets and request.method == 'GET' else upstream.aiter_raw()
            return StreamingResponse(stream, status_code=upstream.status_code, headers=headers, background=BackgroundTask(upstream.aclose))
        except (httpx.HTTPError, OSError) as exc:
            logger.warning('Editor HTTP transport failed (%s)', type(exc).__name__)
            self.start_repair(failed_upstream)
            return PlainTextResponse('编辑器连接中断，请返回重新连接。', 502)

    def start_repair(self, failed_upstream=None):
        if self.repair is None: return None
        if self.repair_task is not None and (not self.repair_task.done() or time.monotonic() - self.repair_started < 3):
            return self.repair_task
        self.repair_started = time.monotonic()
        async def run():
            try:
                await asyncio.wait_for(self.repair(failed_upstream), 60)
                return True
            except Exception as exc:
                logger.warning('Editor automatic repair failed (%s)', type(exc).__name__)
                return False
        self.repair_task = asyncio.create_task(run())
        return self.repair_task

    async def repair_connection(self, failed_upstream=None):
        job = self.start_repair(failed_upstream)
        return await asyncio.shield(job) if job is not None else False

    def note_upstream_activity(self, upstream):
        if upstream == self.upstream:
            self.upstream_activity = (upstream, time.monotonic())

    def has_upstream_activity(self, upstream):
        observed_upstream, observed_at = self.upstream_activity
        return (upstream == self.upstream == observed_upstream
                and time.monotonic() - observed_at < TRANSPORT_PING_TIMEOUT)

    async def watch_transport(self, remote, upstream):
        # Suspect a silent tunnel before VS Code's own reconnect timer fires.
        # A missed pong alone never closes a stream: repair first confirms HTTP
        # health and revalidates ownership before replacing the SSH transport.
        probe = None
        async def ping():
            pong = await remote.ping()
            await pong
            self.note_upstream_activity(upstream)
        try:
            while upstream == self.upstream:
                await asyncio.sleep(TRANSPORT_PING_INTERVAL)
                if upstream != self.upstream: return
                if probe is None: probe = asyncio.create_task(ping())
                try:
                    await asyncio.wait_for(asyncio.shield(probe), TRANSPORT_PING_TIMEOUT)
                    probe = None
                except asyncio.TimeoutError:
                    # Retain at most one pending ping; a slow healthy connection
                    # can acknowledge it later without reconnecting the browser.
                    if upstream != self.upstream: return
                    if not self.has_upstream_activity(upstream):
                        await self.repair_connection(upstream)
        finally:
            if probe is not None:
                probe.cancel()
                await asyncio.gather(probe, return_exceptions=True)

    async def websocket(self, ws: WebSocket):
        if not self.allowed(ws) or ws.headers.get('origin') != self.origin:
            await ws.close(code=1008)
            return
        jobs = []
        close_code = 1000
        phase = 'connect'
        failed_upstream = self.upstream
        self.active_sockets += 1
        try:
            protocols = [x.strip() for x in ws.headers.get('sec-websocket-protocol', '').split(',') if x.strip()]
            async with AsyncExitStack() as stack:
                # Repair before accepting or relaying any browser bytes. Once the
                # stream starts, VS Code owns its reconnection/resume protocol.
                for attempt in range(2):
                    failed_upstream = self.upstream
                    url = failed_upstream.replace('http:', 'ws:') + ws.scope['raw_path'].decode('ascii')
                    if ws.url.query: url += '?' + ws.url.query
                    try:
                        remote = await stack.enter_async_context(connect(url, additional_headers={'Cookie': self.cookie}, origin=self.upstream, subprotocols=protocols or None, proxy=None, max_size=16 * 1024 * 1024, open_timeout=15, ping_interval=20, ping_timeout=120))
                        break
                    except Exception:
                        if attempt or not await self.repair_connection(failed_upstream): raise
                await ws.accept(subprotocol=remote.subprotocol)
                phase = 'relay'
                async def incoming():
                    while True:
                        msg = await ws.receive()
                        if msg['type'] == 'websocket.disconnect': return
                        self.touched = time.monotonic()
                        await remote.send(msg.get('bytes') if msg.get('bytes') is not None else msg['text'])
                async def outgoing():
                    async for msg in remote:
                        self.note_upstream_activity(failed_upstream)
                        self.touched = time.monotonic()
                        if isinstance(msg, bytes): await ws.send_bytes(msg)
                        else: await ws.send_text(msg)
                jobs = [asyncio.create_task(incoming()), asyncio.create_task(outgoing())]
                if self.repair is not None:
                    jobs.append(asyncio.create_task(self.watch_transport(remote, failed_upstream)))
                done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                for job in done: job.result()
        except Exception as exc:
            close_code = 1011
            if phase == 'relay' and not isinstance(exc, WebSocketDisconnect): self.start_repair(failed_upstream)
            # Paths, queries, cookies and exception messages may contain secrets.
            logger.warning('Editor WebSocket %s failed (%s)', phase, type(exc).__name__)
        finally:
            for job in jobs: job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            self.active_sockets -= 1
            self.touched = time.monotonic()
            try: await ws.close(code=close_code)
            except Exception: pass

    async def healthy(self):
        upstream = self.upstream
        healthy = None
        try:
            async with self.client.stream('GET', upstream + '/', headers={'cookie': self.cookie}, timeout=8) as response:
                healthy = response.status_code in (200, 302, 303) and urlsplit(response.headers.get('location', '')).path.rstrip('/') != '/login'
                return healthy
        except (httpx.HTTPError, OSError):
            # A close failure must not override a status already received.
            if healthy is not None: return healthy
            return self.has_upstream_activity(upstream)

    async def start(self):
        try:
            if self.origin_identity is not None:
                if self.origin_registry is None: raise ValueError('editor origin registry missing')
                self.listener = await self.origin_registry.bind(self.origin_identity)
            else:
                self.listener = socket.socket()
                self.listener.bind(('127.0.0.1', 0))
            self.port = self.listener.getsockname()[1]
            self.server = LocalProxyServer(uvicorn.Config(self.app, log_level='critical', access_log=False, lifespan='off', ws_max_size=16 * 1024 * 1024, ws_ping_interval=20, ws_ping_timeout=120))
            self.server_task = asyncio.create_task(self.server.serve(sockets=[self.listener]))
            for _ in range(100):
                if self.server.started: return
                if self.server_task.done():
                    self.server_task.result()
                    break
                await asyncio.sleep(.02)
            raise RuntimeError('editor proxy failed to start')
        except BaseException:
            await self.close()
            raise

    async def close(self):
        try:
            if self.repair_task:
                self.repair_task.cancel()
                await asyncio.gather(self.repair_task, return_exceptions=True)
            if self.server:
                self.server.should_exit = True
            if self.server_task:
                done, _ = await asyncio.wait({self.server_task}, timeout=3)
                if not done:
                    self.server.force_exit = True
                    self.server_task.cancel()
                await asyncio.gather(self.server_task, return_exceptions=True)
        finally:
            if self.listener is not None:
                self.listener.close()
                self.listener = None
            await self.client.aclose()
