"""Loopback-only dashboard API and independent background polling."""
import asyncio
import json
import fcntl
import time
from dataclasses import dataclass
from functools import partial
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI, Query, Request, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .collector import Collector
from .auth import connect_browser, AuthRequired
from .acp_actions import ACPManager, ActionError
from .task_logs import TaskLogs
from .task_rename import TaskRenamer
from .cci_terminal import CCITerminal
from .acp_terminal import ACPTerminal
from .terminal_bridge import TerminalBridge
from .terminal_routes import register_terminal_routes
from .cci_ide import CCIEditor
from .ide_routes import register_ide_routes
from .storage import StorageManager
from .task_history import TaskHistory
from .utilization_queue import UtilizationQueue
from .tokenhub import TokenHub
from .tokenhub_routes import register_tokenhub_routes
from .normalize import task_console_url
from .config import DATA, ROOT
from .service import Monitor
from .store import Store
from .http_assets import StaticAssetCompression, revalidatable_asset
from .runtime_profile import get_runtime_profile
from .sites import SITES, MAOSHANWANG


READ_TIMEOUT_SECONDS = 50
LOG_TIMEOUT_SECONDS = 80


@dataclass
class SiteRuntime:
    monitor: object
    actions: object
    logs: object
    site: object = MAOSHANWANG
    storage: object = None
    task_history: object = None
    terminal: object = None
    acp_terminal: object = None
    terminal_bridge: object = None
    ide: object = None
    utilization_queue: object = None
    renamer: object = None


