"""Desktop localhost entry points, intentionally absent from the mobile gateway."""
from urllib.parse import urlsplit
from fastapi import Depends, Request
from .acp_actions import ActionError


def register_ide_routes(app, current_site, perform, action_body):
    def origin(request):
        value = str(request.base_url).rstrip('/')
        if urlsplit(value).scheme != 'http' or urlsplit(value).hostname not in ('127.0.0.1', 'localhost'):
            raise ActionError('站内 VS Code 仅支持此电脑的 localhost 页面。')
        return value

    @app.post('/api/cci/ide/targets')
    async def targets(request: Request, runtime=Depends(current_site)):
        async def run():
            data = await action_body(request, ('task_id',))
            return await runtime.ide.begin('targets', data, origin(request))
        return await perform(run)

    @app.post('/api/cci/ide/open')
    async def open_editor(request: Request, runtime=Depends(current_site)):
        async def run():
            data = await action_body(request, ('task_id', 'instance', 'container'))
            return await runtime.ide.begin('open', data, origin(request))
        return await perform(run)

    @app.post('/api/cci/ide/recover')
    async def recover_editor(request: Request, runtime=Depends(current_site)):
        async def run():
            data = await action_body(request, ('task_id', 'instance', 'container'))
            return await runtime.ide.begin('recover', data, origin(request))
        return await perform(run)

    @app.post('/api/cci/ide/status')
    async def status(request: Request, runtime=Depends(current_site)):
        async def run():
            data = await action_body(request, ('request_id',))
            return await runtime.ide.status(data['request_id'], origin(request))
        return await perform(run)
