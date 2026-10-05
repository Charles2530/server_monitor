import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit,parse_qs
import pytest
from server.sites import MAOSHANWANG,SENSECORE
from server.auth import AuthRequired
from server.acp_actions import ActionError


def resolver(kind):
    return SimpleNamespace(targets=AsyncMock(return_value={'targets':[{'instance':'worker-0',**({'container':'main'} if kind=='CCI' else {})}]}))


@pytest.mark.asyncio
@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE])
@pytest.mark.parametrize('kind',['CCI','ACP'])
async def test_bridge_uses_verified_target_and_private_cloud_token(site,kind):
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket();dial=AsyncMock(return_value=socket)
    bridge=TerminalBridge(site,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=dial)
    try:
        result=await bridge.open({'task_id':kind+':job','instance':'worker-0','container':'main' if kind=='CCI' else '', 'cols':80,'rows':24},'local')
        assert 'PRIVATE' not in str(result)
        url=dial.call_args.args[0];query=parse_qs(urlsplit(url).query)
        assert urlsplit(url).hostname==urlsplit(site.services['AEC2']).hostname
        assert query=={'token':['PRIVATE'],'lines':['24'],'columns':['80']}
        assert site.workspace in urlsplit(url).path
        assert dial.call_args.kwargs['subprotocols']==['channel.k8s.io']
        await socket.messages.put(b'\x01hello\x1b[32m')
        output=await bridge.read(result['session_id'],0,'local')
        assert output['chunks']==['aGVsbG8bWzMybQ=='] and output['cursor']==1
        await bridge.write(result['session_id'],1,'echo hi\r',None,'local')
        await bridge.write(result['session_id'],1,'echo hi\r',None,'local')
        assert socket.sent==[b'\x04{"Width":80,"Height":24}',b'\0echo hi\r']
        await bridge.write(result['session_id'],2,'',{'cols':120,'rows':40},'local')
        assert socket.sent[-1]==b'\x04{"Width":120,"Height":40}'
        with pytest.raises(ActionError):await bridge.read(result['session_id'],0,'other-device')
    finally:await bridge.close()


class FakeSocket:
    def __init__(self):self.messages=asyncio.Queue();self.sent=[];self.closed=False
    def __aiter__(self):return self
    async def __anext__(self):return await self.messages.get()
    async def send(self,data):self.sent.append(data)
    async def close(self):self.closed=True


@pytest.mark.asyncio
async def test_expired_auth_renews_once_and_revalidates_before_opening():
    from server.terminal_bridge import TerminalBridge
    cci=resolver('CCI');cci.targets.side_effect=[AuthRequired('expired'),{'targets':[{'instance':'worker-0','container':'main'}]}]
    renew=AsyncMock();socket=FakeSocket();dial=AsyncMock(return_value=socket)
    bridge=TerminalBridge(MAOSHANWANG,cci,resolver('ACP'),renew=renew,session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=dial)
    try:
        await bridge.open({'task_id':'CCI:job','instance':'worker-0','container':'main','cols':80,'rows':24},'local')
        assert cci.targets.await_count==2 and renew.await_count==1 and dial.await_count==1
    finally:await bridge.close()


@pytest.mark.asyncio
async def test_invalid_foreign_target_and_input_do_not_reach_cloud():
    from server.terminal_bridge import TerminalBridge
    dial=AsyncMock();bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),dial=dial)
    form={'task_id':'CCI:job','instance':'../foreign','container':'main','cols':80,'rows':24}
    for changes in ({},{'instance':'worker-0','container':'other'},{'instance':'worker-0','cols':0}):
        with pytest.raises(ActionError):await bridge.open({**form,**changes},'local')
    dial.assert_not_awaited()
    await bridge.close()


@pytest.mark.asyncio
async def test_upstream_failure_sanitized_and_no_input_retry():
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket()
    bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=AsyncMock(return_value=socket))
    try:
        result=await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'local')
        socket.send=AsyncMock(side_effect=RuntimeError('PRIVATE cloud URL token'))
        with pytest.raises(ActionError) as e:await bridge.write(result['session_id'],1,'command',None,'local')
        assert 'PRIVATE' not in str(e.value)
        with pytest.raises(ActionError):await bridge.write(result['session_id'],1,'command',None,'local')
        assert socket.send.await_count==1
    finally:await bridge.close()

