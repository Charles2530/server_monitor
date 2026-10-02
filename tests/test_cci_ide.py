import asyncio
import time
import httpx
import pytest
from server.acp_actions import ActionError


def test_private_target_validation():
    from server.cci_ide import private_ipv4
    assert private_ipv4('10.119.1.2')=='10.119.1.2'
    for value in ('127.0.0.1','169.254.1.1','8.8.8.8','::1','10.0.0.1;cmd',None):
        with pytest.raises(ActionError):private_ipv4(value)


def test_bootstrap_fixed_artifact_no_target_injection():
    from server.ide_bootstrap import bootstrap_script
    script=bootstrap_script('pod-test','10.119.1.2')
    compile(script,'bootstrap','exec')
    assert '53029be6c5781b7bca49b815fcc9a2a3fc111813ad8c9965b2c0f0d2985a0674' in script
    assert '4.139.1' in script and 'os.getpid' not in script
    assert 'authorized_keys' not in script and 'sshd' not in script


@pytest.mark.asyncio
async def test_proxy_ticket_origin_and_secret_boundaries():
    from server.ide_proxy import EditorProxy
    seen=[]
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self): yield b'editor'
    async def upstream(req):
        seen.append(req)
        return httpx.Response(200,stream=Stream(),headers={'set-cookie':'upstream=SECRET','x-frame-options':'SAMEORIGIN'})
    proxy=EditorProxy(12345,'upstream=SECRET','http://127.0.0.1:8766',transport=httpx.MockTransport(upstream))
    proxy.port=23456
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app),base_url='http://127.0.0.1:23456') as client:
        assert (await client.get('/')).status_code==403
        ticket=proxy.ticket()
        assert (await client.get('/_ilab_open/'+ticket,headers={'host':'attacker.test:23456'})).status_code==403
        response=await client.get('/_ilab_open/'+ticket)
        assert response.status_code==303 and 'HttpOnly' in response.headers['set-cookie']
        assert (await client.get('/_ilab_open/'+ticket)).status_code==403
        response=await client.get('/')
        assert response.text=='editor' and 'set-cookie' not in response.headers
        assert 'x-frame-options' not in response.headers
        assert "frame-ancestors 'self' http://127.0.0.1:8766" in response.headers['content-security-policy']
        assert seen[-1].headers['cookie']=='upstream=SECRET'
        assert (await client.post('/',headers={'origin':'http://evil.test'})).status_code==403
        assert (await client.get('/',headers={'origin':'http://127.0.0.1:8766'})).status_code==403
        assert 'SECRET' not in response.text
        ticket=proxy.ticket();proxy.tickets[ticket]=time.monotonic()-100
        assert (await client.get('/_ilab_open/'+ticket)).status_code==403
    await proxy.close()


@pytest.mark.asyncio
async def test_proxy_rejects_login_redirect():
    from server.ide_proxy import EditorProxy
    proxy=EditorProxy(12345,'secret=x','http://localhost:8766',transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'/login'})))
    proxy.port=23456
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app),base_url='http://localhost:23456') as client:
        await client.get('/_ilab_open/'+proxy.ticket())
        assert (await client.get('/')).status_code==502
    await proxy.close()


@pytest.mark.asyncio
async def test_proxy_health_detects_expired_auth_and_transport_failure():
    from server.ide_proxy import EditorProxy
    for response, expected in [(httpx.Response(200),True),(httpx.Response(302,headers={'location':'/login?to=/'}),False),(httpx.Response(503),False)]:
        proxy=EditorProxy(12345,'secret=x','http://localhost:8766',transport=httpx.MockTransport(lambda r:response))
        assert await proxy.healthy() is expected
        await proxy.close()


@pytest.mark.asyncio
async def test_failed_preparation_preserves_existing_editor_proxy(monkeypatch):
    from types import SimpleNamespace
    from server.cci_ide import CCIEditor
    from server.sites import MAOSHANWANG
    import server.cci_ide as module
    data={'task_id':'CCI:app-test','instance':'pod-test','container':'main'}
    editor=CCIEditor(MAOSHANWANG,None,None)
    closed=[]
    class Proxy:
        async def healthy(self):return False
        async def close(self):closed.append('proxy')
    class Forward:
        process=SimpleNamespace(returncode=None)
        async def close(self):closed.append('ssh')
    async def resolve(data):return {'name':'charles test'},'10.0.0.1'
    async def bootstrap(*args,**kwargs):raise ActionError('test bootstrap reached')
    editor.resolve=resolve
    key=(data['task_id'],data['instance'],data['container'],'http://localhost:8766')
    editor.sessions[key]={'address':'10.0.0.1','proxy':Proxy(),'forward':Forward()}
    monkeypatch.setattr(module,'run_python',bootstrap)
    with pytest.raises(ActionError,match='test bootstrap reached'):await editor.open(data,key[-1])
    assert closed==[] and key in editor.sessions


