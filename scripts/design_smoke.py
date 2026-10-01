"""Overview data and responsive design checks; all APIs are local fixtures."""
import asyncio
import functools
import re
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
    state = {'status': 'ok', 'connected': True, 'updated_at': now, 'tasks': [task], 'clusters': [{'name': 'modeltoolchain', 'available_gpus': 12}], 'waiting_tasks': [], 'source_status': {key: {'status': 'ok'} for key in ('ACP', 'CCI', 'ACP_queue', 'CCI_queue')}}
    sample_start = int(datetime.now(timezone.utc).timestamp()) - 600
    history = {'series': [
        {'metric': key, 'label': label + ' · pt-0123456789abcdef-' + worker,
         'points': [[sample_start + index * 60, 0 if key == 'gpu' else 30],
                    [sample_start + 300 + index * 60 + worker_index * 60, 0 if key == 'gpu' else 40]]}
        for worker_index, worker in enumerate(('master-0', 'worker-0'))
        for index, (key, label) in enumerate((('cpu', 'CPU'), ('gpu', 'GPU'), ('memory', '内存'), ('gpu_memory', '显存')))
    ]}
    history['series'][0]['points'].insert(1, [sample_start + 30, None])

    async def api(route):
        assert route.request.method == 'GET', 'Design checks must not mutate cloud resources'
        path = route.request.url.split('/api/')[1].split('?')[0]
        await route.fulfill(json=state if path == 'state' else history if path == 'history' else {})

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            await page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='networkidle')
            await expect(page).to_have_title('iLab Server Monitor', timeout=2000)
            for icon in await page.locator('link[rel="icon"]').all():
                response = await page.request.get(await icon.evaluate('element => element.href'))
                assert response.ok
            await expect(page.locator('link[rel="icon"]')).to_have_count(2)
            await expect(page.locator('#overview-task-list')).to_contain_text(task['name'], timeout=2000)
            await expect(page.locator('#overview-task-list img')).to_have_count(0)
            await expect(page.locator('#allocation-total')).to_have_text('8')
            await expect(page.locator('#cluster-body')).to_contain_text('12')
            await expect(page.locator('#summary-waiting')).to_have_text('0')
            await expect(page.locator('[data-metric="gpu"]')).to_contain_text('64%')
            await expect(page.locator('[data-metric="gpu"]')).to_contain_text('GPU 利用率')
            await expect(page.locator('[data-metric="gpu_memory"]')).to_contain_text('37.5%')
            await expect(page.locator('[data-metric="gpu_memory"]')).to_contain_text('显存占用')
            await page.locator('.overview-task').click()
            await expect(page.locator('#detail-title')).to_have_text(task['name'])
            await expect(page.locator('#history-chart')).to_have_attribute('aria-label', re.compile('8个图表系列'))
            option = await page.evaluate('echarts.getInstanceByDom(document.getElementById("history-chart")).getOption()')
            assert len(option['grid']) == 4
            assert sorted(series['xAxisIndex'] for series in option['series']) == [0, 0, 1, 1, 2, 2, 3, 3]
            assert option['series'][1]['data'][0][1] == 0, 'A valid zero must stay zero'
            assert option['series'][0]['data'][1][1] is None, 'Missing samples must not become zero'
            assert all(axis.get('min') == sample_start * 1000 and axis.get('max') == (sample_start + 540) * 1000 for axis in option['xAxis']), 'All four metrics need the same time scale'
            legends = await page.evaluate('''() => {
                const option = echarts.getInstanceByDom(document.getElementById('history-chart')).getOption();
                return option.series.map(series => ({full: series.name, short: option.legend[0].formatter(series.name)}));
            }''')
            assert legends[0] == {'full': 'CPU · pt-0123456789abcdef-master-0', 'short': 'CPU · master-0'}
            assert legends[5]['short'] == 'GPU 利用率 · worker-0'
            for width in (1440, 1280, 1024, 768, 390, 360):
                await page.set_viewport_size({'width': width, 'height': 900})
                for route in ('overview', 'tasks', 'acp', 'waiting'):
                    await page.locator(f'.nav-item[href="#{route}"]').click()
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, route)
                    if route == 'overview':
                        await expect(page.locator('[data-metric="gpu"]')).to_be_visible()
                        await expect(page.locator('[data-metric="gpu_memory"]')).to_be_visible()
                        if width == 390:
                            scroll = page.get_by_role('region', name='标准集群资源，可横向滚动')
                            await expect(scroll).to_have_attribute('tabindex', '0')
                            assert await scroll.evaluate('e => e.scrollWidth <= e.clientWidth + 1'), 'Phone cluster cards should fit without horizontal scrolling'
                            await expect(page.locator('#cluster-body td').nth(2)).to_have_attribute('data-label', '可用 GPU 张')
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
            state['source_status']['ACP_queue']['status'] = 'error'
            state['status'] = 'partial'
            await page.goto(f'http://127.0.0.1:{server.server_port}/#overview', wait_until='networkidle')
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#allocation-total')).to_have_text('—')
            await expect(page.locator('#allocation-note')).to_contain_text('未知')
            await expect(page.locator('#summary-waiting')).to_have_text('—')
            state['tasks'] = []
            state['source_status']['ACP']['status'] = 'ok'
            state['status'] = 'ok'
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#allocation-total')).to_have_text('0')
            await expect(page.locator('#overview-task-list')).to_contain_text('暂无运行任务')
            assert not errors, errors
            await browser.close()
            print('Design passed: eight series on shared time axes, compact legends preserving source names, zero/null integrity, labeled mobile cluster cards, snapshot aggregation, four pages at six widths, no JS errors.')
    finally:
        server.shutdown()
        server.server_close()


asyncio.run(main())
