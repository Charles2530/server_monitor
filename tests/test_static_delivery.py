"""Static assets may revalidate; account data and revoked devices may not."""
import gzip
import httpx
from fastapi.testclient import TestClient
from server.main import create_app
from server.service import Monitor
from server.store import Store
from server.mobile_gateway import create_gateway
from server.mobile_access import DeviceStore


def test_static_compression_revalidation_and_private_dynamic_responses(tmp_path):
    app=create_app(Monitor(Store(tmp_path/'monitor'),None),background=False)
    with TestClient(app,base_url='http://localhost') as client:
        response=client.get('/static/vendor/echarts.min.js',headers={'Accept-Encoding':'gzip'})
        assert response.status_code==200
        assert response.headers['content-encoding']=='gzip'
        with client.stream('GET','/static/vendor/echarts.min.js',headers={'Accept-Encoding':'gzip'}) as streamed:
            encoded=b''.join(streamed.iter_raw())
        assert gzip.decompress(encoded)==response.content and len(encoded)<len(response.content)*.5
        assert response.headers['cache-control']=='private, no-cache'
        assert 'accept-encoding' in response.headers['vary'].lower()
        cached=client.get('/static/vendor/echarts.min.js',headers={'Accept-Encoding':'gzip','If-None-Match':response.headers['etag']})
        assert cached.status_code==304 and cached.content==b''
        assert cached.headers['cache-control']=='private, no-cache'
        plain=client.get('/static/vendor/echarts.min.js',headers={'Accept-Encoding':'identity'})
        assert 'content-encoding' not in plain.headers and plain.content==response.content
        partial=client.get('/static/vendor/echarts.min.js',headers={'Accept-Encoding':'gzip','Range':'bytes=0-31'})
        assert partial.status_code==206 and partial.content==plain.content[:32]
        assert 'content-encoding' not in partial.headers
        for path in ['/','/api/state','/api/history?task_id=missing','/static/missing.js']:
            assert client.get(path).headers['cache-control']=='no-store'


def test_mobile_static_revalidation_keeps_auth_and_compressed_bytes(tmp_path):
    store=DeviceStore(tmp_path/'devices')
    device=store.redeem(store.issue_pairing('https://phone.example.ts.net')['ticket'],'phone')
    store.approve(device['device_id'])
    raw=b'/* static bundle */\n'*5000;packed=gzip.compress(raw);seen=[]
    class Wire(httpx.AsyncByteStream):
        async def __aiter__(self):yield packed
    async def upstream(request):
        seen.append(request)
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        if request.url.path=='/api/state':
            assert 'if-none-match' not in request.headers
            return httpx.Response(200,json={'private':'state'},headers={'cache-control':'public, max-age=1000'})
        assert request.headers['accept-encoding']=='gzip'
        headers={'etag':'"asset"','last-modified':'Mon, 05 Oct 2026 00:00:00 GMT','cache-control':'private, no-cache','vary':'Accept-Encoding'}
        if request.headers.get('if-none-match')=='"asset"':return httpx.Response(304,headers=headers)
        return httpx.Response(200,stream=Wire(),headers={**headers,'content-encoding':'gzip','content-type':'text/javascript'})
    with TestClient(create_gateway(store,transport=httpx.MockTransport(upstream))) as client:
        headers={'Authorization':'Bearer '+device['token'],'Accept-Encoding':'gzip'}
        response=client.get('/static/app.js',headers=headers)
        assert response.content==raw and response.headers['content-encoding']=='gzip'
        assert response.headers['content-length']==str(len(packed))
        assert response.headers['cache-control']=='private, no-cache'
        cached=client.get('/static/app.js',headers={**headers,'If-None-Match':response.headers['etag']})
        assert cached.status_code==304 and not cached.content
        assert cached.headers['etag']=='"asset"'
        dynamic=client.get('/api/state',headers={**headers,'If-None-Match':'"asset"'})
        assert dynamic.status_code==200 and dynamic.headers['cache-control']=='no-store'
        store.revoke(device['device_id'])
        denied=client.get('/static/app.js',headers={**headers,'If-None-Match':'"asset"'})
        assert denied.status_code==401 and denied.headers['cache-control']=='no-store'
        assert len(seen)==3


def test_static_encoding_negotiation_and_variant_validators(tmp_path):
    app=create_app(Monitor(Store(tmp_path/'monitor'),None),background=False)
    with TestClient(app,base_url='http://localhost') as client:
        for encoding in ('gzip;q=0, identity;q=1','gzip;q=0, *;q=1','br','gzip;q=invalid'):
            response=client.get('/static/app.js',headers={'Accept-Encoding':encoding})
            assert 'content-encoding' not in response.headers
        encoded=client.get('/static/app.js',headers={'Accept-Encoding':'gzip'})
        plain=client.get('/static/app.js',headers={'Accept-Encoding':'identity'})
        assert encoded.headers['etag'].startswith('W/')
        assert encoded.headers['etag']==plain.headers['etag']
        for encoding in ('GZIP, identity;q=0', '*;q=1, identity;q=0'):
            response=client.get('/static/app.js',headers={'Accept-Encoding':encoding})
            assert response.headers.get('content-encoding')=='gzip'
        for encoding in ('identity','gzip'):
            response=client.get('/static/app.js',headers={'Accept-Encoding':encoding,'If-None-Match':encoded.headers['etag']})
            assert response.status_code==304
            assert 'accept-encoding' in response.headers['vary'].lower()
