"""Local, read-only W&B data routes and task associations."""
import time

from fastapi import Query, Request

from .wandb_client import WandbError


def register_wandb_routes(app, client, store, perform, read_only, action_body):
    def cached_read(loader, fallback, save):
        async def read():
            try:
                data = await loader()
            except WandbError:
                cached = fallback()
                if cached is not None and cached != []:
                    return cached
                raise
            save(data)
            return data
        return read

    def site_id(request, requested=None):
        value = requested or request.headers.get('x-monitor-site', 'maoshanwang')
        if value not in ('maoshanwang', 'sensecore'):
            raise WandbError('站点无效。', 'invalid_site', 400)
        return value

    @app.get('/api/wandb/status')
    async def wandb_status():
        return client.status()

    @app.post('/api/wandb/refresh', status_code=202)
    async def wandb_refresh():
        return {'accepted': True}

    @app.get('/api/wandb/projects')
    async def wandb_projects(entity: str = Query(default='', max_length=120), limit: int = Query(default=100, ge=1, le=200)):
        resolved_entity = (entity or getattr(client, 'entity', '')).strip()
        return await read_only(cached_read(
            lambda: client.projects(resolved_entity, limit),
            lambda: store.wandb_projects(resolved_entity)[:limit],
            lambda data: store.wandb_upsert_projects(data, time.time()),
        ), timeout=60)

    @app.get('/api/wandb/runs')
    async def wandb_runs(entity: str = Query(..., min_length=1, max_length=120), project: str = Query(..., min_length=1, max_length=200),
                         state: str = Query(default='', max_length=40), search: str = Query(default='', max_length=120),
                         limit: int = Query(default=100, ge=1, le=200)):
        return await read_only(cached_read(
            lambda: client.runs(entity, project, state=state or None, search=search or None, limit=limit),
            lambda: store.wandb_runs(entity, project)[:limit],
            lambda data: store.wandb_upsert_runs(data, time.time()),
        ), timeout=80)

    @app.get('/api/wandb/run')
    async def wandb_run(entity: str = Query(..., min_length=1, max_length=120), project: str = Query(..., min_length=1, max_length=200),
                        run_id: str = Query(..., min_length=1, max_length=120)):
        return await read_only(cached_read(
            lambda: client.run(entity, project, run_id),
            lambda: store.wandb_run(entity, project, run_id),
            lambda data: store.wandb_upsert_runs([data], time.time()),
        ), timeout=60)

    @app.get('/api/wandb/history')
    async def wandb_history(entity: str = Query(..., min_length=1, max_length=120), project: str = Query(..., min_length=1, max_length=200),
                            run_id: str = Query(..., min_length=1, max_length=120), keys: str = Query(default='', max_length=2000),
                            samples: int = Query(default=500, ge=1, le=2000)):
        metric_keys = [item.strip() for item in keys.split(',') if item.strip()][:100] or None
        return await read_only(cached_read(
            lambda: client.history(entity, project, run_id, metric_keys, samples),
            lambda: store.wandb_history(entity, project, run_id),
            lambda data: store.wandb_save_history(entity, project, run_id, data, time.time()),
        ), timeout=90)

    @app.get('/api/wandb/logs')
    async def wandb_logs(entity: str = Query(..., min_length=1, max_length=120), project: str = Query(..., min_length=1, max_length=200),
                         run_id: str = Query(..., min_length=1, max_length=120), tail: int = Query(default=1000, ge=1, le=2000)):
        return await read_only(cached_read(
            lambda: client.logs(entity, project, run_id, tail),
            lambda: store.wandb_logs(entity, project, run_id),
            lambda data: store.wandb_save_logs(entity, project, run_id, data, time.time()),
        ), timeout=20)

    @app.get('/api/wandb/task-links')
    async def wandb_task_links(request: Request, task_id: str = Query(default=None, max_length=320), site: str = Query(default=None, max_length=40)):
        return store.wandb_links(site_id(request, site), task_id)

    @app.post('/api/wandb/task-links')
    async def wandb_task_link(request: Request):
        async def save():
            data = await action_body(request, ('task_id', 'entity', 'project', 'run_id'))
            if not all(isinstance(data[key], str) and data[key].strip() for key in ('task_id', 'entity', 'project', 'run_id')):
                raise WandbError('关联字段不能为空。', 'invalid_body', 400)
            if len(data['task_id']) > 320 or not data['task_id'].startswith(('CCI:', 'ACP:')):
                raise WandbError('任务 ID 无效。', 'invalid_body', 400)
            return store.wandb_add_link(site_id(request), data['task_id'], data['entity'].strip(), data['project'].strip(), data['run_id'].strip(), time.time())
        return await perform(save)

    @app.delete('/api/wandb/task-links/{link_id}')
    async def wandb_task_link_delete(request: Request, link_id: int):
        async def remove():
            if link_id <= 0:
                raise WandbError('关联 ID 无效。', 'invalid_body', 400)
            store.wandb_delete_link(site_id(request), link_id)
            return {'deleted': True}
        return await perform(remove)
