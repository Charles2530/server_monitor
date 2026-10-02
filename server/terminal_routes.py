"""Same-origin terminal transport: WebSocket first, bounded HTTP for older mobile bridges."""
import asyncio
import anyio
import json
from urllib.parse import urlsplit
from fastapi import Depends, Request, WebSocket, WebSocketDisconnect
from .acp_actions import ActionError


def owner(request):
    return request.headers.get('x-monitor-device','local')


def register_terminal_routes(app,current_site,perform,action_body,runtimes):
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
            data=await action_body(request,('session_id','cursor'))
            return await runtime.terminal_bridge.read(data['session_id'],data['cursor'],owner(request))
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
        bridge=None;key=None;jobs=[];attached=False
        async def message():
            raw=await asyncio.wait_for(ws.receive_text(),45)
            if len(raw.encode())>32768:raise ActionError('终端请求过长。')
            data=json.loads(raw)
            if not isinstance(data,dict):raise ActionError('终端请求无效。')
            return data
        send_lock=asyncio.Lock()
        async def send(data):
            async with send_lock:await ws.send_json(data)
        try:
            hello=await asyncio.wait_for(message(),5)
            if set(hello)!={'site','session_id'} or not isinstance(hello['site'],str) or hello['site'] not in runtimes:
                raise ActionError('终端站点无效。')
            bridge=runtimes[hello['site']].terminal_bridge;key=hello['session_id']
            bridge.get(key,owner(ws))
            await send({'type':'ready'})
            if await asyncio.wait_for(message(),5)!={'type':'attach'}:raise ActionError('终端连接确认无效。')
            attached=True
            async def output():
                cursor=0
                while True:
                    data=await bridge.read(key,cursor,owner(ws))
                    await send(data);cursor=data['cursor']
                    if data['closed']:break
            async def inputs():
                while True:
                    data=await message()
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
                if bridge and key and attached:
                    try:await bridge.disconnect(key,owner(ws))
                    except ActionError:pass
                try:await ws.close()
                except Exception:pass
