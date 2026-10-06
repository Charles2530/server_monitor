"""Stable editor origins use local sockets and temporary registries only."""
import asyncio
import fcntl
import json
import socket
import sys
import threading
from pathlib import Path

import httpx
import pytest

from server.acp_actions import ActionError
from server.ide_proxy import EditorProxy


IDENTITY = ('maoshanwang', 'CCI:task', 'pod', 'main', 'http://127.0.0.1:8766', '10.0.0.1')


def registry(path):
    from server.ide_origins import EditorOriginRegistry
    return EditorOriginRegistry(path)


@pytest.mark.asyncio
async def test_registry_reopens_exact_port_and_never_shares_across_identity_fields(tmp_path):
    path = tmp_path / 'origins.json'
    first = await registry(path).bind(IDENTITY)
    port = first.getsockname()[1]
    assert first.getsockname()[0] == '127.0.0.1'
    first.close()
    reopened = await registry(path).bind(IDENTITY)
    assert reopened.getsockname()[1] == port
    reopened.close()
    ports = {port}
    for index, value in enumerate(('sensecore', 'CCI:other', 'pod-other', 'sidecar', 'http://localhost:8766', '10.0.0.2')):
        identity = list(IDENTITY); identity[index] = value
        sock = await registry(path).bind(identity)
        try:
            assert sock.getsockname()[1] not in ports
            ports.add(sock.getsockname()[1])
        finally:
            sock.close()
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_occupied_origin_fails_without_changing_history(tmp_path):
    path = tmp_path / 'origins.json'
    first = await registry(path).bind(IDENTITY)
    before = path.read_bytes()
    try:
        with pytest.raises(ActionError, match='固定编辑器端口.*占用'):
            await registry(path).bind(IDENTITY)
        assert path.read_bytes() == before
    finally:
        first.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('contents', ['not json', '{}', '{"version":1,"origins":{"bad":12345}}'])
async def test_corrupt_registry_is_not_replaced(tmp_path, contents):
    path = tmp_path / 'origins.json'; path.write_text(contents)
    with pytest.raises(ActionError, match='注册表'):
        await registry(path).bind(IDENTITY)
    assert path.read_text() == contents


@pytest.mark.asyncio
async def test_process_restart_and_concurrent_allocations_share_registry(tmp_path):
    path = tmp_path / 'origins.json'
    script = '''
import asyncio,json,sys
from server.ide_origins import EditorOriginRegistry
async def main():
    sock=await EditorOriginRegistry(sys.argv[1]).bind(json.loads(sys.argv[2]))
    print(sock.getsockname()[1],flush=True)
    sock.close()
asyncio.run(main())
'''
    async def child(identity):
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(path), json.dumps(identity),
                                                       cwd=Path(__file__).resolve().parents[1],
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, stderr.decode()
        return int(stdout)
    # Check implementation availability before spawning subprocesses.
    registry(path)
    other = ('sensecore', *IDENTITY[1:])
    ports = await asyncio.gather(child(IDENTITY), child(other))
    assert len(set(ports)) == 2
    assert await child(IDENTITY) == ports[0]
    assert await child(other) == ports[1]


@pytest.mark.asyncio
async def test_failed_atomic_save_closes_socket_and_preserves_registry(tmp_path, monkeypatch):
    from server import ide_origins
    path = tmp_path / 'origins.json'
    first = await registry(path).bind(IDENTITY); first.close()
    before = path.read_bytes()
    sockets = []
    original_socket = socket.socket
    def track(*args, **kwargs):
        sock = original_socket(*args, **kwargs); sockets.append(sock); return sock
    def fail(*args):
        raise OSError('fixture disk failure')
    monkeypatch.setattr(ide_origins.socket, 'socket', track)
    monkeypatch.setattr(ide_origins.os, 'replace', fail)
    with pytest.raises(ActionError):
        await registry(path).bind(('sensecore', *IDENTITY[1:]))
    assert sockets and all(sock.fileno() == -1 for sock in sockets)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.asyncio
