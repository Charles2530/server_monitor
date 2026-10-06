import asyncio
from fastapi.testclient import TestClient
from server.main import create_app
from server.service import Monitor
from server.store import Store
from server.tokenhub_auth import TokenHubError


class Hub:
    loop_task = None
    def __init__(self):
        self.calls = []
    async def snapshot(self, force=False):
        self.calls.append(('snapshot', force))
        return {'status': 'disconnected', 'sections': {}, 'connected': False}
    async def keys(self, page=1):
        self.calls.append(('keys', page))
        return {'items': [], 'page': page}
    async def key_groups(self, key_id):
        return {'key': {'id': key_id}, 'items': []}
    async def update_key_group(self, **kwargs):
        self.calls.append(('update_group', kwargs))
        return {'id': kwargs['key_id'], 'group_id': kwargs['group_id']}
    async def trend(self, days=7):
        return {'days': days, 'trend': []}
    async def usage(self, **kwargs):
        self.calls.append(('usage', kwargs))
        raise TokenHubError('日期无效。', 'invalid_dates', 400)
    def connect(self):
        self.calls.append(('connect',))
        return {'connecting': True}
    async def disconnect(self):
        self.calls.append(('disconnect',))
        return {'status': 'disconnected'}
    async def close(self):
        pass


def client_for(tmp_path, hub):
    monitor = Monitor(Store(tmp_path / 'db'), None)
    return TestClient(create_app(monitor, background=False, tokenhub=hub), base_url='http://localhost:8765')


def test_tokenhub_routes_are_global_redacted_and_validated(tmp_path):
    hub = Hub()
    with client_for(tmp_path, hub) as client:
        assert client.get('/api/tokenhub/state', headers={'X-Monitor-Site': 'sensecore'}).json()['status'] == 'disconnected'
        assert client.get('/api/tokenhub/state', headers={'X-Monitor-Device': 'mobile'}).json()['can_connect'] is False
        assert client.get('/api/tokenhub/keys?page=2').json()['page'] == 2
        assert client.get('/api/tokenhub/keys?page=0').status_code == 422
        assert client.get('/api/tokenhub/trend?days=30').json()['days'] == 30
        assert client.get('/api/tokenhub/trend?days=8').status_code == 422
        assert client.get('/api/tokenhub/usage?start_date=2026-10-07&end_date=2026-10-06').status_code == 400
        assert client.get('/data/tokenhub/session.json').status_code == 404
        assert client.post('/api/tokenhub/connect').status_code == 403
        headers = {'X-Monitor-Request': '1'}
        assert client.post('/api/tokenhub/connect', headers=headers).status_code == 202
        assert client.post('/api/tokenhub/connect', headers={**headers, 'Origin': 'https://evil.test'}).status_code == 403
        assert client.post('/api/tokenhub/disconnect', headers=headers).status_code == 200
        assert client.post('/api/tokenhub/keys', headers=headers).status_code == 405


def test_tokenhub_cannot_open_login_from_mobile_or_leak_exception(tmp_path):
    hub = Hub()
    async def fail(**kwargs):
        raise RuntimeError('PRIVATE SESSION CONTENT')
    hub.snapshot = fail
    with client_for(tmp_path, hub) as client:
        response = client.get('/api/tokenhub/state')
        assert response.status_code == 503 and 'PRIVATE' not in response.text
        response = client.post('/api/tokenhub/connect', headers={'X-Monitor-Request': '1', 'X-Monitor-Device': 'mobile'})
        assert response.status_code == 403
        assert not hub.calls


def test_mobile_allowlist_limits_tokenhub_to_explicit_operations():
    from server.mobile_gateway import allowed_path
    for path in ('/api/tokenhub/state', '/api/tokenhub/keys', '/api/tokenhub/usage', '/api/tokenhub/trend'):
        assert allowed_path(path, 'GET')
    for path in ('/api/tokenhub/connect', '/api/tokenhub/disconnect', '/api/tokenhub/keys', '/api/tokenhub/proxy'):
        assert not allowed_path(path, 'POST')


def test_key_group_routes_validate_body_and_origin(tmp_path):
    hub = Hub()
    body = {'key_id': 1, 'group_id': 3, 'expected_group_id': 2, 'account_id': 7}
    headers = {'X-Monitor-Request': '1'}
    with client_for(tmp_path, hub) as client:
        assert client.get('/api/tokenhub/key-groups?key_id=1').json()['key']['id'] == 1
        assert client.get('/api/tokenhub/key-groups?key_id=0').status_code == 422
        assert client.post('/api/tokenhub/key-group', json=body).status_code == 403
        assert client.post('/api/tokenhub/key-group', headers={**headers, 'Origin': 'https://evil.test'}, json=body).status_code == 403
        for bad in ({**body, 'group_id': True}, {**body, 'key_id': '1'}, {**body, 'group_id': 0}, {**body, 'quota': 100}, {**body, 'account_id': None}):
            assert client.post('/api/tokenhub/key-group', headers=headers, json=bad).status_code == 422
        assert not hub.calls
        result = client.post('/api/tokenhub/key-group', headers={**headers, 'X-Monitor-Device': 'mobile'}, json=body)
        assert result.status_code == 200 and result.json()['group_id'] == 3
        assert hub.calls == [('update_group', body)]
    from server.mobile_gateway import allowed_path
    assert allowed_path('/api/tokenhub/key-groups', 'GET')
    assert allowed_path('/api/tokenhub/key-group', 'POST')
    assert not allowed_path('/api/tokenhub/key-group/1', 'POST')


def test_key_copy_requires_explicit_post_validated_identity_and_no_cache(tmp_path):
    hub = Hub()
    async def copy_key(**kwargs):
        hub.calls.append(('copy', kwargs))
        return {'key': 'sk-test-only'}
    hub.copy_key = copy_key
    headers = {'X-Monitor-Request': '1'}
    body = {'key_id': 1, 'account_id': 7}
    with client_for(tmp_path, hub) as client:
        assert client.get('/api/tokenhub/key-copy').status_code == 405
        assert client.post('/api/tokenhub/key-copy', json=body).status_code == 403
        assert client.post('/api/tokenhub/key-copy', headers={**headers, 'Origin': 'https://evil.test'}, json=body).status_code == 403
        for bad in ({'key_id': True, 'account_id': 7}, {'key_id': -1, 'account_id': 7}, {'key_id': '1', 'account_id': 7}, {'key_id': 1}, {**body, 'url': 'https://evil.test'}):
            assert client.post('/api/tokenhub/key-copy', headers=headers, json=bad).status_code == 422
        assert not hub.calls
        response = client.post('/api/tokenhub/key-copy', headers=headers, json=body)
        assert response.status_code == 200 and response.json() == {'key': 'sk-test-only'}
        assert response.headers['cache-control'] == 'no-store'
        assert hub.calls == [('copy', body)]
    from server.mobile_gateway import allowed_path
    assert allowed_path('/api/tokenhub/key-copy', 'POST')
    assert not allowed_path('/api/tokenhub/key-copy', 'GET')
