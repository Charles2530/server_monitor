import asyncio
from types import SimpleNamespace

import httpx
import pytest

from server.acp_actions import ActionError
from server.cci_ide import CCIEditor
from server.ide_proxy import EditorProxy
from server.sites import MAOSHANWANG


DATA = {'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main'}
ORIGIN = 'http://localhost:8766'


def existing_editor(monkeypatch, health=(True,), dead=False):
    import server.cci_ide as module
    editor = CCIEditor(MAOSHANWANG, None, None)
    calls = []
    states = iter(health)
    proxy = EditorProxy(12345, 'remote=old', ORIGIN,
                        transport=httpx.MockTransport(lambda request: httpx.Response(200 if next(states) else 503)))
    proxy.port = 23456
    proxy.browser_keys['existing-browser'] = 1
    class Forward:
        alias = 'test-jump'
        remote_port = 45678
        process = SimpleNamespace(returncode=1 if dead else None)
        async def close(self): calls.append('old-forward-closed')
    session = {'proxy': proxy, 'forward': Forward(), 'address': '10.0.0.1',
               'password': 'p' * 48, 'name': 'charles', 'codex': {}}
    editor.sessions[(*DATA.values(), ORIGIN)] = session
    async def resolve(data): return {'name': 'charles'}, '10.0.0.1'
    async def forbidden(*args, **kwargs): raise AssertionError('Recovery must not bootstrap')
    editor.resolve = resolve
    monkeypatch.setattr(module, 'run_python', forbidden)
    return editor, session, calls


@pytest.mark.asyncio
async def test_open_transient_health_failure_keeps_existing_proxy(monkeypatch):
    editor, session, calls = existing_editor(monkeypatch, (False, True))
    try:
        result = await editor.open(DATA, ORIGIN)
        assert result['port'] == 23456
        assert editor.sessions[(*DATA.values(), ORIGIN)] is session
        assert 'existing-browser' in session['proxy'].browser_keys
        assert not calls
    finally: await editor.close()


@pytest.mark.asyncio
async def test_recover_healthy_session_needs_no_cloud_and_touches_idle_lease(monkeypatch):
    editor, session, calls = existing_editor(monkeypatch)
    async def forbidden(*args): raise AssertionError('Healthy heartbeat must not query cloud')
    editor.resolve = forbidden
    session['proxy'].touched = -100
    try:
        result = await editor.recover(DATA, ORIGIN)
        assert result['port'] == 23456 and result['task_id'] == DATA['task_id']
        assert session['proxy'].touched > -100
        assert 'ticket' not in result
        assert not calls
        with pytest.raises(ActionError): await editor.recover(DATA, 'http://localhost:9000')
    finally: await editor.close()


@pytest.mark.asyncio
async def test_recover_replaces_dead_ssh_without_changing_browser_origin(monkeypatch):
    import server.cci_ide as module
    editor, session, calls = existing_editor(monkeypatch, dead=True)
    original_proxy = session['proxy']
    class Forward:
        def __init__(self, alias, address, port):
            assert (alias, address, port) == ('test-jump', '10.0.0.1', 45678)
            self.port = 34567
        async def start(self): calls.append('new-forward-started')
        async def close(self): calls.append('new-forward-closed')
    async def authenticate(port, password):
        assert port == 34567 and password == 'p' * 48
        return 'remote=new'
    monkeypatch.setattr(module, 'SSHForward', Forward)
    monkeypatch.setattr(editor, 'authenticate', authenticate, raising=False)
    try:
        result = await editor.recover(DATA, ORIGIN)
        assert result['port'] == 23456
        assert session['proxy'] is original_proxy
        assert original_proxy.upstream == 'http://127.0.0.1:34567'
        assert original_proxy.cookie == 'remote=new'
        assert original_proxy.browser_keys == {'existing-browser': 1}
        assert calls == ['new-forward-started', 'old-forward-closed']
    finally: await editor.close()


