"""An isolated localhost origin: editor credentials never reach the browser."""
import asyncio
from contextlib import nullcontext
import secrets
import socket
import time
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask
from websockets.asyncio.client import connect

HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}


class LocalProxyServer(uvicorn.Server):
    # The enclosing monitor owns process signals; child listeners must not replace them.
    def capture_signals(self):
        return nullcontext()


class EditorProxy:
    def __init__(self, upstream_port, cookie, parent_origin, *, transport=None):
        self.upstream = 'http://127.0.0.1:' + str(upstream_port)
        self.cookie = cookie
        self.parent_origin = parent_origin
        self.host = urlsplit(parent_origin).hostname
        self.port = None
        self.cookie_name = 'ilab_editor_' + secrets.token_hex(12)
        self.browser_keys = {}
        self.tickets = {}
        self.touched = time.monotonic()
        self.active_sockets = 0
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
        headers.update({'host': self.host + ':' + str(self.port), 'cookie': self.cookie, 'accept-encoding': 'identity'})
        return headers

    async def http(self, request: Request):
        path = request.url.path
        if path.startswith('/_ilab_open/'):
            if not self.allowed(request, entry=True) or request.method != 'GET': return PlainTextResponse('Forbidden', 403)
            stamp = self.tickets.pop(path.rsplit('/', 1)[-1], None)
            if stamp is None or time.monotonic() - stamp >= 60: return PlainTextResponse('入口已过期，请返回重新打开。', 403)
            key = secrets.token_urlsafe(32)
            self.browser_keys[key] = time.monotonic()
            if len(self.browser_keys) > 32: self.browser_keys.pop(next(iter(self.browser_keys)))
            response = RedirectResponse('/', status_code=303)
            response.set_cookie(self.cookie_name, key, httponly=True, samesite='strict', path='/')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            return response
        if not self.allowed(request): return PlainTextResponse('Forbidden', 403)
        if path.rstrip('/') in ('/login', '/logout'): return PlainTextResponse('请返回监控网站重新连接编辑器。', 502)
        self.touched = time.monotonic()
        try:
            url = self.upstream + request.scope['raw_path'].decode('ascii')
            if request.url.query: url += '?' + request.url.query
            upstream = await self.client.send(self.client.build_request(request.method, url, headers=self.headers(request), content=request.stream()), stream=True)
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
            return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers, background=BackgroundTask(upstream.aclose))
        except (httpx.HTTPError, OSError):
            return PlainTextResponse('编辑器连接中断，请返回重新连接。', 502)

    async def websocket(self, ws: WebSocket):
        if not self.allowed(ws) or ws.headers.get('origin') != self.origin:
            await ws.close(code=1008)
            return
        jobs = []
        self.active_sockets += 1
        try:
            url = self.upstream.replace('http:', 'ws:') + ws.scope['raw_path'].decode('ascii')
            if ws.url.query: url += '?' + ws.url.query
            protocols = [x.strip() for x in ws.headers.get('sec-websocket-protocol', '').split(',') if x.strip()]
            async with connect(url, additional_headers={'Cookie': self.cookie}, origin=self.upstream, subprotocols=protocols or None, proxy=None, max_size=16 * 1024 * 1024, open_timeout=15) as remote:
                await ws.accept(subprotocol=remote.subprotocol)
                async def incoming():
                    while True:
                        msg = await ws.receive()
                        if msg['type'] == 'websocket.disconnect': return
                        self.touched = time.monotonic()
                        await remote.send(msg.get('bytes') if msg.get('bytes') is not None else msg['text'])
                async def outgoing():
                    async for msg in remote:
                        self.touched = time.monotonic()
                        if isinstance(msg, bytes): await ws.send_bytes(msg)
                        else: await ws.send_text(msg)
                jobs = [asyncio.create_task(incoming()), asyncio.create_task(outgoing())]
                done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                for job in done: job.result()
        except Exception:
            pass
        finally:
            for job in jobs: job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            self.active_sockets -= 1
            self.touched = time.monotonic()
            try: await ws.close()
            except Exception: pass

    async def healthy(self):
        try:
            response = await self.client.get(self.upstream + '/', headers={'cookie': self.cookie}, timeout=8)
            return response.status_code in (200, 302, 303) and urlsplit(response.headers.get('location', '')).path.rstrip('/') != '/login'
        except (httpx.HTTPError, OSError):
            return False

    async def start(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.port = sock.getsockname()[1]
        self.server = LocalProxyServer(uvicorn.Config(self.app, log_level='critical', access_log=False, lifespan='off', ws_max_size=16 * 1024 * 1024))
        self.server_task = asyncio.create_task(self.server.serve(sockets=[sock]))
        for _ in range(100):
            if self.server.started: return
            if self.server_task.done(): self.server_task.result()
            await asyncio.sleep(.02)
        await self.close()
        raise RuntimeError('editor proxy failed to start')

    async def close(self):
        if self.server:
            self.server.should_exit = True
        if self.server_task:
            try: await asyncio.wait_for(asyncio.shield(self.server_task), 3)
            except asyncio.TimeoutError:
                self.server.force_exit = True
                self.server_task.cancel()
                await asyncio.gather(self.server_task, return_exceptions=True)
        await self.client.aclose()
