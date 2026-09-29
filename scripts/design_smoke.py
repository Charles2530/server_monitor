"""Overview data and responsive design checks; all APIs are local fixtures."""
import asyncio
import functools
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect
from v2_browser_smoke import Handler, ROOT


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    now = datetime.now(timezone.utc).isoformat()
    task = {'id': 'ACP:test', 'name': 'charles <img src=x onerror=alert(1)>', 'kind': 'ACP', 'cluster': 'modeltoolchain', 'state': 'running', 'gpu_count': 8, 'replicas': 1, 'updated_at': now, 'metrics': {'gpu': 64, 'gpu_memory': 37.5}}
    state = {'status': 'ok', 'connected': True, 'updated_at': now, 'tasks': [task], 'clusters': [{'name': 'modeltoolchain', 'available_gpus': 12}], 'waiting_tasks': [], 'source_status': {'ACP': {'status': 'ok'}, 'CCI': {'status': 'ok'}}}

    async def api(route):
        assert route.request.method == 'GET', 'Design checks must not mutate cloud resources'
        path = route.request.url.split('/api/')[1].split('?')[0]
        await route.fulfill(json=state if path == 'state' else {'series': []} if path == 'history' else {})

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            await page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='networkidle')
            await expect(page.locator('#overview-task-list')).to_contain_text(task['name'], timeout=2000)
            await expect(page.locator('#overview-task-list img')).to_have_count(0)
            await expect(page.locator('#allocation-total')).to_have_text('8')
            await expect(page.locator('#overview-resources')).to_contain_text('12')
            await expect(page.locator('[data-metric="gpu"]')).to_contain_text('64%')
            await expect(page.locator('[data-metric="gpu"]')).to_contain_text('GPU 利用率')
            await expect(page.locator('[data-metric="gpu_memory"]')).to_contain_text('37.5%')
            await expect(page.locator('[data-metric="gpu_memory"]')).to_contain_text('显存占用')
            await page.locator('.overview-task').click()
            await expect(page.locator('#detail-title')).to_have_text(task['name'])
            for width in (1440, 1280, 1024, 768, 390, 360):
                await page.set_viewport_size({'width': width, 'height': 900})
                for route in ('overview', 'tasks', 'acp', 'waiting'):
                    await page.locator(f'.nav-item[href="#{route}"]').click()
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, route)
                    if route == 'overview':
                        await expect(page.locator('[data-metric="gpu"]')).to_be_visible()
                        await expect(page.locator('[data-metric="gpu_memory"]')).to_be_visible()
                    await expect(page.locator('.workspace-view:visible')).to_have_count(1)
            task['metrics']['gpu'] = 0
            task['metrics']['gpu_memory'] = None
            task['failed_metrics'] = ['gpu_memory']
            await page.goto(f'http://127.0.0.1:{server.server_port}/#overview', wait_until='networkidle')
            await page.reload(wait_until='networkidle')
            await expect(page.locator('[data-metric="gpu"] strong')).to_have_text('0%')
            await expect(page.locator('[data-metric="gpu"]')).not_to_contain_text('旧快照')
            await expect(page.locator('[data-metric="gpu_memory"] strong')).to_have_text('—')
            await expect(page.locator('[data-metric="gpu_memory"]')).to_contain_text('旧快照')
            state['source_status']['ACP']['status'] = 'error'
            state['status'] = 'partial'
            await page.goto(f'http://127.0.0.1:{server.server_port}/#overview', wait_until='networkidle')
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#allocation-total')).to_have_text('—')
            await expect(page.locator('#allocation-note')).to_contain_text('未知')
            state['tasks'] = []
            state['source_status']['ACP']['status'] = 'ok'
            state['status'] = 'ok'
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#allocation-total')).to_have_text('0')
            await expect(page.locator('#overview-task-list')).to_contain_text('暂无运行任务')
            assert not errors, errors
            await browser.close()
            print('Design passed: real snapshot aggregation, safe task names, task selection, unknown vs zero, four pages at six widths, no JS errors.')
    finally:
        server.shutdown()
        server.server_close()


asyncio.run(main())