@pytest.mark.asyncio
async def test_failed_repair_preserves_proxy_cookie_and_existing_transport(monkeypatch):
    import server.cci_ide as module
    editor, session, calls = existing_editor(monkeypatch, dead=True)
    class Forward:
        def __init__(self, *args): self.port = 34567
        async def start(self): raise ActionError('test SSH failure')
        async def close(self): calls.append('failed-forward-closed')
    monkeypatch.setattr(module, 'SSHForward', Forward)
    try:
        with pytest.raises(ActionError, match='test SSH failure'):
            await editor.recover(DATA, ORIGIN)
        assert editor.sessions[(*DATA.values(), ORIGIN)] is session
        assert session['proxy'].upstream == 'http://127.0.0.1:12345'
        assert session['proxy'].cookie == 'remote=old'
        assert calls == ['failed-forward-closed']
    finally: await editor.close()


@pytest.mark.asyncio
async def test_recover_request_uses_async_status_and_deduplicates(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch)
    gate = asyncio.Event()
    async def recover(*args, **kwargs): await gate.wait(); return {'port': 23456}
    editor.recover = recover
    try:
        first = await editor.begin('recover', DATA, ORIGIN)
        assert await editor.begin('recover', DATA, ORIGIN) == first
        assert (await editor.status(first['request_id'], ORIGIN))['pending']
        gate.set()
        await editor.requests[first['request_id']]['task']
        assert (await editor.status(first['request_id'], ORIGIN))['result']['port'] == 23456
    finally: await editor.close()


@pytest.mark.asyncio
async def test_proxy_failure_logs_only_phase_and_exception_class(monkeypatch, caplog):
    import server.ide_proxy as module
    from starlette.websockets import WebSocket
    proxy = EditorProxy(12345, 'remote=PRIVATE', ORIGIN)
    proxy.port = 23456
    proxy.browser_keys['browser'] = 1
    sent = []
    async def send(value): sent.append(value)
    async def receive(): return {'type': 'websocket.connect'}
    ws = WebSocket({'type': 'websocket', 'scheme': 'ws', 'path': '/PRIVATE',
                    'raw_path': b'/PRIVATE', 'query_string': b'token=PRIVATE',
                    'headers': [(b'host', b'localhost:23456'), (b'origin', b'http://localhost:23456'),
                                (b'cookie', (proxy.cookie_name + '=browser').encode())]}, receive, send)
    def fail(*args, **kwargs): raise OSError('PRIVATE exception details')
    monkeypatch.setattr(module, 'connect', fail)
    try:
        await proxy.websocket(ws)
        assert sent[-1]['code'] == 1011
        assert 'Editor WebSocket connect failed (OSError)' in caplog.text
        assert 'PRIVATE' not in caplog.text
        assert proxy.active_sockets == 0
    finally: await proxy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,has_cookie,success', [(302, True, True), (302, False, False), (403, True, False)])
async def test_editor_authentication_validates_redirect_and_private_cookie(monkeypatch, status, has_cookie, success):
    import server.cci_ide as module
    original_client = httpx.AsyncClient
    seen = []
    def login(request):
        seen.append(request)
        return httpx.Response(status, headers={'set-cookie': 'editor=PRIVATE; Path=/'} if has_cookie else {})
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(login), **kwargs))
    editor = CCIEditor(MAOSHANWANG, None, None)
    if success: assert await editor.authenticate(12345, 'password') == 'editor=PRIVATE'
    else:
        with pytest.raises(ActionError): await editor.authenticate(12345, 'password')
    assert str(seen[0].url) == 'http://127.0.0.1:12345/login'
    assert seen[0].method == 'POST'


def test_recovery_remains_unavailable_from_mobile_gateway():
    from server.mobile_gateway import allowed_path
    assert not allowed_path('api/cci/ide/recover', 'POST')


@pytest.mark.asyncio
@pytest.mark.parametrize('removed', [False, True])
async def test_unhealthy_recovery_revalidates_target_before_reusing_private_address(monkeypatch, removed):
    import server.cci_ide as module
    editor, session, calls = existing_editor(monkeypatch, dead=True)
    async def resolve(data):
        if removed: raise ActionError('所选 CCI 容器已不可用')
        return {'name': 'charles'}, '10.0.0.2'
    def forbidden(*args): raise AssertionError('A stale target must not establish a forward')
    editor.resolve = resolve
    monkeypatch.setattr(module, 'SSHForward', forbidden)
    try:
        with pytest.raises(ActionError): await editor.recover(DATA, ORIGIN)
        assert editor.sessions[(*DATA.values(), ORIGIN)] is session
        assert session['proxy'].upstream == 'http://127.0.0.1:12345'
        assert not calls
    finally: await editor.close()
