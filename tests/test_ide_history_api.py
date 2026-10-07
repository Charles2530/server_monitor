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
