"""Browser resource requests must preserve body framing through code-server."""
import httpx
import pytest
from server.ide_proxy import EditorProxy


class ResponseStream(httpx.AsyncByteStream):
    async def __aiter__(self):yield b'webview'


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['GET', 'HEAD'])
async def test_bodyless_resource_request_does_not_become_chunked(method):
    seen=[]
    async def upstream(request):
        seen.append(request)
        # code-server rejects a chunked GET (confirmed against its webview entry).
        if 'transfer-encoding' in request.headers:
            raise httpx.RemoteProtocolError('Server disconnected without sending a response.')
        return httpx.Response(200,stream=ResponseStream())
    proxy=EditorProxy(12345,'private=upstream','http://localhost:8766',transport=httpx.MockTransport(upstream))
    proxy.port=23456
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app),base_url=proxy.origin) as client:
            await client.get('/_ilab_open/'+proxy.ticket())
            response=await client.request(method,'/static/webview/index.html?platform=browser')
            assert response.status_code==200
            assert 'transfer-encoding' not in seen[-1].headers
            assert seen[-1].content==b''
            assert seen[-1].url.query==b'platform=browser'
    finally:await proxy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['POST','PUT','GET'])
async def test_requests_with_bodies_keep_exact_bytes(method):
    seen=[]
    async def upstream(request):
        seen.append(request)
        return httpx.Response(200,stream=ResponseStream())
    proxy=EditorProxy(12345,'private=upstream','http://localhost:8766',transport=httpx.MockTransport(upstream))
    proxy.port=23456
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app),base_url=proxy.origin) as client:
            await client.get('/_ilab_open/'+proxy.ticket())
            response=await client.request(method,'/resource',content=b'\x00exact\xffpayload')
            assert response.status_code==200
            assert seen[-1].content==b'\x00exact\xffpayload'
            assert seen[-1].headers['content-length']=='14'
    finally:await proxy.close()


@pytest.mark.asyncio
async def test_browser_compression_is_negotiated_and_encoded_bytes_stay_intact():
    import gzip
    original=b'webview javascript resource\n'*100
    encoded=gzip.compress(original)
    seen=[]
    class CompressedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield encoded[:20]
            yield encoded[20:]
    async def upstream(request):
        seen.append(request)
        return httpx.Response(200,stream=CompressedStream(),headers={'content-encoding':'gzip','content-length':str(len(encoded)),'vary':'Accept-Encoding'})
    proxy=EditorProxy(12345,'private=upstream','http://localhost:8766',transport=httpx.MockTransport(upstream))
    proxy.port=23456
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app),base_url=proxy.origin) as client:
            await client.get('/_ilab_open/'+proxy.ticket())
            async with client.stream('GET','/static/workbench.js',headers={'accept-encoding':'gzip'}) as r:
                wire=b''.join([chunk async for chunk in r.aiter_raw()])
                assert r.headers['content-encoding']=='gzip'
                assert wire==encoded and gzip.decompress(wire)==original
            assert seen[-1].headers['accept-encoding']=='gzip'
            client.headers.pop('accept-encoding',None)
            async with client.stream('GET','/static/resource') as r:await r.aread()
            assert seen[-1].headers['accept-encoding']=='identity'
    finally:await proxy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,expected', [
    ('GET','/stable-'+'a'*40+'/static/webview/index.html',2),
    ('HEAD','/stable-'+'a'*40+'/vscode-remote-resource',2),
    ('GET','/_static/resource.js',2),
    ('POST','/_static/resource.js',1),
    ('GET','/proxy/8080/action',1),
])
async def test_pre_header_disconnect_retries_only_readonly_resources(method,path,expected):
    seen=[]
    async def upstream(request):
        seen.append(request)
        if len(seen)==1:raise httpx.RemoteProtocolError('private internal handshake data')
        return httpx.Response(200,stream=ResponseStream())
    proxy=EditorProxy(12345,'private=upstream','http://localhost:8766',transport=httpx.MockTransport(upstream))
    proxy.port=23456
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app),base_url=proxy.origin) as client:
            await client.get('/_ilab_open/'+proxy.ticket())
            response=await client.request(method,path)
            assert len(seen)==expected
            assert response.status_code==(200 if expected==2 else 502)
            assert 'private' not in response.text
    finally:await proxy.close()


@pytest.mark.asyncio
async def test_resource_retry_is_bounded():
    seen=[]
    async def upstream(request):
        seen.append(request)
        raise httpx.RemoteProtocolError('private details')
    proxy=EditorProxy(12345,'private=upstream','http://localhost:8766',transport=httpx.MockTransport(upstream))
    proxy.port=23456
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app),base_url=proxy.origin) as client:
            await client.get('/_ilab_open/'+proxy.ticket())
            r=await client.get('/_static/resource.js')
            assert r.status_code==502 and len(seen)==2
            assert 'private' not in r.text
    finally:await proxy.close()
