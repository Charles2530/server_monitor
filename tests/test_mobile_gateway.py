import httpx
from fastapi.testclient import TestClient
from server.mobile_access import DeviceStore
from server.mobile_gateway import create_gateway


def setup(tmp_path,write=False):
    store=DeviceStore(tmp_path/'db')
    device=store.redeem(store.issue_pairing('https://mac.tail.ts.net')['ticket'],'phone')
    store.approve(device['device_id'],write=write)
    calls=[]
    async def upstream(request):
        calls.append(request)
        assert request.url.host=='127.0.0.1' and request.url.port==8766
        assert 'authorization' not in request.headers and 'cookie' not in request.headers and 'origin' not in request.headers
        return httpx.Response(200,json={'site':request.headers.get('x-monitor-site')},headers={'set-cookie':'bad=bad'})
    app=create_gateway(store,transport=httpx.MockTransport(upstream))
    return store,device,calls,TestClient(app)


def test_auth_revocation_and_site_forwarding(tmp_path):
    store,device,calls,client=setup(tmp_path)
    with client:
        assert client.get('/api/state').status_code==401
        headers={'Authorization':'Bearer '+device['token'],'X-Monitor-Site':'sensecore','Cookie':'secret','Origin':'https://evil.test'}
        response=client.get('/api/state',headers=headers)
        assert response.status_code==200 and response.json()['site']=='sensecore'
        assert response.headers['cache-control']=='no-store' and 'set-cookie' not in response.headers
        store.revoke(device['device_id'])
        assert client.get('/api/state',headers=headers).status_code==401
    assert len(calls)==1


def test_readonly_write_allowlist_and_no_arbitrary_proxy(tmp_path):
    _,device,calls,client=setup(tmp_path)
    h={'Authorization':'Bearer '+device['token']}
    with client:
        assert client.post('/api/acp/stop',headers=h,json={}).status_code==403
        for path in ('/api/private','/static/..%2f.env','/.env','/https://evil.test','/api/state/../connect'):
            assert client.get(path,headers=h).status_code in (400,404,405)
        assert client.get('/api/state',headers={**h,'X-Monitor-Site':'evil'}).status_code==400
    assert not calls


def test_post_forwarded_once_without_credentials(tmp_path):
    _,device,calls,client=setup(tmp_path,write=True)
    with client:
        r=client.post('/api/acp/stop',headers={'Authorization':'Bearer '+device['token']},json={'request_id':'same'})
        assert r.status_code==200
    assert len(calls)==1 and calls[0].headers['x-monitor-request']=='1'
    assert calls[0].content==b'{"request_id":"same"}'


def test_pairing_pending_and_replay(tmp_path):
    store=DeviceStore(tmp_path/'db');ticket=store.issue_pairing('https://mac.tail.ts.net')
    with TestClient(create_gateway(store)) as client:
        response=client.post('/mobile/pair',json={'ticket':ticket['ticket'],'name':'phone'})
        assert response.status_code==200;token=response.json()['token']
        assert client.post('/mobile/pair',json={'ticket':ticket['ticket'],'name':'phone'}).status_code==403
        h={'Authorization':'Bearer '+token}
        assert client.get('/mobile/status',headers=h).json()['state']=='pending'
        assert client.get('/api/state',headers=h).status_code==403
        assert client.post('/mobile/pair',content='x'*140000).status_code==413


def test_upstream_failure_is_sanitized_no_retry(tmp_path):
    store=DeviceStore(tmp_path/'db');device=store.redeem(store.issue_pairing('https://mac.tail.ts.net')['ticket'],'phone')
    store.approve(device['device_id'],write=True);calls=[]
    async def broken(request):
        calls.append(request);raise httpx.ReadTimeout('PRIVATE_TOKEN')
    with TestClient(create_gateway(store,transport=httpx.MockTransport(broken))) as client:
        response=client.post('/api/acp/create',headers={'Authorization':'Bearer '+device['token']},json={})
        assert response.status_code==503 and 'PRIVATE' not in response.text and '未知' in response.text
    assert len(calls)==1
