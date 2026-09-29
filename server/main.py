"""Loopback-only dashboard API and independent background polling."""
import copy
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
from .config import DATA, ROOT
from .service import Monitor
from .store import Store
from .sites import SITES, MAOSHANWANG


@dataclass
class SiteRuntime:
    monitor: object
    actions: object
    logs: object
    site: object = MAOSHANWANG


def create_app(monitor=None, background=True, actions=None, logs=None, runtimes=None):
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
        try:
            yield
        finally:
            for runtime in runtimes.values():
                await runtime.monitor.close()
            if lock_file:
                lock_file.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.monitor=runtimes["maoshanwang"].monitor
    app.state.runtimes=runtimes

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
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.get('/')
    async def index():
        return FileResponse(ROOT/'static/index.html')

    @app.get('/api/state')
    async def state(runtime=Depends(current_site)):
        snapshot=copy.deepcopy(runtime.monitor.state)
        snapshot["site"]={"id":runtime.site.id,"console":runtime.site.console,"workspace":runtime.site.workspace}
        for task in snapshot['tasks']:
            task.pop('series',None)
            task.pop('uid',None)
            task.pop('source_id',None)
        return snapshot

    @app.get('/api/history')
    async def history(task_id: str=Query(max_length=300), hours: int=Query(default=1,ge=1,le=168), runtime=Depends(current_site)):
        return {'task_id':task_id,'series':runtime.monitor.store.history(task_id,time.time()-hours*3600)}

    @app.post('/api/refresh',status_code=202)
    async def refresh(runtime=Depends(current_site)):
        monitor=runtime.monitor
        if not monitor.lock.locked():
            monitor.spawn(monitor.refresh())
        return {'message':'已请求采集，完成后自动更新。'}

    @app.post('/api/connect',status_code=202)
    async def connect(runtime=Depends(current_site)):
        monitor=runtime.monitor
        if monitor.connecting is None or monitor.connecting.done():
            monitor.connecting=monitor.spawn(monitor.connect())
        return {'message':'正在使用本机保存的 IAM 凭据自动连接，无需手动输入。'}

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

    @app.get('/api/logs/targets')
    async def log_targets(task_id: str=Query(max_length=100),runtime=Depends(current_site)):
        return await perform(lambda:runtime.logs.targets(task_id))

    @app.get('/api/logs')
    async def task_logs(task_id: str=Query(max_length=100),worker: str=Query(min_length=1,max_length=253),
                        container: str=Query(min_length=1,max_length=253),tail: int=Query(default=1000,ge=1,le=1000),runtime=Depends(current_site)):
        return await perform(lambda:runtime.logs.read(task_id,worker,container,tail))

    @app.get('/api/acp/options')
    async def acp_options(partition_id: str=Query(default=None,max_length=253),runtime=Depends(current_site)):
        return await perform((lambda:runtime.actions.options(partition_id=partition_id)) if partition_id is not None else runtime.actions.options)

    @app.post('/api/acp/preview')
    async def acp_preview(request:Request,runtime=Depends(current_site)):
        actions,monitor=runtime.actions,runtime.monitor
        async def run():
            data=await action_body(request,('display_name','replicas','gpus_per_replica','command'),optional=('cluster_id','partition_id'))
            return await actions.preview(data)
        return await perform(run)

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