async def test_cancelled_registry_read_drains_worker_and_closes_returned_socket(tmp_path, monkeypatch):
    obj = registry(tmp_path / 'origins.json')
    entered, release = threading.Event(), threading.Event()
    sockets = []
    original = obj.bind_socket
    def blocked(identity):
        sock = original(identity); sockets.append(sock); entered.set()
        if not release.wait(2):
            sock.close()
            raise RuntimeError('fixture release missing')
        return sock
    monkeypatch.setattr(obj, 'bind_socket', blocked)
    task = asyncio.create_task(obj.bind(IDENTITY))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel(); await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert sockets and sockets[0].fileno() == -1


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_real_registry_lock_times_out_and_repeated_cancellation_finishes(tmp_path, monkeypatch, cancel):
    from server import ide_origins
    path = tmp_path / 'origins.json'; obj = registry(path)
    monkeypatch.setattr(ide_origins, 'LOCK_TIMEOUT_SECONDS', .05, raising=False)
    entered = threading.Event()
    original = obj.bind_socket
    def bind(identity):
        entered.set()
        return original(identity)
    monkeypatch.setattr(obj, 'bind_socket', bind)
    with Path(str(path) + '.lock').open('a+b') as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        task = asyncio.create_task(obj.bind(IDENTITY))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            if cancel:
                task.cancel(); await asyncio.sleep(0); task.cancel()
            # The external lock stays held throughout: completion cannot depend
            # on its release, even after the caller has been cancelled twice.
            done, _ = await asyncio.wait({task}, timeout=.5)
            assert task in done, 'Registry lock wait outlived its deadline'
            if cancel:
                with pytest.raises(asyncio.CancelledError): await task
            else:
                with pytest.raises(ActionError) as error: await task
                assert error.value.code == 'ide_origin_locked'
            assert not path.exists()
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            if isinstance(result, socket.socket): result.close()


@pytest.mark.asyncio
async def test_reopened_proxy_keeps_origin_but_rotates_cookies_and_tickets(tmp_path):
    path = tmp_path / 'origins.json'
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self): yield b'editor'
    def make():
        return EditorProxy(12345, 'upstream=private', IDENTITY[4], origin_registry=registry(path), origin_identity=IDENTITY,
                           transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body())))
    first = make()
    await first.start()
    origin, cookie_name = first.origin, first.cookie_name
    stale_ticket = first.ticket()
    async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
        assert (await client.get('/_ilab_open/' + first.ticket())).status_code == 303
        old_cookies = dict(client.cookies)
    await first.close()
    second = make()
    try:
        await second.start()
        assert second.origin == origin and second.cookie_name != cookie_name
        async with httpx.AsyncClient(base_url=origin, cookies=old_cookies, trust_env=False) as client:
            assert (await client.get('/')).status_code == 403
            assert (await client.get('/_ilab_open/' + stale_ticket)).status_code == 403
            ticket = second.ticket()
            assert (await client.get('/_ilab_open/' + ticket, headers={'host': 'attacker.test'})).status_code == 403
            assert (await client.get('/_ilab_open/' + ticket)).status_code == 303
            assert (await client.get('/')).text == 'editor'
            assert (await client.get('/', headers={'origin': 'http://evil.test'})).status_code == 403
    finally:
        await first.close(); await second.close()


@pytest.mark.asyncio
async def test_proxy_start_failure_reclaims_reserved_socket(tmp_path, monkeypatch):
    import server.ide_proxy as module
    class BrokenServer:
        def __init__(self, config): self.started = False
        async def serve(self, sockets): raise RuntimeError('fixture startup failure')
    monkeypatch.setattr(module, 'LocalProxyServer', BrokenServer)
    obj = registry(tmp_path / 'origins.json')
    proxy = EditorProxy(12345, 'cookie', IDENTITY[4], origin_registry=obj, origin_identity=IDENTITY)
    with pytest.raises(RuntimeError, match='fixture startup failure'):
        await proxy.start()
    assert proxy.client.is_closed
    sock = await obj.bind(IDENTITY)
    sock.close()


@pytest.mark.asyncio
async def test_cancelled_proxy_start_reclaims_listener_and_server_task(tmp_path, monkeypatch):
    import server.ide_proxy as module
    entered = asyncio.Event()
    class StartingServer:
        def __init__(self, config): self.started = False; self.should_exit = False
        async def serve(self, sockets):
            entered.set()
            while not self.should_exit: await asyncio.sleep(0)
    monkeypatch.setattr(module, 'LocalProxyServer', StartingServer)
    obj = registry(tmp_path / 'origins.json')
    proxy = EditorProxy(12345, 'cookie', IDENTITY[4], origin_registry=obj, origin_identity=IDENTITY)
    opening = asyncio.create_task(proxy.start())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError): await opening
        assert proxy.client.is_closed and proxy.server_task.done() and proxy.listener is None
        sock = await obj.bind(IDENTITY); sock.close()
    finally:
        opening.cancel(); await asyncio.gather(opening, return_exceptions=True); await proxy.close()