@pytest.mark.asyncio
async def test_prepare_deduplicates_pending_and_rejects_wrong_kind():
    from server.cci_ide import CCIEditor
    from server.sites import MAOSHANWANG
    editor=CCIEditor(MAOSHANWANG,None,None)
    gate=asyncio.Event();count=[]
    async def targets(task):count.append(task);await gate.wait();return {'task_id':task}
    editor.targets=targets
    try:
        with pytest.raises(ActionError):await editor.begin('targets',{'task_id':'ACP:job'},'http://localhost:8766')
        a=await editor.begin('targets',{'task_id':'CCI:job'},'http://localhost:8766')
        b=await editor.begin('targets',{'task_id':'CCI:job'},'http://localhost:8766')
        assert a==b
        with pytest.raises(ActionError):await editor.status(a['request_id'],'http://localhost:9000')
        assert (await editor.status(a['request_id'],'http://localhost:8766'))['pending']
        gate.set();await asyncio.sleep(.01)
        assert count==['CCI:job']
        assert (await editor.status(a['request_id'],'http://localhost:8766'))['result']=={'task_id':'CCI:job'}
    finally:await editor.close()


def test_ide_routes_use_local_guards_and_narrow_frame_policy(tmp_path):
    from fastapi.testclient import TestClient
    from server.main import create_app
    from server.service import Monitor
    from server.store import Store
    app=create_app(Monitor(Store(tmp_path/'db'),None),background=False)
    with TestClient(app,base_url='http://localhost:8766') as client:
        headers={'X-Monitor-Request':'1'}
        assert 'frame-src' in client.get('/ide').headers['content-security-policy']
        assert 'frame-src' not in client.get('/').headers['content-security-policy']
        assert client.post('/api/cci/ide/targets',json={'task_id':'CCI:job'}).status_code==403
        recovery={'task_id':'CCI:job','instance':'pod','container':'main'}
        assert client.post('/api/cci/ide/recover',json=recovery).status_code==403
        assert client.post('/api/cci/ide/recover',json=recovery,headers={**headers,'origin':'http://evil.test'}).status_code==403
        assert client.post('/api/cci/ide/recover',json={**recovery,'url':'http://arbitrary'},headers=headers).status_code==400
        assert client.post('/api/cci/ide/targets',json={'task_id':'ACP:job'},headers=headers).status_code==400
        assert client.post('/api/cci/ide/open',json={'task_id':'CCI:job','instance':'pod','container':'main','url':'http://arbitrary'},headers=headers).status_code==400
        assert client.post('/api/cci/ide/status',json={'request_id':[]},headers=headers).status_code==404


@pytest.mark.asyncio
async def test_proxy_websocket_auth_and_binary_transport(monkeypatch):
    from websockets.asyncio.server import serve
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus
    from server.ide_proxy import EditorProxy
    received=[]
    import server.ide_proxy as proxy_module
    original_connect = proxy_module.connect
    def tolerant_connect(*args, **kwargs):
        assert kwargs.get('ping_timeout') == 120
        return original_connect(*args, **kwargs)
    monkeypatch.setattr(proxy_module, 'connect', tolerant_connect)
    async def echo(ws):
        received.append(ws.request.headers.get('Cookie'))
        async for message in ws:await ws.send(message)
    async with serve(echo,'127.0.0.1',0) as upstream:
        port=upstream.sockets[0].getsockname()[1]
        proxy=EditorProxy(port,'private=upstream','http://127.0.0.1:8766')
        await proxy.start()
        try:
            assert proxy.server.config.ws_ping_timeout == 120
            url=proxy.origin.replace('http:','ws:')+'/socket'
            with pytest.raises(InvalidStatus):
                async with connect(url,origin=proxy.origin,proxy=None):pass
            async with httpx.AsyncClient(base_url=proxy.origin) as client:
                assert (await client.get('/_ilab_open/'+proxy.ticket())).status_code==303
                cookie='; '.join(k+'='+v for k,v in client.cookies.items())
            with pytest.raises(InvalidStatus):
                async with connect(url,origin='http://evil.test',additional_headers={'Cookie':cookie},proxy=None):pass
            async with connect(url,origin=proxy.origin,additional_headers={'Cookie':cookie},proxy=None) as ws:
                await ws.send(b'\x00\xffeditor');assert await ws.recv()==b'\x00\xffeditor'
                await ws.send('text');assert await ws.recv()=='text'
            assert received==['private=upstream']
        finally:await proxy.close()


