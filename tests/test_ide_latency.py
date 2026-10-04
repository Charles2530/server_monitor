"""Health reuse and optional setup must stay off the connection critical path."""
import asyncio
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock

from tests.test_ide_recovery import DATA, ORIGIN, existing_editor


@pytest.mark.asyncio
@pytest.mark.parametrize('health', [True, False])
async def test_proxy_failure_needs_one_health_confirmation_before_repair(monkeypatch, health):
    editor, session, _ = existing_editor(monkeypatch)
    probe = AsyncMock(return_value=health)
    resolve = AsyncMock(return_value=({'name': 'charles'}, session['address']))
    reconnect = AsyncMock(return_value=session)
    monkeypatch.setattr(session['proxy'], 'healthy', probe)
    monkeypatch.setattr(editor, 'resolve', resolve)
    monkeypatch.setattr(editor, 'connect', reconnect)
    try:
        editor.opened((*DATA.values(), ORIGIN), session, DATA, ORIGIN, lambda _: None)
        assert await session['proxy'].repair_connection(session['proxy'].upstream)
        assert probe.await_count == 1
        assert resolve.await_count == reconnect.await_count == (0 if health else 1)
        assert session['proxy'].port == 23456
        assert 'existing-browser' in session['proxy'].browser_keys
    finally: await editor.close()


@pytest.mark.asyncio
async def test_stale_proxy_failure_does_not_shortcut_new_transport_health(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch, health=(False, True))
    reconnect = AsyncMock()
    monkeypatch.setattr(editor, 'connect', reconnect)
    identity = (*DATA.values(), ORIGIN)
    try:
        async with editor.session_lock(identity):
            repair = asyncio.create_task(editor.recover(DATA, ORIGIN, failed_upstream=session['proxy'].upstream))
            await asyncio.sleep(0)
            session['proxy'].upstream = 'http://127.0.0.1:34567'
        assert (await repair)['port'] == 23456
        reconnect.assert_not_awaited()
    finally: await editor.close()


@pytest.mark.asyncio
async def test_completed_bootstrap_cleanup_does_not_delay_connection_and_is_joined():
    import base64, re
    from server.cci_ide import CCIEditor, run_python
    from server.sites import MAOSHANWANG
    release = asyncio.Event(); closing = asyncio.Event()
    class Bridge:
        command = ''
        async def open(self, *args): return {'session_id': 'owned'}
        async def write(self, key, seq, data, *args): self.command += data
        async def read(self, *args):
            payload = base64.b64decode(''.join(re.findall(r"'([A-Za-z0-9+/=]+)'", self.command))).decode()
            tag = re.search(r"chr\(30\)\+'([a-f0-9]+)'", payload).group(1)
            raw = b'\x1e' + tag.encode() + b'{"port":1234}\x1f'
            return {'chunks': [base64.b64encode(raw).decode()], 'cursor': 1, 'closed': False}
        async def disconnect(self, *args): closing.set(); await release.wait()
    editor = CCIEditor(MAOSHANWANG, Bridge(), None)
    try:
        result = await asyncio.wait_for(run_python(editor.bridge, {}, 'RESULT={"port":1234}', cleanup_jobs=editor.cleanup_jobs), .2)
        assert result == {'port': 1234}
        await closing.wait()
        shutdown = asyncio.create_task(editor.close())
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set(); await shutdown
        assert not editor.cleanup_jobs
    finally:
        release.set(); await editor.close()


@pytest.mark.asyncio
async def test_cancelled_bootstrap_waits_for_cleanup_instead_of_detaching_it():
    from server.cci_ide import run_python
    entered = asyncio.Event(); closing = asyncio.Event(); release = asyncio.Event()
    class Bridge:
        async def open(self, *args): return {'session_id': 'owned'}
        async def write(self, *args): pass
        async def read(self, *args): entered.set(); await asyncio.Event().wait()
        async def disconnect(self, *args): closing.set(); await release.wait()
    cleanup = set()
    work = asyncio.create_task(run_python(Bridge(), {}, 'RESULT={}', cleanup_jobs=cleanup))
    try:
        await entered.wait(); work.cancel(); await closing.wait()
        assert not cleanup and not work.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await work
    finally:
        release.set(); await asyncio.gather(work, return_exceptions=True)