@pytest.mark.asyncio
async def test_expired_websocket_handshake_retries_auth_only_once():
    from server.terminal_bridge import TerminalBridge
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response
    from websockets.datastructures import Headers
    failure=InvalidStatus(Response(401,'Unauthorized',Headers()))
    dial=AsyncMock(side_effect=[failure,FakeSocket()]);renew=AsyncMock()
    acp=resolver('ACP');bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),acp,renew=renew,session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=dial)
    try:
        await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'local')
        assert dial.await_count==acp.targets.await_count==2
        renew.assert_awaited_once()
    finally:await bridge.close()


@pytest.mark.asyncio
async def test_input_order_replay_changed_payload_and_isolation():
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket();bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=AsyncMock(return_value=socket))
    other=TerminalBridge(SENSECORE,resolver('CCI'),resolver('ACP'))
    try:
        key=(await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'phone-a'))['session_id']
        await bridge.write(key,1,'one',None,'phone-a')
        for seq,text,who in [(1,'different','phone-a'),(3,'skip','phone-a'),(2,'foreign','phone-b')]:
            with pytest.raises(ActionError):await bridge.write(key,seq,text,None,who)
        with pytest.raises(ActionError):other.get(key,'phone-a')
        assert socket.sent==[b'\x04{"Width":80,"Height":24}',b'\0one']
        with pytest.raises(ActionError):await bridge.read(key,100,'phone-a')
        await bridge.disconnect(key,'phone-a')
        assert socket.closed and not bridge.sessions
    finally:await bridge.close();await other.close()


@pytest.mark.asyncio
async def test_output_is_bounded_and_shell_error_never_leaks_internal_message(monkeypatch):
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket();bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=AsyncMock(return_value=socket))
    try:
        key=(await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'local'))['session_id']
        await socket.messages.put(b'\3PRIVATE_TOKEN internal error')
        data=await bridge.read(key,0,'local')
        assert data['closed'] and 'PRIVATE' not in str(data)
        monkeypatch.setattr('server.terminal_bridge.MAX_BUFFER',4)
        socket=FakeSocket();bridge.dial=AsyncMock(return_value=socket)
        key=(await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'local'))['session_id']
        await socket.messages.put(b'\x0112345')
        data=await bridge.read(key,0,'local')
        assert data['closed'] and not data['chunks'] and '积压' in data['error']
    finally:await bridge.close()


@pytest.mark.asyncio
async def test_session_limit_and_expiry_close_sockets(monkeypatch):
    import time
    from server.terminal_bridge import TerminalBridge,IDLE_SECONDS
    monkeypatch.setattr('server.terminal_bridge.MAX_SESSIONS',1)
    socket=FakeSocket();bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=AsyncMock(return_value=socket))
    form={'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24}
    try:
        key=(await bridge.open(form,'local'))['session_id']
        with pytest.raises(ActionError) as caught:await bridge.open(form,'local')
        assert caught.value.status==429
        bridge.sessions[key].touched=time.monotonic()-IDLE_SECONDS-1
        with pytest.raises(ActionError):await bridge.read(key,0,'local')
        fresh=FakeSocket();bridge.dial=AsyncMock(return_value=fresh)
        new=(await bridge.open(form,'local'))['session_id']
        assert new!=key and socket.closed
        s=bridge.sessions[new]
        await bridge.close()
        assert fresh.closed and s.pump.done() and s.heartbeat.done()
    finally:await bridge.close()

@pytest.mark.asyncio
async def test_long_login_prepares_in_background_and_status_is_owner_scoped(monkeypatch):
    from server.terminal_bridge import TerminalBridge
    monkeypatch.setattr('server.terminal_bridge.PREPARATION_POLL_SECONDS',.01)
    bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'))
    gate=asyncio.Event()
    async def slow(*args):await gate.wait();return {'targets':[]}
    bridge.targets=slow
    try:
        response=await asyncio.wait_for(bridge.begin('targets',{'task_id':'CCI:job'},'phone-a'),.1)
        key=response['request_id']
        assert await bridge.request_status(key,'phone-a')=={'pending':True}
        with pytest.raises(ActionError):await bridge.request_status(key,'phone-b')
        gate.set()
        assert await bridge.request_status(key,'phone-a')=={'pending':False,'result':{'targets':[]}}
        assert await bridge.request_status(key,'phone-a')=={'pending':False,'result':{'targets':[]}}
    finally:await bridge.close()


def test_cloud_websocket_never_follows_redirect():
    from server.terminal_bridge import CloudConnection
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response
    from websockets.datastructures import Headers
    error=InvalidStatus(Response(302,'Found',Headers({'Location':'wss://evil.test/'})))
    assert CloudConnection.process_redirect(None,error) is error


@pytest.mark.asyncio
async def test_initial_pty_resize_sent_before_session_is_exposed():
    """Cloud handshake query doesn't initialize PTY size (observed as stty 0 0)."""
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket()
    bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=AsyncMock(return_value=socket))
    try:
        await bridge.open({'task_id':'CCI:job','instance':'worker-0','container':'main','cols':155,'rows':38},'local')
        assert socket.sent==[b'\x04{"Width":155,"Height":38}']
    finally:await bridge.close()


