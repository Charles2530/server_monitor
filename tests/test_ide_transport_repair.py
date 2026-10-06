import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.websockets import WebSocket

from server.ide_proxy import EditorProxy


class ProbeClock:
    """Drive real relay/probe tasks without depending on wall-clock scheduling."""
    def __init__(self):
        self.now = 100.0
        self.waiting = asyncio.Queue()
        self.expire = asyncio.Queue()

    def __getattr__(self, name): return getattr(asyncio, name)

    async def sleep(self, delay):
        await asyncio.sleep(0 if delay == 10 else delay)

    async def wait_for(self, future, timeout):
        if timeout != 8: return await asyncio.wait_for(future, timeout)
        self.waiting.put_nowait(None)
        try: await self.expire.get()
        finally: future.cancel()  # Cancel only the shield, as wait_for does.
        raise asyncio.TimeoutError


@asynccontextmanager
async def live_relay(monkeypatch, *, repair=None, transport=None):
    import server.ide_proxy as module
    received = asyncio.Queue(); forwarded = asyncio.Queue(); upstream = asyncio.Queue()
    pong = asyncio.Future(); probes = []; sent_to_remote = []
    await received.put({'type': 'websocket.connect'})
    class Remote:
        subprotocol = None
        async def ping(self): probes.append(1); return pong
        def __aiter__(self): return self
        async def __anext__(self): return await upstream.get()
        async def send(self, data): sent_to_remote.append(data)
    class Dial:
        async def __aenter__(self): return Remote()
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(module, 'connect', lambda *args, **kwargs: Dial())
    proxy = EditorProxy(12345, 'private', 'http://localhost:8766', repair=repair, transport=transport)
    proxy.port = 23456; proxy.browser_keys['browser'] = 1
    async def send(message): await forwarded.put(message)
    ws = WebSocket({'type': 'websocket', 'scheme': 'ws', 'path': '/socket', 'raw_path': b'/socket',
                    'query_string': b'', 'headers': [(b'host', b'localhost:23456'),
                    (b'origin', b'http://localhost:23456'), (b'cookie', (proxy.cookie_name + '=browser').encode())]}, received.get, send)
    relay = asyncio.create_task(proxy.websocket(ws))
    try:
        assert (await asyncio.wait_for(forwarded.get(), .3))['type'] == 'websocket.accept'
        yield SimpleNamespace(proxy=proxy, upstream=upstream, forwarded=forwarded, received=received,
                              pong=pong, probes=probes, relay=relay, sent_to_remote=sent_to_remote)
    finally:
        relay.cancel(); await asyncio.gather(relay, return_exceptions=True)
        await proxy.close()


@pytest.mark.asyncio
async def test_upstream_messages_keep_pending_probe_alive_but_silence_still_repairs(monkeypatch):
    import server.ide_proxy as module
    clock = ProbeClock(); repairs = []
    monkeypatch.setattr(module, 'asyncio', clock)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    async def repair(upstream): repairs.append(upstream)
    async with live_relay(monkeypatch, repair=repair) as live:
        await asyncio.wait_for(clock.waiting.get(), .3)
        for message in (b'first output', 'text output', b'more output'):
            clock.now += 7
            await live.upstream.put(message)
            assert (await asyncio.wait_for(live.forwarded.get(), .3))['type'] == 'websocket.send'
            clock.now += 1
            clock.expire.put_nowait(None)
            await asyncio.wait_for(clock.waiting.get(), .3)
            assert repairs == [], 'received upstream data must prevent a false repair while pong is delayed'
            assert live.probes == [1] and not live.pong.done()
        clock.now += 8
        clock.expire.put_nowait(None)
        await asyncio.wait_for(clock.waiting.get(), .3)
        assert repairs == [live.proxy.upstream]
        assert live.probes == [1] and not live.pong.done()
    assert live.pong.cancelled()


@pytest.mark.asyncio
async def test_old_upstream_messages_do_not_keep_replacement_transport_alive(monkeypatch):
    import server.ide_proxy as module
    clock = ProbeClock(); repairs = []
    monkeypatch.setattr(module, 'asyncio', clock)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    async def repair(upstream): repairs.append(upstream)
    async with live_relay(monkeypatch) as live:
        old_upstream = live.proxy.upstream
        live.proxy.upstream = 'http://127.0.0.1:34567'; live.proxy.repair = repair
        pong = asyncio.Future()
        async def ping(): return pong
        watcher = asyncio.create_task(live.proxy.watch_transport(SimpleNamespace(ping=ping), live.proxy.upstream))
        try:
            await asyncio.wait_for(clock.waiting.get(), .3)
            clock.now += 7
            await live.upstream.put(b'late old-generation output')
            await asyncio.wait_for(live.forwarded.get(), .3)
            clock.now += 1; clock.expire.put_nowait(None)
            await asyncio.wait_for(clock.waiting.get(), .3)
            assert repairs == [live.proxy.upstream] and repairs != [old_upstream]
        finally:
            watcher.cancel(); await asyncio.gather(watcher, return_exceptions=True)
        assert pong.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [httpx.ReadTimeout, httpx.ConnectError, OSError])