@pytest.mark.asyncio
async def test_terminal_bootstrap_parses_only_control_delimited_json_and_closes():
    import base64,re
    from server.cci_ide import run_python
    class Bridge:
        def __init__(self):self.commands=[];self.closed=False
        async def open(self,data,owner):return {'session_id':'owned'}
        async def write(self,key,seq,data,size,owner):self.commands.append(data)
        async def read(self,key,cursor,owner):
            command=''.join(self.commands)
            parts=re.findall(r"'([A-Za-z0-9+/=]+)'",command)
            payload=base64.b64decode(''.join(parts)).decode()
            tag=re.search(r"chr\(30\)\+'([a-f0-9]+)'",payload).group(1)
            raw=b'echo text and fake JSON {"password":"wrong"}'+b'\x1e'+tag.encode()+b'{"port":1234}\x1f'
            return {'chunks':[base64.b64encode(raw).decode()],'cursor':1,'closed':False}
        async def disconnect(self,key,owner):self.closed=True
    bridge=Bridge()
    assert await run_python(bridge,{'task_id':'CCI:test','instance':'pod','container':'main'},'RESULT={"port":1234}')=={'port':1234}
    assert bridge.closed and max(map(len,bridge.commands))<=1800


@pytest.mark.asyncio
async def test_missing_codex_plugin_retries_without_replacing_healthy_editor(monkeypatch):
    from types import SimpleNamespace
    import server.cci_ide as module
    from server.sites import MAOSHANWANG
    editor=module.CCIEditor(MAOSHANWANG,None,None)
    data={'task_id':'CCI:app-test','instance':'pod-test','container':'main'}
    origin='http://localhost:8766'
    calls=[]
    class Proxy:
        port=23456
        async def healthy(self):return True
        def ticket(self):return 'ticket'
    async def resolve(data):return {'name':'charles'},'10.0.0.1'
    async def bootstrap(*args,**kwargs):calls.append(1);return {'codex':{'configured':True,'installed':True}}
    editor.resolve=resolve;monkeypatch.setattr(module,'run_python',bootstrap)
    key=(data['task_id'],data['instance'],data['container'],origin)
    session={'address':'10.0.0.1','proxy':Proxy(),'forward':SimpleNamespace(process=SimpleNamespace(returncode=None)),'codex':{'configured':True,'installed':False}}
    editor.sessions[key]=session
    assert (await editor.open(data,origin))['codex']['installed']
    assert (await editor.open(data,origin))['codex']['installed']
    assert calls==[1] and editor.sessions[key] is session


@pytest.mark.asyncio
@pytest.mark.parametrize('action,expected',[('targets','读取'),('open','连接')])
async def test_pending_status_does_not_claim_download_before_installation(action,expected):
    from server.cci_ide import CCIEditor
    from server.sites import MAOSHANWANG
    editor=CCIEditor(MAOSHANWANG,None,None)
    gate=asyncio.Event()
    async def pending(*args,**kwargs):await gate.wait()
    editor.targets=editor.open=pending
    data={'task_id':'CCI:job'}
    if action=='open':data.update(instance='pod',container='main')
    try:
        started=await editor.begin(action,data,'http://localhost:8766')
        status=await editor.status(started['request_id'],'http://localhost:8766')
        assert status['pending'] and expected in status['message']
        assert all(word not in status['message'] for word in ('首次','下载','220 MB','15 分钟'))
    finally:await editor.close()


@pytest.mark.asyncio
async def test_repeated_open_reuses_healthy_editor_without_bootstrap(monkeypatch):
    from types import SimpleNamespace
    import server.cci_ide as module
    from server.sites import MAOSHANWANG
    editor=module.CCIEditor(MAOSHANWANG,None,None)
    data={'task_id':'CCI:job','instance':'pod','container':'main'}
    origin='http://localhost:8766'
    class Proxy:
        port=23456
        async def healthy(self):return True
        def ticket(self):return 'ticket'
    async def resolve(data):return {'name':'charles'},'10.0.0.1'
    async def forbidden(*args,**kwargs):raise AssertionError('Healthy sessions must not run installation')
    editor.resolve=resolve
    monkeypatch.setattr(module,'run_python',forbidden)
    key=(data['task_id'],data['instance'],data['container'],origin)
    session={'address':'10.0.0.1','proxy':Proxy(),'forward':SimpleNamespace(process=SimpleNamespace(returncode=None)),'codex':{'configured':True,'installed':True}}
    editor.sessions[key]=session
    for _ in range(2):
        assert (await editor.open(data,origin))['port']==23456
    assert editor.sessions[key] is session
