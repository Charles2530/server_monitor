import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.websockets import WebSocket

from server.ide_proxy import EditorProxy


@pytest.mark.asyncio
async def test_silent_transport_is_probed_without_closing_a_slow_connection(monkeypatch):
    import server.ide_proxy as module
    monkeypatch.setattr(module, 'TRANSPORT_PING_INTERVAL', .001, raising=False)
    monkeypatch.setattr(module, 'TRANSPORT_PING_TIMEOUT', .01, raising=False)
    pong = asyncio.Future(); repaired = asyncio.Event(); probes = []
    async def ping(): probes.append(1); return pong
    async def repair(failed_upstream):
        assert failed_upstream == proxy.upstream
        repaired.set()  # A successful HTTP confirmation keeps this transport.
    remote = SimpleNamespace(ping=ping, close=AsyncMock())
    proxy = EditorProxy(12345, 'private', 'http://localhost:8766', repair=repair)
    watcher = None
    try:
        assert hasattr(proxy, 'watch_transport'), 'Accepted streams need a browser-independent liveness probe'
        watcher = asyncio.create_task(proxy.watch_transport(remote, proxy.upstream))
        await asyncio.wait_for(repaired.wait(), .3)
        assert not watcher.done() and not pong.done()
        assert probes == [1]  # Retain one outstanding ping even while it is slow.
        remote.close.assert_not_awaited()
        pong.set_result(.02)
    finally:
        if watcher:
            watcher.cancel(); await asyncio.gather(watcher, return_exceptions=True)
        await proxy.close()


@pytest.mark.asyncio
async def test_stalled_probe_releases_old_stream_after_transport_replacement(monkeypatch):
    import server.ide_proxy as module
    monkeypatch.setattr(module, 'TRANSPORT_PING_INTERVAL', .001, raising=False)
    monkeypatch.setattr(module, 'TRANSPORT_PING_TIMEOUT', .01, raising=False)
    pong = asyncio.Future()
    async def ping(): return pong
    async def repair(failed_upstream): proxy.upstream = 'http://127.0.0.1:34567'
    proxy = EditorProxy(12345, 'private', 'http://localhost:8766', repair=repair)
    try:
        assert hasattr(proxy, 'watch_transport'), 'A stalled stream must observe a replacement'
        await asyncio.wait_for(proxy.watch_transport(SimpleNamespace(ping=ping), proxy.upstream), .3)
        assert pong.cancelled()
        assert proxy.upstream == 'http://127.0.0.1:34567'
    finally: await proxy.close()


@pytest.mark.asyncio
async def test_websocket_relay_keeps_slow_stream_and_joins_probe_on_disconnect(monkeypatch):
    import server.ide_proxy as module
    monkeypatch.setattr(module, 'TRANSPORT_PING_INTERVAL', .001)
    monkeypatch.setattr(module, 'TRANSPORT_PING_TIMEOUT', .01)
    received = asyncio.Queue(); sent = []; repaired = asyncio.Event()
    pong = asyncio.Future()
    await received.put({'type': 'websocket.connect'})
    class Remote:
        subprotocol = None
        async def ping(self): return pong
        def __aiter__(self): return self
        async def __anext__(self): await asyncio.Event().wait()
        async def send(self, data): pass
    class Dial:
        async def __aenter__(self): return Remote()
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(module, 'connect', lambda *args, **kwargs: Dial())
    async def repair(failed_upstream): repaired.set()
    proxy = EditorProxy(12345, 'private', 'http://localhost:8766', repair=repair)
    proxy.port = 23456; proxy.browser_keys['browser'] = 1
    async def send(message): sent.append(message)
    ws = WebSocket({'type': 'websocket', 'scheme': 'ws', 'path': '/socket', 'raw_path': b'/socket',
                    'query_string': b'', 'headers': [(b'host', b'localhost:23456'),
                    (b'origin', b'http://localhost:23456'), (b'cookie', (proxy.cookie_name + '=browser').encode())]}, received.get, send)
    relay = asyncio.create_task(proxy.websocket(ws))
    try:
        try: await asyncio.wait_for(repaired.wait(), .3)
        except asyncio.TimeoutError: pytest.fail('An accepted silent stream never started automatic repair')
        assert not relay.done() and not pong.done()
        assert not any(message['type'] == 'websocket.close' for message in sent)
        await received.put({'type': 'websocket.disconnect', 'code': 1000})
        await asyncio.wait_for(relay, .3)
        assert pong.cancelled() and proxy.active_sockets == 0
    finally:
        relay.cancel(); await asyncio.gather(relay, return_exceptions=True)
        await proxy.close()


@pytest.mark.asyncio
async def test_concurrent_transport_failures_share_one_repair():
    gate = asyncio.Event(); entered = asyncio.Event(); calls = []
    async def repair(failed_upstream=None): calls.append(1); entered.set(); await gate.wait()
    proxy = EditorProxy(12345, 'private', 'http://localhost:8766', repair=repair)
    try:
        jobs = [asyncio.create_task(proxy.repair_connection()) for _ in range(3)]
        await entered.wait(); await asyncio.sleep(0)
        gate.set()
        assert all(await asyncio.gather(*jobs)) and calls == [1]
    finally: await proxy.close()