@pytest.mark.asyncio
async def test_explicit_open_restarts_stopped_editor_without_replacing_proxy(monkeypatch):
    from server.acp_actions import ActionError
    editor, session, _ = existing_editor(monkeypatch, dead=True)
    proxy = session['proxy']
    connect = AsyncMock(side_effect=[ActionError('old listener gone'), session])
    bootstrap = AsyncMock(return_value={'port': 45679, 'password': 'n' * 48})
    monkeypatch.setattr(editor, 'connect', connect)
    monkeypatch.setattr('server.cci_ide.run_python', bootstrap)
    try:
        result = await editor.open(DATA, ORIGIN)
        assert result['port'] == 23456 and session['proxy'] is proxy
        assert connect.await_count == 2
        assert connect.call_args.args[1:3] == (45679, 'n' * 48)
        assert connect.call_args.args[-1] is session
        assert '"install_optional": false' in bootstrap.call_args.args[2]
        assert 'existing-browser' in proxy.browser_keys
    finally: await editor.close()


@pytest.mark.asyncio
async def test_cancelled_explicit_reconnect_never_starts_bootstrap(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch, dead=True)
    monkeypatch.setattr(editor, 'connect', AsyncMock(side_effect=asyncio.CancelledError))
    bootstrap = AsyncMock()
    monkeypatch.setattr('server.cci_ide.run_python', bootstrap)
    try:
        with pytest.raises(asyncio.CancelledError): await editor.open(DATA, ORIGIN)
        bootstrap.assert_not_awaited()
    finally: await editor.close()


@pytest.mark.asyncio
async def test_healthy_open_does_not_query_cloud(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch)
    session['name'] = 'charles'
    async def forbidden(*args): raise AssertionError('Healthy reuse must not query cloud')
    editor.resolve = forbidden
    try:
        result = await editor.open(DATA, ORIGIN)
        assert result['port'] == 23456 and result['name'] == 'charles'
    finally: await editor.close()


@pytest.mark.asyncio
async def test_unconfigured_codex_never_triggers_bootstrap(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch, health=(True, True))
    session['codex'] = {'configured': False, 'installed': False}
    try:
        for _ in range(2): await editor.open(DATA, ORIGIN)
        await asyncio.sleep(0)
        assert not editor.optional_jobs
    finally: await editor.close()


@pytest.mark.asyncio
async def test_optional_setup_cannot_block_healthy_open_and_is_deduplicated(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch, health=(True, True))
    session['codex'] = {'configured': True, 'installed': False}
    entered = asyncio.Event(); release = asyncio.Event(); calls = []
    async def bootstrap(*args, **kwargs):
        calls.append(1); entered.set(); await release.wait()
        return {'codex': {'configured': True, 'installed': True}, 'appearance': {'installed': True, 'version': '1.0.0'}}
    monkeypatch.setattr('server.cci_ide.run_python', bootstrap)
    try:
        first = await asyncio.wait_for(editor.open(DATA, ORIGIN), .2)
        await entered.wait()
        second = await asyncio.wait_for(editor.open(DATA, ORIGIN), .2)
        assert first['port'] == second['port'] == 23456
        assert first['optional_pending'] is True
        assert calls == [1]
        release.set()
        await asyncio.gather(*editor.optional_jobs.values())
        assert session['codex']['installed'] is True
    finally: await editor.close()


