import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest
from server.main import create_app
from server.service import Monitor
from server.store import Store
from server.acp_actions import ActionError


def app_with_bridge(tmp_path):
    app=create_app(Monitor(Store(tmp_path/'db'),None),background=False)
    bridge=SimpleNamespace(begin=AsyncMock(return_value={'request_id':'private-request'}),read=AsyncMock(),write=AsyncMock(return_value={'type':'ack','sequence':1}),
        disconnect=AsyncMock(return_value={'closed':True}),close=AsyncMock(),get=Mock(return_value=SimpleNamespace(sequence=0,transport=None)),attach=Mock(return_value='lease'),detach=Mock())
    bridge.attach.side_effect=lambda *args:setattr(bridge.get.return_value,'transport','lease') or 'lease'
    app.state.runtimes['maoshanwang'].terminal_bridge=bridge
    return app,bridge


def test_http_terminal_scopes_and_validation(tmp_path):
    app,bridge=app_with_bridge(tmp_path)
    with TestClient(app,base_url='http://localhost:8766') as client:
        form={'task_id':'ACP:test','instance':'worker','container':'','cols':80,'rows':24}
        assert client.post('/api/terminal/open',json=form).status_code==403
        headers={'X-Monitor-Request':'1','X-Monitor-Device':'phone-a'}
        assert client.post('/api/terminal/open',json=form,headers=headers).status_code==200
        assert bridge.begin.call_args.args==('open',form,'phone-a')
        assert client.post('/api/terminal/open',json={**form,'url':'https://evil'},headers=headers).status_code==400
        assert client.post('/api/terminal/open',json=form,headers={**headers,'Origin':'https://evil'}).status_code==403
        bridge.begin.side_effect=RuntimeError('PRIVATE_TOKEN')
        r=client.post('/api/terminal/open',json=form,headers=headers)
        assert r.status_code==503 and 'PRIVATE' not in r.text


def test_websocket_rejects_cross_origin_and_nonlocal_host(tmp_path):
    app,bridge=app_with_bridge(tmp_path)
    with TestClient(app,base_url='http://localhost:8766') as client:
        for headers in ({},{'Origin':'https://evil'},{'Origin':'http://evil','Host':'evil'}):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect('ws://localhost:8766/api/terminal/socket',headers=headers):pass
        bridge.begin.assert_not_awaited()


def test_websocket_input_and_output_without_credentials_in_url(tmp_path):
    app,bridge=app_with_bridge(tmp_path)
    async def read(*args):await asyncio.sleep(60)
    bridge.read.side_effect=read
    with TestClient(app,base_url='http://localhost:8766') as client:
        with client.websocket_connect('ws://localhost:8766/api/terminal/socket',headers={'Origin':'http://localhost:8766'}) as ws:
            ws.send_json({'site':'maoshanwang','session_id':'opaque'})
            assert ws.receive_json()=={'type':'ready','sequence':0}
            ws.send_json({'type':'attach'})
            ws.send_json({'sequence':1,'data':'hello','size':None})
            assert ws.receive_json()=={'type':'ack','sequence':1}
            assert bridge.write.call_args.args==('opaque',1,'hello',None,'local')
            ws.send_json({'type':'ping'})
            assert ws.receive_json()=={'type':'pong'}
        bridge.disconnect.assert_not_awaited()
        bridge.detach.assert_called_once_with('opaque','lease','local')


def test_ws_no_attach_can_fall_back_without_closing_session(tmp_path):
    app,bridge=app_with_bridge(tmp_path)
    with TestClient(app,base_url='http://localhost:8766') as client:
        with client.websocket_connect('ws://localhost:8766/api/terminal/socket',headers={'Origin':'http://localhost:8766'}) as ws:
            ws.send_json({'site':'maoshanwang','session_id':'opaque'})
            assert ws.receive_json()=={'type':'ready','sequence':0}
            # The caller times out before confirming attachment and uses HTTP instead.
        bridge.disconnect.assert_not_awaited()


def test_ws_resume_cursor_waits_for_render_ack_before_releasing_output(tmp_path):
    app,bridge=app_with_bridge(tmp_path)
    bridge.get.return_value.transport='lease'
    async def read(key,cursor,who):
        if cursor==7:return {'type':'output','cursor':8,'chunks':['aGk='],'closed':False}
        await asyncio.sleep(60)
    bridge.read.side_effect=read
    with TestClient(app,base_url='http://localhost:8766') as client:
        with client.websocket_connect('ws://localhost:8766/api/terminal/socket',headers={'Origin':'http://localhost:8766'}) as ws:
            ws.send_json({'site':'maoshanwang','session_id':'opaque','cursor':7})
            assert ws.receive_json()=={'type':'ready','sequence':0}
            ws.send_json({'type':'attach'})
            assert ws.receive_json()['cursor']==8
            ws.send_json({'type':'ping'})
            assert ws.receive_json()=={'type':'pong'}
            assert bridge.read.await_count==1  # Sending output isn't acknowledgement.
            ws.send_json({'type':'output-ack','cursor':8})
            ws.send_json({'type':'ping'})
            assert ws.receive_json()=={'type':'pong'}
        bridge.attach.assert_called_once_with('opaque',7,'local')
        bridge.disconnect.assert_not_awaited()
