"""Optional authenticated gateway. The existing monitor remains loopback-only."""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,Response
from .mobile_access import AccessError,DeviceStore
from .store import store_call

READ_PATHS=frozenset(('/', '/terminal', '/api/state','/api/history','/api/task-history','/api/storage',
    '/api/tokenhub/state','/api/tokenhub/keys','/api/tokenhub/usage','/api/tokenhub/trend','/api/tokenhub/key-groups',
    '/api/logs','/api/logs/targets','/api/cci/terminal-targets','/api/acp/terminal-targets',
    '/api/acp/options','/api/acp/task-config','/api/acp/restart-options','/api/acp/release-restart-options'))
WRITE_PATHS=frozenset(('/api/connect','/api/refresh','/api/acp/preview','/api/acp/create','/api/acp/stop',
    '/api/tokenhub/key-group',
    '/api/acp/restart-preview','/api/acp/release-restart-preview',
    '/api/terminal/open','/api/terminal/targets','/api/terminal/status','/api/terminal/read','/api/terminal/write','/api/terminal/close'))
MAX_BODY=131072
MAX_RESPONSE=32*1024*1024


def allowed_path(path,method):
    if method=='POST':return path in WRITE_PATHS
    if method!='GET':return False
    return path in READ_PATHS or (path.startswith('/static/') and all(part not in ('','.','..') for part in path.split('/')[2:]) and '\\' not in path and '%' not in path)


def default_store():
    root=Path(os.environ.get('MONITOR_MOBILE_DATA',str(Path(__file__).resolve().parents[1]/'data/mobile'))).expanduser()
    return DeviceStore(root/'devices.sqlite3')


def bearer(request):
    header=request.headers.get('authorization','')
    return header[7:] if header.startswith('Bearer ') else ''


async def bounded_body(request):
    result=bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result)>MAX_BODY:return None
    return bytes(result)


def create_gateway(store=None,*,transport=None):
    store=store or default_store()
    async def strip_cookies(request):
        request.headers.pop('cookie',None)

    @asynccontextmanager
    async def lifespan(app):
        # Fixed destination and no environment proxy. Never accept an upstream URL from a client.
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8766',timeout=95,follow_redirects=False,trust_env=False,transport=transport,event_hooks={'request':[strip_cookies]}) as client:
            app.state.upstream=client
            yield
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        return response

    @app.get('/health')
    async def health():return {'service':'charles-mobile-gateway','protocol':1}

    @app.post('/mobile/pair')
    async def pair(request:Request):
        body=await bounded_body(request)
        if body is None:return JSONResponse({'error':'请求过长。'},status_code=413)
        try:
            data=json.loads(body)
            if not isinstance(data,dict) or set(data)!={'ticket','name'}:raise ValueError()
            return await store_call(store.redeem,data['ticket'],data['name'])
        except (ValueError,UnicodeDecodeError):return JSONResponse({'error':'配对格式无效。'},status_code=400)
        except AccessError as exc:return JSONResponse({'error':str(exc),'code':'mobile_access_denied'},status_code=403)

    @app.get('/mobile/status')
    async def status(request:Request):
        try:return await store_call(store.status,bearer(request))
        except AccessError as exc:return JSONResponse({'error':str(exc),'code':'mobile_auth_required'},status_code=401)

    @app.api_route('/{path:path}',methods=['GET','POST','PUT','DELETE','PATCH','HEAD','OPTIONS'])
    async def forward(request:Request,path:str):
        token=bearer(request)
        try:await store_call(store.status,token)
        except AccessError as exc:return JSONResponse({'error':str(exc),'code':'mobile_auth_required'},status_code=401)
        path='/'+path
        if not allowed_path(path,request.method):return JSONResponse({'error':'未开放此接口。'},status_code=404)
        try:device=await store_call(store.authorize,token,write=request.method=='POST')
        except AccessError as exc:return JSONResponse({'error':str(exc),'code':'mobile_access_denied'},status_code=403)
        site=request.headers.get('x-monitor-site','maoshanwang')
        if site not in ('maoshanwang','sensecore'):return JSONResponse({'error':'未知站点。'},status_code=400)
        body=await bounded_body(request)
        if body is None:return JSONResponse({'error':'请求过长。'},status_code=413)
        query=request.scope.get('query_string',b'')
        if len(query)>8192:return JSONResponse({'error':'查询过长。'},status_code=413)
        headers={'X-Monitor-Site':site,'X-Monitor-Request':'1','Content-Type':'application/json','Host':'127.0.0.1:8766','X-Monitor-Device':device['id']}
        try:
            # Do not forward cookies, caller Authorization, Host, Origin, or forwarding headers.
            async with app.state.upstream.stream(request.method,path+('?' + query.decode('ascii') if query else ''),headers=headers,content=body) as response:
                if 300<=response.status_code<400:return JSONResponse({'error':'后台返回了非预期跳转。'},status_code=502)
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>MAX_RESPONSE:raise ValueError('response too large')
                forwarded={k:v for k,v in response.headers.items() if k in ('content-type','content-security-policy')}
                return Response(bytes(data),status_code=response.status_code,headers=forwarded)
        except (httpx.HTTPError,ValueError,UnicodeDecodeError):
            message='后台未响应；本次操作结果未知，请连接恢复后核对任务，勿重复提交。' if request.method=='POST' else '电脑上的监控后台暂不可用，请稍后重试。'
            return JSONResponse({'error':message,'code':'backend_unavailable'},status_code=503)
    return app