def create_app(monitor=None, background=True, actions=None, logs=None, runtimes=None, tokenhub=None):
    tokenhub = tokenhub or TokenHub()
    if runtimes is None:
        if monitor is not None:
            runtimes={'maoshanwang':SiteRuntime(monitor,actions or ACPManager(monitor.store),logs or TaskLogs())}
        else:
            runtimes={}
            for site in SITES.values():
                site.data.mkdir(parents=True,mode=0o700,exist_ok=True)
                store=Store(site.data/'monitor.sqlite3')
                instance=Monitor(store,Collector(site),renew=partial(connect_browser,site=site))
                runtimes[site.id]=SiteRuntime(instance,ACPManager(store,site=site),TaskLogs(site=site),site)
    for runtime in runtimes.values():
        if runtime.renamer is None:runtime.renamer=TaskRenamer(runtime.monitor.store,site=runtime.site)
        if runtime.terminal is None:runtime.terminal=CCITerminal(runtime.site)
        if runtime.acp_terminal is None:runtime.acp_terminal=ACPTerminal(runtime.site)
        if runtime.terminal_bridge is None:runtime.terminal_bridge=TerminalBridge(runtime.site,runtime.terminal,runtime.acp_terminal)
        if runtime.ide is None:runtime.ide=CCIEditor(runtime.site,runtime.terminal_bridge,runtime.terminal)
        if runtime.storage is None:runtime.storage=StorageManager(runtime.site)
        if runtime.task_history is None:runtime.task_history=TaskHistory(runtime.monitor.store,runtime.site)
        if runtime.utilization_queue is None:runtime.utilization_queue=UtilizationQueue(runtime.site)
    def current_site(request: Request):
        site_id=request.headers.get('x-monitor-site','maoshanwang')
        if site_id not in runtimes:
            raise HTTPException(status_code=400,detail='未知站点，未执行请求。')
        return runtimes[site_id]
    @asynccontextmanager
    async def lifespan(app):
        lock_file = None
        if background:
            lock_file = (DATA/'collector.lock').open('w')
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock_file.close()
                raise RuntimeError('本项目采集器已经运行。') from None
            for runtime in runtimes.values():
                instance=runtime.monitor
                instance.loop_task=instance.spawn(instance.loop())
            tokenhub.loop_task=asyncio.create_task(tokenhub.loop())
        try:
            yield
        finally:
            await tokenhub.close()
            for runtime in runtimes.values():
                await runtime.ide.close()
                await runtime.terminal_bridge.close()
                await runtime.monitor.close()
                if hasattr(runtime.storage,"close"):await runtime.storage.close()
                if hasattr(runtime.task_history,"close"):await runtime.task_history.close()
                await runtime.utilization_queue.close()
            if lock_file:
                lock_file.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(StaticAssetCompression)
    app.state.monitor=runtimes["maoshanwang"].monitor
    app.state.runtimes=runtimes
    app.state.tokenhub=tokenhub
    app.state.runtime_profile=get_runtime_profile()

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host=urlparse('//'+request.headers.get('host','')).hostname
        if host not in ('127.0.0.1','localhost','::1'):
            return JSONResponse({'error':'仅允许本机访问。'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'error':'拒绝跨站请求。'},status_code=403)
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('x-monitor-request')!='1':
            return JSONResponse({'error':'缺少本地请求标记。'},status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']=('private, no-cache' if revalidatable_asset(request.url.path,request.method,response.status_code) else 'no-store')
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path=='/ide':
            response.headers['Content-Security-Policy'] += "; frame-src http://127.0.0.1:* http://localhost:*"
        return response

    @app.get('/')
    async def index():
        return FileResponse(ROOT/'static/index.html')

    @app.get('/ide')
    async def ide_page():
        return FileResponse(ROOT/'static/ide.html')

    @app.get('/terminal')
    async def terminal_page():
        return FileResponse(ROOT/'static/terminal.html')

    @app.get('/api/state')
    async def state(runtime=Depends(current_site)):
        snapshot=dict(runtime.monitor.state)
        snapshot["site"]={"id":runtime.site.id,"console":runtime.site.console,"workspace":runtime.site.workspace}
        for key in ('tasks','waiting_tasks'):
            snapshot[key]=[
                {**{k:v for k,v in task.items() if k not in ('series','uid','source_id')},
                 'url':task_console_url(task,runtime.site)}
                for task in snapshot.get(key,[])
            ]
        # State is already normalized to JSON values; skip FastAPI's extra tree walk.
        return JSONResponse(snapshot)

    @app.get('/api/history')
    def history(task_id: str=Query(max_length=300), hours: int=Query(default=1,ge=1,le=168), runtime=Depends(current_site)):
        # Keep both SQLite and large curve JSON encoding in FastAPI's worker pool.
        return JSONResponse({'task_id':task_id,'series':runtime.monitor.store.history(task_id,time.time()-hours*3600)})

    @app.post('/api/refresh',status_code=202)
    async def refresh(runtime=Depends(current_site)):
        return runtime.monitor.request_refresh()

    @app.post('/api/connect',status_code=202)
    async def connect(runtime=Depends(current_site)):
        return runtime.monitor.request_connect()

    async def action_body(request,fields,optional=()):
        content=await request.body()
        if len(content)>131072:raise ActionError('请求内容过长。')
        try:data=json.loads(content)
        except (ValueError,UnicodeDecodeError):raise ActionError('请求格式无效。') from None
        if not isinstance(data,dict) or not set(fields) <= set(data) <= set(fields) | set(optional):raise ActionError('请求字段不完整或包含额外配置。')
        return data

    async def perform(callback):
        try:return await callback()
        except ActionError as exc:return JSONResponse({'error':str(exc),'code':exc.code},status_code=exc.status)
        except AuthRequired:return JSONResponse({'error':'请重新连接云会话。','code':'auth_required'},status_code=403)
        except Exception:return JSONResponse({'error':'操作暂不可用，请检查连接并核对云端任务状态。','code':'source_unavailable'},status_code=503)

    async def read_only(callback, timeout=None):
        async def bounded():
            try:
                return await asyncio.wait_for(callback(), READ_TIMEOUT_SECONDS if timeout is None else timeout)
            except asyncio.TimeoutError:
                raise ActionError('云端读取超时，请稍后刷新。','source_timeout',504) from None
        return await perform(bounded)

    register_terminal_routes(app,current_site,perform,action_body,runtimes)
    register_ide_routes(app,current_site,perform,action_body)
    register_tokenhub_routes(app,tokenhub)

    @app.get('/api/task-history')
    async def task_history(runtime=Depends(current_site)):
        return await perform(runtime.task_history.snapshot)

    @app.get('/api/storage')
    async def storage(runtime=Depends(current_site)):
        return await perform(runtime.storage.snapshot)

    @app.get('/api/utilization-queue')
    async def utilization_queue(runtime=Depends(current_site)):
        return await perform(runtime.utilization_queue.snapshot)

    @app.get('/api/logs/targets')
    async def log_targets(task_id: str=Query(max_length=100),runtime=Depends(current_site)):
        return await read_only(lambda:runtime.logs.targets(task_id), min(30,READ_TIMEOUT_SECONDS))

    @app.get('/api/cci/terminal-targets')
    async def terminal_targets(task_id: str=Query(max_length=100),runtime=Depends(current_site)):
        return await perform(lambda:runtime.terminal_bridge.targets(task_id))

    @app.get('/api/acp/terminal-targets')
    async def acp_terminal_targets(task_id: str=Query(max_length=100),runtime=Depends(current_site)):
        return await perform(lambda:runtime.terminal_bridge.targets(task_id))

    @app.get('/api/logs')
    async def task_logs(task_id: str=Query(max_length=100),worker: str=Query(min_length=1,max_length=253),
                        container: str=Query(min_length=1,max_length=253),tail: int=Query(default=1000,ge=1,le=1000),runtime=Depends(current_site)):
        return await read_only(lambda:runtime.logs.read(task_id,worker,container,tail), LOG_TIMEOUT_SECONDS)

    @app.post('/api/tasks/rename')
    async def task_rename(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('task_id','expected_name','display_name','request_id'),optional=('check_only',))
            try:
                result=await asyncio.wait_for(runtime.renamer.rename(**data),75)
            except asyncio.TimeoutError:
                raise ActionError('改名结果尚未确认，请核对结果；不会自动重复提交。','outcome_unknown',504) from None
            runtime.monitor.request_refresh()
            return result
        return await perform(run)

    @app.get('/api/acp/task-config')
    async def acp_task_config(task_id: str=Query(max_length=100),runtime=Depends(current_site)):
        return await read_only(lambda:runtime.actions.task_config(task_id))

    @app.get('/api/acp/options')
    async def acp_options(partition_id: str=Query(default=None,max_length=253),runtime=Depends(current_site)):
        return await read_only((lambda:runtime.actions.options(partition_id=partition_id)) if partition_id is not None else runtime.actions.options)

    @app.post('/api/acp/preview')
    async def acp_preview(request:Request,runtime=Depends(current_site)):
        actions,monitor=runtime.actions,runtime.monitor
        async def run():
            data=await action_body(request,('display_name','replicas','gpus_per_replica','command'),optional=('cluster_id','partition_id'))
            return await actions.preview(data)
        return await read_only(run)

    @app.get('/api/acp/restart-options')
    async def acp_restart_options(task_id: str=Query(max_length=100),runtime=Depends(current_site)):
        return await read_only(lambda:runtime.actions.restart_options(task_id))

    @app.post('/api/acp/restart-preview')
    async def acp_restart_preview(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('task_id','revision','command'))
            return await runtime.actions.restart_preview(data)
        return await read_only(run)

    @app.get('/api/acp/release-restart-options')
    async def acp_release_restart_options(task_id: str=Query(max_length=100),runtime=Depends(current_site)):
        return await read_only(lambda:runtime.actions.restart_options(task_id,release=True))

    @app.post('/api/acp/release-restart-preview')
    async def acp_release_restart_preview(request:Request,runtime=Depends(current_site)):
        async def run():
            data=await action_body(request,('task_id','revision','command'))
            return await runtime.actions.restart_preview(data,release=True)
        return await read_only(run)

    @app.post('/api/acp/create')
    async def acp_create(request:Request,runtime=Depends(current_site)):
        actions,monitor=runtime.actions,runtime.monitor
        async def run():
            data=await action_body(request,('preview_id',))
            result=await actions.create(data['preview_id'])
            monitor.spawn(monitor.refresh())
            return result
        return await perform(run)

    @app.post('/api/acp/stop')
    async def acp_stop(request:Request,runtime=Depends(current_site)):
        actions,monitor=runtime.actions,runtime.monitor
        async def run():
            data=await action_body(request,('task_id','display_name','request_id'))
            result=await actions.stop(data['task_id'],data['display_name'],data['request_id'])
            monitor.spawn(monitor.refresh())
            return result
        return await perform(run)

    app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
    return app

app=create_app()