@pytest.mark.asyncio
async def test_optional_failure_has_cooldown_and_close_cancels_jobs(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch, health=(True, True))
    session['codex'] = {'configured': True, 'installed': False}
    calls = []
    async def bootstrap(*args, **kwargs): calls.append(1); raise OSError('private remote failure')
    monkeypatch.setattr('server.cci_ide.run_python', bootstrap)
    try:
        await editor.open(DATA, ORIGIN)
        await asyncio.gather(*editor.optional_jobs.values())
        await editor.open(DATA, ORIGIN)
        assert calls == [1]
    finally: await editor.close()


def test_connection_bootstrap_can_skip_optional_installation():
    from server.ide_bootstrap import bootstrap_script
    script = bootstrap_script('pod', '10.0.0.1', install_optional=False)
    compile(script, 'bootstrap', 'exec')
    assert '"install_optional": false' in script
    bundle_line = script.split('\nAPPEARANCE_BUNDLE = ', 1)[1].split('\n', 1)[0]
    assert "'payload':" not in bundle_line
    assert len(script.encode()) < 20000


def test_codex_inspection_does_not_install_missing_plugin(tmp_path, monkeypatch):
    from server.ide_codex import prepare_codex
    home = tmp_path / 'home'; (home / '.codex').mkdir(parents=True)
    (home / '.codex/config.toml').write_text('model="test"')
    def forbidden(*args, **kwargs): raise AssertionError('Optional install is outside the connection path')
    monkeypatch.setattr('server.ide_codex.install_codex', forbidden)
    env = {'HOME': str(home)}
    result = prepare_codex(tmp_path, env, tmp_path / 'binary', install=False)
    assert result == {'configured': True, 'installed': False}
    assert env['CODEX_HOME'] == str(home / '.codex')


@pytest.mark.asyncio
async def test_target_selection_and_open_share_one_recent_verification():
    from server.cci_ide import CCIEditor
    from server.sites import MAOSHANWANG
    result = {'site': MAOSHANWANG.id, 'task_id': DATA['task_id'], 'name': 'charles',
              'targets': [{'instance': DATA['instance'], 'container': DATA['container'], 'address': '10.0.0.1'}]}
    resolver = SimpleNamespace(targets=AsyncMock(return_value=result))
    editor = CCIEditor(MAOSHANWANG, SimpleNamespace(renew_once=AsyncMock()), resolver)
    public = await editor.targets(DATA['task_id'])
    assert 'address' not in public['targets'][0]
    resolved, address = await editor.resolve(DATA)
    assert address == '10.0.0.1' and resolver.targets.await_count == 1
    assert resolved['targets'][0]['address'] == '10.0.0.1'


@pytest.mark.asyncio
@pytest.mark.parametrize('expired,wrong_site', [(False, False), (True, False), (False, True)])
async def test_bootstrap_can_reuse_only_a_fresh_site_scoped_verification(expired, wrong_site):
    from server.cci_terminal import VerifiedCCITargets
    from server.terminal_bridge import TerminalBridge
    from server.sites import MAOSHANWANG, SENSECORE
    from tests.test_terminal_bridge import FakeSocket
    import time
    targets = {'site': MAOSHANWANG.id, 'task_id': DATA['task_id'], 'name': 'charles',
               'targets': [{'instance': DATA['instance'], 'container': DATA['container']}]}
    lease = VerifiedCCITargets(SENSECORE if wrong_site else MAOSHANWANG, targets)
    if expired: lease.created = time.monotonic() - 60
    resolver = SimpleNamespace(targets=AsyncMock(return_value=targets))
    bridge = TerminalBridge(MAOSHANWANG, resolver, None, dial=AsyncMock(return_value=FakeSocket()),
                            session_loader=lambda: {'headers': {'authorization': 'test'}})
    try:
        await bridge.open({**DATA, 'cols': 80, 'rows': 24}, 'cci-editor-bootstrap', verified_targets=lease)
        assert resolver.targets.await_count == int(expired or wrong_site)
    finally: await bridge.close()
