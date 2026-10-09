"""Fixed TokenHub reads and explicit, validated key group edits."""
from typing import Annotated, Optional
from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .tokenhub_auth import TokenHubError


PositiveId = Annotated[StrictInt, Field(gt=0)]


class KeyGroupUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key_id: PositiveId
    group_id: PositiveId
    expected_group_id: Optional[PositiveId]
    account_id: PositiveId


class KeyCopyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key_id: PositiveId
    account_id: PositiveId


def register_tokenhub_routes(app, hub):
    async def perform(callback):
        try:
            return JSONResponse(await callback())
        except TokenHubError as exc:
            return JSONResponse({'error': str(exc), 'code': exc.code}, status_code=exc.status)
        except Exception:
            return JSONResponse({'error': 'TokenHub 暂不可用，请稍后重试。', 'code': 'source_unavailable'}, status_code=503)

    def local_login(request):
        if request.headers.get('x-monitor-device'):
            return JSONResponse({'error': '请在运行监控服务的电脑上连接 TokenHub。'}, status_code=403)

    @app.get('/api/tokenhub/state')
    async def state(request: Request, refresh: bool = False):
        async def snapshot():
            data = await hub.snapshot(force=refresh)
            return {**data, 'can_connect': not bool(request.headers.get('x-monitor-device'))}
        return await perform(snapshot)

    @app.get('/api/tokenhub/keys')
    async def keys(page: int = Query(default=1, ge=1, le=10000)):
        return await perform(lambda: hub.keys(page=page))

    @app.get('/api/tokenhub/key-groups')
    async def key_groups(key_id: int = Query(gt=0)):
        return await perform(lambda: hub.key_groups(key_id))

    @app.post('/api/tokenhub/key-copy')
    async def key_copy(body: KeyCopyRequest):
        return await perform(lambda: hub.copy_key(**body.model_dump()))

    @app.post('/api/tokenhub/key-group')
    async def key_group(body: KeyGroupUpdate):
        return await perform(lambda: hub.update_key_group(**body.model_dump()))

    @app.get('/api/tokenhub/trend')
    async def trend(days: int = Query(default=7, ge=7, le=30),
                    granularity: str = Query(default='day', pattern='^(day|hour)$')):
        if granularity == 'hour':
            return await perform(hub.hourly_trend)
        if days not in (7, 30):
            return JSONResponse({'error': '请选择 7 天或 30 天。'}, status_code=422)
        return await perform(lambda: hub.trend(days=days))

    @app.get('/api/tokenhub/usage')
    async def usage(start_date: str = Query(max_length=10), end_date: str = Query(max_length=10), page: int = Query(default=1, ge=1, le=10000)):
        return await perform(lambda: hub.usage(start_date=start_date, end_date=end_date, page=page))

    @app.post('/api/tokenhub/connect', status_code=202)
    async def connect(request: Request):
        denied = local_login(request)
        if denied is not None:
            return denied
        return hub.connect()

    @app.post('/api/tokenhub/disconnect')
    async def disconnect(request: Request):
        denied = local_login(request)
        if denied is not None:
            return denied
        return await perform(hub.disconnect)
