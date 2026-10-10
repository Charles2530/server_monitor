"""Same-origin terminal transport: WebSocket first, bounded HTTP for older mobile bridges."""
import asyncio
import anyio
import json
import re
from urllib.parse import urlsplit
from fastapi import Depends, Request, WebSocket, WebSocketDisconnect
from .acp_actions import ActionError
from .cci_ide import run_python


# Reuse the verified temporary-shell runner; never query through the user's stdin.
TMUX_LIST_SCRIPT = r'''
import os, shutil, subprocess
RESULT = {'sessions': []}
if not shutil.which('tmux'):
    RESULT['unavailable'] = True
else:
    result = subprocess.run(['tmux', '-u', 'list-sessions', '-F', '#{session_id}\t#{session_windows}\t#{session_attached}\t#{session_name}'],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=8, env=dict(os.environ, LC_ALL='C'))
    if result.returncode:
        if not any(message in result.stderr for message in ('no server running', 'No such file or directory')):
            raise RuntimeError('无法读取 tmux 会话，请检查容器内的 tmux 状态。')
    else:
        for line in result.stdout.splitlines():
            key, windows, attached, name = line.split('\t', 3)
            RESULT['sessions'].append({'id': key, 'name': name, 'windows': int(windows), 'attached': int(attached)})
'''

TMUX_SWITCH_SCRIPT = r'''
import subprocess
marker = subprocess.run(['tmux', 'show-options', '-gqv', '@ilab-client-' + TMUX_TOKEN],
                        capture_output=True, text=True, timeout=8, check=True).stdout.strip()
clients = subprocess.run(['tmux', 'list-clients', '-F', '#{client_pid}\t#{client_tty}\t#{client_name}'],
                         capture_output=True, text=True, timeout=8, check=True)
matched = []
for line in clients.stdout.splitlines():
    fields = line.split('\t', 2)
    if len(fields) == 3 and marker in (fields[1], fields[0] + ':' + fields[1]):
        matched.append(fields[2])
    elif line == marker and ':' in line:
        # Keep compatibility with older tmux output used by existing clients.
        matched.append(line.split(':', 1)[1])
if len(matched) != 1:
    RESULT = {'error': 'client_not_identified'}
else:
    subprocess.run(['tmux', 'switch-client', '-c', matched[0], '-t', TMUX_TARGET],
                   capture_output=True, text=True, timeout=8, check=True)
    RESULT = {'switched': True}
'''


def owner(request):
    return request.headers.get('x-monitor-device','local')


async def run_tmux(bridge, data, script, session=None):
    if session is None:
        return await run_python(bridge,data,script,timeout=12)
    async with session.tmux_lock:
        if session.ended:raise ActionError('终端已断开，请重新连接。')
        control=session.tmux_control
        if control is not None:
            try:bridge.get(control[0],'cci-editor-bootstrap')
            except ActionError:control=None
        if control is None or control[1].ended:
            opened=await bridge.open({**data,'cols':240,'rows':40},'cci-editor-bootstrap')
            control=(opened['session_id'],bridge.get(opened['session_id'],'cci-editor-bootstrap'))
            session.tmux_control=control
            if session.ended:
                await session.close_tmux_control()
                raise ActionError('终端已断开，请重新连接。')
        return await run_python(bridge,data,script,timeout=20,session_id=control[0])