async def test_http_health_exception_uses_only_recent_current_upstream_data(monkeypatch, failure):
    import server.ide_proxy as module
    now = [100.0]; requests = []
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    async def unavailable(request): requests.append(request); raise failure('private')
    async with live_relay(monkeypatch, transport=httpx.MockTransport(unavailable)) as live:
        assert await live.proxy.healthy() is False
        await live.upstream.put(b'confirmed output'); await asyncio.wait_for(live.forwarded.get(), .3)
        assert await live.proxy.healthy() is True
        assert len(requests) == 2, 'fresh websocket activity must not bypass the HTTP health check'
        now[0] += 8
        assert await live.proxy.healthy() is False
        await live.upstream.put(b'new output'); await asyncio.wait_for(live.forwarded.get(), .3)
        assert await live.proxy.healthy() is True
        live.proxy.upstream = 'http://127.0.0.1:34567'
        await live.upstream.put(b'late old output'); await asyncio.wait_for(live.forwarded.get(), .3)
        assert await live.proxy.healthy() is False


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 403, 500, 302])
async def test_fresh_upstream_data_never_overrides_http_health_rejection(monkeypatch, status):
    async def rejected(request): return httpx.Response(status, headers={'location': '/login'} if status == 302 else {})
    async with live_relay(monkeypatch, transport=httpx.MockTransport(rejected)) as live:
        await live.upstream.put(b'confirmed output'); await asyncio.wait_for(live.forwarded.get(), .3)
        assert await live.proxy.healthy() is False


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected', [(200, True), (401, False), (403, False), (500, False), (302, False)])
@pytest.mark.parametrize('broken_close', [False, True])
async def test_health_uses_headers_without_reading_body_and_closes_response(monkeypatch, status, expected, broken_close):
    class BrokenBody(httpx.AsyncByteStream):
        reads = 0
        closes = 0
        async def __aiter__(self):
            self.reads += 1
            raise httpx.ReadError('body failed after response headers')
            yield b''
        async def aclose(self):
            self.closes += 1
            if broken_close: raise httpx.ReadError('close failed after response headers')
    body = BrokenBody()
    async def response(request):
        return httpx.Response(status, stream=body, headers={'location': '/login'} if status == 302 else {})
    async with live_relay(monkeypatch, transport=httpx.MockTransport(response)) as live:
        await live.upstream.put(b'confirmed output'); await asyncio.wait_for(live.forwarded.get(), .3)
        assert await live.proxy.healthy() is expected
        assert body.reads == 0, 'HTTP status is enough; probing must not download the editor HTML body'
        assert body.closes == 1, 'the response must be released even though its body was not consumed'


@pytest.mark.asyncio
@pytest.mark.parametrize('replace_before_pong', [False, True])
async def test_only_confirmed_current_upstream_pong_counts_as_health(monkeypatch, replace_before_pong):
    import server.ide_proxy as module
    clock = ProbeClock()
    monkeypatch.setattr(module, 'asyncio', clock)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    async def unavailable(request): raise httpx.ReadTimeout('private')
    async with live_relay(monkeypatch, repair=AsyncMock(), transport=httpx.MockTransport(unavailable)) as live:
        await asyncio.wait_for(clock.waiting.get(), .3)
        assert await live.proxy.healthy() is False, 'sending a ping is not confirmed upstream activity'
        if replace_before_pong: live.proxy.upstream = 'http://127.0.0.1:34567'
        live.pong.set_result(.01)
        for _ in range(5): await asyncio.sleep(0)
        assert await live.proxy.healthy() is (not replace_before_pong)
        clock.now += 8
        assert await live.proxy.healthy() is False


@pytest.mark.asyncio
async def test_client_output_alone_does_not_mask_http_health_failure(monkeypatch):
    async def unavailable(request): raise httpx.ReadTimeout('private')
    async with live_relay(monkeypatch, transport=httpx.MockTransport(unavailable)) as live:
        await live.received.put({'type': 'websocket.receive', 'bytes': b'client activity'})
        for _ in range(5): await asyncio.sleep(0)
        assert live.sent_to_remote == [b'client activity']
        assert await live.proxy.healthy() is False


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