@pytest.mark.asyncio
async def test_websocket_handshake_repairs_before_accepting_browser(monkeypatch):
    import server.ide_proxy as module
    sent = []; calls = []; received = asyncio.Queue()
    await received.put({'type': 'websocket.connect'})
    await received.put({'type': 'websocket.disconnect', 'code': 1000})
    async def repair(failed_upstream=None):
        assert failed_upstream == 'http://127.0.0.1:12345'
        calls.append('repair')
        proxy.upstream = 'http://127.0.0.1:34567'; proxy.cookie = 'new-secret'
    class Remote:
        subprotocol = None
        def __aiter__(self): return self
        async def __anext__(self): await asyncio.Event().wait()
        async def send(self, data): pass
    class Dial:
        def __init__(self, url, **kwargs):
            self.fail = len(calls) == 0
            calls.append(('dial', url, kwargs['additional_headers']['Cookie']))
        async def __aenter__(self):
            if self.fail:
                proxy.upstream = 'http://127.0.0.1:45678'
                raise OSError('ssh exited')
            return Remote()
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(module, 'connect', Dial)
    proxy = EditorProxy(12345, 'old-secret', 'http://localhost:8766', repair=repair)
    proxy.port = 23456; proxy.browser_keys['browser'] = 1
    async def send(message): sent.append(message)
    ws = WebSocket({'type': 'websocket', 'scheme': 'ws', 'path': '/socket', 'raw_path': b'/socket',
                    'query_string': b'reconnection=true', 'headers': [(b'host', b'localhost:23456'),
                    (b'origin', b'http://localhost:23456'), (b'cookie', (proxy.cookie_name + '=browser').encode())]}, received.get, send)
    try:
        await asyncio.wait_for(proxy.websocket(ws), 1)
        assert [x if isinstance(x, str) else x[0] for x in calls] == ['dial', 'repair', 'dial']
        assert calls[-1][1] == 'ws://127.0.0.1:34567/socket?reconnection=true'
        assert calls[-1][2] == 'new-secret'
        assert sum(x['type'] == 'websocket.accept' for x in sent) == 1
        assert proxy.port == 23456 and proxy.active_sockets == 0
    finally: await proxy.close()


@pytest.mark.asyncio
async def test_failed_repair_is_bounded_sanitized_and_close_cancels(caplog):
    repair = AsyncMock(side_effect=OSError('PRIVATE'))
    proxy = EditorProxy(12345, 'private', 'http://localhost:8766', repair=repair)
    try:
        assert await proxy.repair_connection() is False
        assert 'PRIVATE' not in caplog.text
        assert repair.await_count == 1
    finally: await proxy.close()


@pytest.mark.asyncio
async def test_workbench_uses_decodable_compression_and_readiness_signal():
    import gzip
    from tests.test_ide_assets import Wire
    seen = []
    body = b'<html><head><meta id="vscode-workbench-web-configuration"></head><body></body></html>'
    def remote(request):
        seen.append(request)
        return httpx.Response(200, stream=Wire(gzip.compress(body)), headers={'content-type': 'text/html', 'content-encoding': 'gzip'})
    proxy = EditorProxy(12345, 'private', 'http://localhost:8766', transport=httpx.MockTransport(remote))
    proxy.port = 23456
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as client:
            assert (await client.get('/_ilab_frame_status.js')).status_code == 403
            await client.get('/_ilab_open/' + proxy.ticket())
            response = await client.get('/', headers={'accept-encoding': 'gzip, br, zstd'})
            assert seen[0].headers['accept-encoding'] == 'gzip'
            assert '/_ilab_frame_status.js' in response.text
            assert 'data-parent-origin="http://localhost:8766"' in response.text
            assert 'content-encoding' not in response.headers
            assert 'ilab-editor-ready' in (await client.get('/_ilab_frame_status.js')).text
    finally: await proxy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,retries', [('GET', '/', True), ('GET', '/_static/test.js', True), ('POST', '/', False), ('GET', '/proxy/8000/action', False)])
async def test_http_transport_repair_retries_only_editor_reads(method, path, retries):
    from tests.test_ide_assets import Wire
    seen = []
    def remote(request):
        seen.append(request)
        if len(seen) == 1:
            proxy.upstream = 'http://127.0.0.1:45678'
            raise httpx.ConnectError('private transport failure')
        return httpx.Response(200, stream=Wire(b'editor'))
    async def repair(failed_upstream=None):
        assert failed_upstream == 'http://127.0.0.1:12345'
        proxy.upstream = 'http://127.0.0.1:34567'; proxy.cookie = 'new'
    callback = AsyncMock(side_effect=repair)
    proxy = EditorProxy(12345, 'old', 'http://localhost:8766', transport=httpx.MockTransport(remote), repair=callback)
    proxy.port = 23456
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as client:
            await client.get('/_ilab_open/' + proxy.ticket())
            response = await client.request(method, path)
            assert response.status_code == (200 if retries else 502)
            assert len(seen) == (2 if retries else 1)
            if retries:
                assert seen[-1].url.port == 34567 and seen[-1].headers['cookie'] == 'new'
                callback.assert_awaited_once()
    finally: await proxy.close()