def register_terminal_routes(app,current_site,perform,action_body,runtimes):
    @app.post('/api/terminal/tmux')
    async def tmux_sessions(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('task_id','instance','container'),optional=('session_id','tmux_session'))
            switching='tmux_session' in data
            session=None;script=TMUX_LIST_SCRIPT
            if 'session_id' in data or switching:
                key, target = data.pop('session_id', None), data.pop('tmux_session', None)
                if not isinstance(key,str) or not key or (switching and (not isinstance(target,str) or not re.fullmatch(r'\$[0-9]{1,10}',target))):
                    raise ActionError('tmux 切换参数无效。')
                session=runtime.terminal_bridge.get(key,owner(request))
                if session.ended or session.target != data:raise ActionError('tmux 与当前终端不一致，请重新连接。')
            if switching:
                script='TMUX_TOKEN = '+json.dumps(session.tmux_token)+'\nTMUX_TARGET = '+json.dumps(target)+'\n'+TMUX_SWITCH_SCRIPT
            result=await asyncio.wait_for(run_tmux(runtime.terminal_bridge,data,script,session),45)
            if switching and result.get('switched') is not True:
                raise ActionError('未能切换当前页面的 tmux 客户端；请确认会话仍在运行，或重新连接终端后再选择。')
            if result.get('error'):raise ActionError('无法读取 tmux 会话，请确认容器内 Python 3 和 tmux 可用。')
            return result
        return await perform(run)

    @app.post('/api/terminal/open')
    async def open_terminal(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('task_id','instance','container','cols','rows'))
            return await runtime.terminal_bridge.begin('open',data,owner(request))
        return await perform(run)

    @app.post('/api/terminal/targets')
    async def prepare_targets(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('task_id',))
            return await runtime.terminal_bridge.begin('targets',data,owner(request))
        return await perform(run)

    @app.post('/api/terminal/status')
    async def preparation_status(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('request_id',))
            return await runtime.terminal_bridge.request_status(data['request_id'],owner(request))
        return await perform(run)

    @app.post('/api/terminal/read')
    async def read_terminal(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('session_id','cursor'),optional=('wait',))
            return await runtime.terminal_bridge.read(data['session_id'],data['cursor'],owner(request),wait=data.get('wait',True))
        return await perform(run)

    @app.post('/api/terminal/write')
    async def write_terminal(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('session_id','sequence','data','size'))
            return await runtime.terminal_bridge.write(data['session_id'],data['sequence'],data['data'],data['size'],owner(request))
        return await perform(run)

    @app.post('/api/terminal/close')
    async def close_terminal(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('session_id',))
            return await runtime.terminal_bridge.disconnect(data['session_id'],owner(request))
        return await perform(run)

    @app.websocket('/api/terminal/socket')
    async def socket(ws:WebSocket):
        # HTTP middleware does not protect WebSocket upgrades. Require exact same-origin here.
        host=ws.headers.get('host','');origin=ws.headers.get('origin','')
        if urlsplit('//'+host).hostname not in ('127.0.0.1','localhost','::1') or origin!='http://'+host:
            await ws.close(code=1008);return
        await ws.accept()
        bridge=None;key=None;jobs=[];lease=None
        async def message():
            raw=await ws.receive_text()
            if len(raw.encode())>32768:raise ActionError('终端请求过长。')
            data=json.loads(raw)
            if not isinstance(data,dict):raise ActionError('终端请求无效。')
            return data
        send_lock=asyncio.Lock()
        async def send(data):
            async with send_lock:await ws.send_json(data)
        try:
            hello=await asyncio.wait_for(message(),5)
            if set(hello) not in ({'site','session_id'},{'site','session_id','cursor'}) or not isinstance(hello['site'],str) or hello['site'] not in runtimes:
                raise ActionError('终端站点无效。')
            bridge=runtimes[hello['site']].terminal_bridge;key=hello['session_id']
            session=bridge.get(key,owner(ws))
            cursor=hello.get('cursor',0)
            lease=bridge.attach(key,cursor,owner(ws))
            await send({'type':'ready','sequence':session.sequence})
            if await asyncio.wait_for(message(),5)!={'type':'attach'}:raise ActionError('终端连接确认无效。')
            acknowledged=asyncio.Event();sent_cursor=None
            async def output():
                nonlocal cursor,sent_cursor
                while session.transport is lease:
                    data=await bridge.read(key,cursor,owner(ws))
                    if session.transport is not lease:return
                    sent_cursor=data['cursor'];acknowledged.clear()
                    await send(data)
                    if data['closed']:break
                    # Keep buffered output until xterm has rendered it. Background
                    # tabs can pause rendering; this wait must not kill the shell.
                    await acknowledged.wait()
                    cursor=sent_cursor
            async def inputs():
                while True:
                    data=await message()
                    if session.transport is not lease:return
                    if data.get('type')=='output-ack':
                        if set(data)!={'type','cursor'} or type(data['cursor']) is not int or data['cursor']!=sent_cursor:
                            raise ActionError('终端输出确认无效。')
                        acknowledged.set();continue
                    if data=={'type':'ping'}:
                        bridge.get(key,owner(ws));await send({'type':'pong'});continue
                    if set(data)!={'sequence','data','size'}:raise ActionError('终端输入无效。')
                    await send(await bridge.write(key,data['sequence'],data['data'],data['size'],owner(ws)))
            jobs=[asyncio.create_task(output()),asyncio.create_task(inputs())]
            done,_=await asyncio.wait(jobs,return_when=asyncio.FIRST_COMPLETED)
            for job in done:job.result()
        except (WebSocketDisconnect,asyncio.CancelledError):pass
        except Exception as exc:
            try:await send({'type':'error','error':str(exc) if isinstance(exc,ActionError) else '终端连接中断，请重新连接。'})
            except Exception:pass
        finally:
            with anyio.CancelScope(shield=True):
                for job in jobs:job.cancel()
                await asyncio.gather(*jobs,return_exceptions=True)
                if bridge and key and lease is not None:
                    try:bridge.detach(key,lease,owner(ws))
                    except ActionError:pass
                try:await ws.close()
                except Exception:pass