@pytest.mark.asyncio
async def test_simultaneous_same_identity_proxies_cannot_share_listener(tmp_path):
    path = tmp_path / 'origins.json'
    proxies = [EditorProxy(12345, 'cookie', IDENTITY[4], origin_registry=registry(path), origin_identity=IDENTITY)
               for _ in range(2)]
    try:
        outcomes = await asyncio.gather(*(proxy.start() for proxy in proxies), return_exceptions=True)
        assert sum(result is None for result in outcomes) == 1
        assert sum(isinstance(result, ActionError) and result.code == 'ide_origin_busy' for result in outcomes) == 1
    finally:
        await asyncio.gather(*(proxy.close() for proxy in proxies))


@pytest.mark.asyncio
async def test_plain_proxy_without_identity_still_uses_ephemeral_loopback():
    proxy = EditorProxy(12345, 'cookie', IDENTITY[4])
    try:
        await proxy.start()
        assert proxy.port and proxy.origin.startswith('http://127.0.0.1:')
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_new_identity_skips_a_free_but_historically_assigned_port(tmp_path, monkeypatch):
    obj = registry(tmp_path / 'origins.json')
    first = await obj.bind(IDENTITY); old_port = first.getsockname()[1]; first.close()
    reserved = obj.socket(old_port)
    original = obj.socket
    calls = []
    def allocate(port):
        calls.append(port)
        return reserved if len(calls) == 1 else original(port)
    monkeypatch.setattr(obj, 'socket', allocate)
    result = await obj.bind(('sensecore', *IDENTITY[1:]))
    try:
        assert result.getsockname()[1] != old_port and len(calls) >= 2
        assert reserved.fileno() == -1
    finally:
        reserved.close(); result.close()


@pytest.mark.asyncio
async def test_duplicate_port_registry_is_rejected(tmp_path):
    path = tmp_path / 'origins.json'; obj = registry(path)
    sock = await obj.bind(IDENTITY); sock.close()
    data = json.loads(path.read_text())
    other = json.dumps(('sensecore', *IDENTITY[1:]), ensure_ascii=True, separators=(',', ':'))
    data['origins'][other] = next(iter(data['origins'].values()))
    path.write_text(json.dumps(data)); before = path.read_bytes()
    with pytest.raises(ActionError, match='注册表'):
        await obj.bind(IDENTITY)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_occupied_proxy_start_closes_client_and_keeps_mapping(tmp_path):
    path = tmp_path / 'origins.json'; obj = registry(path)
    occupied = await obj.bind(IDENTITY)
    before = path.read_bytes()
    proxy = EditorProxy(12345, 'cookie', IDENTITY[4], origin_registry=obj, origin_identity=IDENTITY)
    try:
        with pytest.raises(ActionError, match='固定编辑器端口.*占用'):
            await proxy.start()
        assert proxy.client.is_closed and proxy.listener is None
        assert path.read_bytes() == before
    finally:
        occupied.close(); await proxy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('site_id', ['maoshanwang', 'sensecore'])
async def test_editor_open_passes_full_identity_to_shared_registry(tmp_path, monkeypatch, site_id):
    from tests.test_ide_ssh_warmup import cold_editor
    from tests.test_ide_recovery import DATA, ORIGIN
    from server.sites import SITES
    editor, *_ = cold_editor(monkeypatch)
    editor.site = SITES[site_id]
    editor.origin_registry = registry(tmp_path / 'shared-origins.json')
    try:
        await editor.open(DATA, ORIGIN)
        proxy = editor.sessions[(*DATA.values(), ORIGIN)]['proxy']
        assert proxy.origin_identity == (site_id, DATA['task_id'], DATA['instance'], DATA['container'], ORIGIN, '10.0.0.1')
        assert proxy.origin_registry is editor.origin_registry
    finally:
        await editor.close()


def test_default_registry_is_shared_and_explicit_registry_is_injectable(tmp_path, monkeypatch):
    from dataclasses import replace
    import server.cci_ide as module
    from server.sites import MAOSHANWANG, SENSECORE
    monkeypatch.setattr(module, 'MAOSHANWANG', replace(MAOSHANWANG, data=tmp_path), raising=False)
    first, second = (module.CCIEditor(site, None, None) for site in (MAOSHANWANG, SENSECORE))
    assert first.origin_registry.path == second.origin_registry.path == tmp_path / 'ide-origins.json'
    custom = registry(tmp_path / 'custom.json')
    assert module.CCIEditor(SENSECORE, None, None, origin_registry=custom).origin_registry is custom
    assert not list(tmp_path.iterdir())
