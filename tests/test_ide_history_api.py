import pytest


@pytest.mark.asyncio
async def test_container_history_is_scoped_and_cached(monkeypatch):
    import server.cci_ide as module
    from server.cci_ide import CCIEditor
    from server.sites import MAOSHANWANG

    editor = CCIEditor(MAOSHANWANG, object(), object())
    data = {'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main'}
    origin = 'http://localhost:8766'
    identity = (data['task_id'], data['instance'], data['container'], origin)
    class Closeable:
        async def close(self): pass
    editor.sessions[identity] = {'workspace': '/workspace/project', 'proxy': Closeable(), 'forward': Closeable()}
    calls = []

    async def fake_run_python(*args, **kwargs):
        calls.append(kwargs.get('timeout'))
        return {'records': [
            {'thread_id': 'root', 'display_title': 'Root', 'updated_at': 2, 'cwd': '/workspace/project'},
            {'thread_id': 'child', 'display_title': 'Child', 'updated_at': 1, 'cwd': '/workspace/project/src'},
            {'thread_id': 'other', 'display_title': 'Other', 'updated_at': 3, 'cwd': '/workspace/other'},
        ]}

    monkeypatch.setattr(module, 'run_python', fake_run_python)
    first = await editor.history(data, origin)
    second = await editor.history(data, origin)
    assert [row['thread_id'] for row in first['records']] == ['root', 'child']
    assert first['workspace'] == '/workspace/project'
    assert first['source'] == 'container'
    assert second == first
    assert calls == [45]
    await editor.close()


@pytest.mark.asyncio
async def test_container_history_failure_is_redacted(monkeypatch):
    import server.cci_ide as module
    from server.cci_ide import CCIEditor
    from server.sites import MAOSHANWANG
    from server.acp_actions import ActionError

    editor = CCIEditor(MAOSHANWANG, object(), object())
    data = {'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main'}
    origin = 'http://localhost:8766'
    class Closeable:
        async def close(self): pass
    editor.sessions[(data['task_id'], data['instance'], data['container'], origin)] = {'workspace': '/workspace/project', 'proxy': Closeable(), 'forward': Closeable()}

    async def fake_run_python(*args, **kwargs):
        return {'error': 'private session contents'}

    monkeypatch.setattr(module, 'run_python', fake_run_python)
    with pytest.raises(ActionError, match='历史暂不可用') as raised:
        await editor.history(data, origin)
    assert raised.value.code == 'history_unavailable'
    assert 'private' not in str(raised.value)
    await editor.close()


def test_history_route_has_same_local_and_payload_guards(tmp_path):
    from fastapi.testclient import TestClient
    from server.main import create_app
    from server.service import Monitor
    from server.store import Store

    app = create_app(Monitor(Store(tmp_path / 'db'), None), background=False)
    with TestClient(app, base_url='http://localhost:8766') as client:
        headers = {'X-Monitor-Request': '1'}
        assert client.post('/api/cci/ide/history', json={'task_id': 'CCI:job'}).status_code == 403
        assert client.post('/api/cci/ide/history', json={'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main', 'extra': 1}, headers=headers).status_code == 400


@pytest.mark.asyncio
async def test_parallel_history_reads_share_container_work_and_cache_workspaces(monkeypatch):
    import asyncio
    import server.cci_ide as module
    from server.sites import MAOSHANWANG
    editor = module.CCIEditor(MAOSHANWANG, object(), object())
    data = {'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main'}
    origin = 'http://localhost:8766'
    class Closeable:
        async def close(self): pass
    editor.sessions[('CCI:job', 'pod', 'main', origin)] = {
        'workspace': '/workspace/project', 'proxy': Closeable(), 'forward': Closeable()}
    calls = []
    async def run(*args, **kwargs):
        calls.append(args[1].get('workspace'))
        await asyncio.sleep(.01)
        return {'records': []}
    monkeypatch.setattr(module, 'run_python', run)
    try:
        a = {**data, 'workspace': '/workspace/project'}
        b = {**data, 'workspace': '/workspace/other'}
        results = await asyncio.gather(*(editor.history(a, origin) for _ in range(6)))
        assert calls == ['/workspace/project']
        assert all(r == results[0] for r in results)
        await editor.history(b, origin)
        await editor.history(a, origin)
        assert calls == ['/workspace/project', '/workspace/other']
        assert not editor.locks
    finally:
        await editor.close()


@pytest.mark.asyncio
async def test_history_cache_expires_bounds_memory_and_retries_failures(monkeypatch):
    import server.cci_ide as module
    from server.sites import MAOSHANWANG
    editor = module.CCIEditor(MAOSHANWANG, object(), object())
    data = {'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main', 'workspace': '/project'}
    origin = 'http://localhost:8766'
    class Closeable:
        async def close(self): pass
    session = {'workspace': '/project', 'proxy': Closeable(), 'forward': Closeable()}
    editor.sessions[('CCI:job', 'pod', 'main', origin)] = session
    calls = []
    async def run(*args, **kwargs):
        calls.append(args[1]['workspace'])
        return {'records': []}
    monkeypatch.setattr(module, 'run_python', run)
    try:
        await editor.history(data, origin)
        session['history_caches']['/project']['created'] -= 16
        await editor.history(data, origin)
        assert calls == ['/project', '/project']
        for i in range(5):
            await editor.history({**data, 'workspace': f'/other/{i}'}, origin)
        assert len(session['history_caches']) == 4
        assert '/project' not in session['history_caches']
        async def failure(*args, **kwargs): raise RuntimeError('private detail')
        monkeypatch.setattr(module, 'run_python', failure)
        with pytest.raises(module.ActionError, match='历史暂不可用'):
            await editor.history(data, origin)
        assert '/project' not in session['history_caches']
        assert not editor.locks
        monkeypatch.setattr(module, 'run_python', run)
        await editor.history(data, origin)
        assert calls[-1] == '/project'
    finally:
        await editor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('workspace', ['relative/project', '', '/project/\x00', None])
async def test_invalid_requested_workspace_does_not_fall_back(monkeypatch, workspace):
    from tests.test_ide_history_transport import history_editor
    from server.acp_actions import ActionError
    import server.cci_ide as module
    editor, data, origin = history_editor(object())
    async def forbidden(*args, **kwargs):
        pytest.fail('invalid scope must not start a container command')
    monkeypatch.setattr(module, 'run_python', forbidden)
    try:
        with pytest.raises(ActionError) as raised:
            await editor.history({**data, 'workspace': workspace}, origin)
        assert raised.value.code == 'history_unavailable'
    finally:
        await editor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [{}, {'records': {}}, {'records': [], 'workspace': None}, {'records': [], 'workspace': 'relative'}])
async def test_malformed_history_result_is_not_cached_as_empty(monkeypatch, response):
    from tests.test_ide_history_transport import history_editor
    from server.acp_actions import ActionError
    import server.cci_ide as module
    editor, data, origin = history_editor(object())
    async def malformed(*args, **kwargs):
        return response
    monkeypatch.setattr(module, 'run_python', malformed)
    try:
        with pytest.raises(ActionError) as raised:
            await editor.history(data, origin)
        assert raised.value.code == 'history_unavailable'
        assert not editor.sessions[('CCI:job', 'pod', 'main', origin)]['history_caches']
    finally:
        await editor.close()