@pytest.mark.asyncio
async def test_initial_resize_failure_closes_unpublished_socket():
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket();socket.send=AsyncMock(side_effect=OSError('private handshake data'))
    bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer PRIVATE'}},dial=AsyncMock(return_value=socket))
    try:
        with pytest.raises(ActionError) as caught:
            await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':155,'rows':38},'local')
        assert socket.closed and not bridge.sessions and 'private' not in str(caught.value)
    finally:await bridge.close()


@pytest.mark.asyncio
async def test_attached_background_session_survives_idle_and_detach_can_resume(monkeypatch):
    import time
    from server.terminal_bridge import TerminalBridge,IDLE_SECONDS
    socket=FakeSocket();bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer private'}},dial=AsyncMock(return_value=socket))
    try:
        key=(await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'local'))['session_id']
        lease=bridge.attach(key,0,'local')
        s=bridge.sessions[key];s.touched=time.monotonic()-IDLE_SECONDS-1
        assert bridge.get(key,'local') is s
        await bridge.write(key,1,'one',None,'local')
        await socket.messages.put(b'\1retained')
        first=await bridge.read(key,0,'local')
        bridge.detach(key,lease,'local')
        assert not socket.closed
        replacement=bridge.attach(key,0,'local')
        assert replacement is not lease
        bridge.detach(key,lease,'local')  # stale transport cannot detach the replacement
        assert s.transport is replacement
        resumed=await bridge.read(key,0,'local')
        assert resumed['chunks']==first['chunks'] and resumed['sequence']==1
        with pytest.raises(ActionError):bridge.attach(key,0,'other')
        bridge.detach(key,replacement,'local')
        s.touched=time.monotonic()-IDLE_SECONDS-1
        with pytest.raises(ActionError):bridge.get(key,'local')
    finally:await bridge.close()


@pytest.mark.asyncio
async def test_non_waiting_read_returns_empty_output_with_current_sequence():
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket();bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer private'}},dial=AsyncMock(return_value=socket))
    try:
        key=(await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'phone-a'))['session_id']
        await bridge.write(key,1,'one',None,'phone-a')
        output=await asyncio.wait_for(bridge.read(key,0,'phone-a',wait=False),.1)
        assert output=={'type':'output','cursor':0,'chunks':[],'closed':False,'error':None,'sequence':1}
    finally:await bridge.close()


@pytest.mark.asyncio
async def test_read_wait_must_be_keyword_only_boolean_and_preserves_ownership():
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket();bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer private'}},dial=AsyncMock(return_value=socket))
    try:
        key=(await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'phone-a'))['session_id']
        for invalid in (None,0,1,'false'):
            with pytest.raises(ActionError):await bridge.read(key,0,'phone-a',wait=invalid)
        with pytest.raises(TypeError):await bridge.read(key,0,'phone-a',False)
        with pytest.raises(ActionError):await bridge.read(key,0,'phone-b',wait=False)
    finally:await bridge.close()


@pytest.mark.asyncio
async def test_read_without_wait_argument_keeps_long_polling():
    from server.terminal_bridge import TerminalBridge
    socket=FakeSocket();bridge=TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'),session_loader=lambda:{'headers':{'authorization':'Bearer private'}},dial=AsyncMock(return_value=socket))
    try:
        key=(await bridge.open({'task_id':'ACP:job','instance':'worker-0','container':'','cols':80,'rows':24},'phone-a'))['session_id']
        pending=asyncio.create_task(bridge.read(key,0,'phone-a'))
        await asyncio.sleep(0)
        assert not pending.done()
        await socket.messages.put(b'\1ready')
        output=await asyncio.wait_for(pending,.1)
        assert output['chunks']==['cmVhZHk='] and output['cursor']==1
    finally:await bridge.close()
