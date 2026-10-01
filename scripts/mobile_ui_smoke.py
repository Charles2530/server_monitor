"""Phone layout checks with fixture APIs only; never submits cloud operations."""
import asyncio
import functools
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect
from v2_browser_smoke import Handler, ROOT


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    now = datetime.now(timezone.utc).isoformat()
    name = 'kill_before_notice_grh_charles_' + 'long_training_name_' * 6
    task = dict(id='ACP:test', name=name, kind='ACP', cluster='modeltoolchain_test', state='running',
                gpu_count=8, replicas=1, can_stop=True, updated_at=now, created_at=now,
                metrics={'gpu': 63, 'gpu_memory': 42, 'cpu': 24, 'memory': 35})
    state = dict(status='ok', connected=True, updated_at=now, tasks=[task],
                 waiting_tasks=[dict(task, id=f'ACP:waiting-{i}', state='WAIT_QUOTA', state_label='等待配额', owner='test-submitter', observed_at=now) for i in range(4)],
                 clusters=[dict(name='modeltoolchain_test', available_gpus=12, available_vcpu=128, available_memory_gib=4096,
                                updated_at=now, inventory=dict(total_gpus=160, node_count=20, disabled_nodes=0, abnormal_nodes=0, updated_at=now))],
                 source_status={k: {'status': 'ok', 'updated_at': now} for k in ('CCI', 'ACP', 'CCI_queue', 'ACP_queue')})

    async def api(route):
        assert route.request.method == 'GET', 'UI validation must not submit operations'
        path = urlparse(route.request.url).path
        site = route.request.headers.get('x-monitor-site', 'maoshanwang')
        if path == '/api/state': data = state
        elif path == '/api/history': data = {'series': []}
        elif path == '/api/logs/targets': data = {'task_id': 'ACP:test', 'workers': [{'name': 'worker-0', 'containers': ['trainer']}]}
        elif path == '/api/logs': data = {'task_id': 'ACP:test', 'worker': 'worker-0', 'container': 'trainer', 'text': 'training log ' * 70, 'entries': 1, 'updated_at': now}
        elif path == '/api/acp/options':
            data = {'template': {'id': 'ACP:test', 'name': name, 'created_at': now}, 'gpu_options': [1, 2, 4, 8],
                    'clusters': [{'id': 'cluster-test', 'name': 'modeltoolchain_test', 'gpu_options': [1, 2, 4, 8]}],
                    'template_config': {'images': ['registry/' + 'long-path/' * 10 + ':latest'], 'afs_mounts': []}}
        elif path == '/api/storage':
            data = {'volumes': [{'id': 'afs-test', 'name': 'shared-storage-' * 8, 'zone': 'cn-sh-01a', 'state': 'ACTIVE',
                               'total_bytes': 2e12, 'used_bytes': 1e12, 'free_bytes': 1e12, 'used_percent': 50,
                               'mounts': [{'kind': 'ACP', 'task_name': name, 'task_state': 'RUNNING',
                                           'subdir': '/data/' + 'long-path/' * 12, 'mount_path': '/mnt/data'}]}],
                    'site': site, 'status': 'ok', 'warnings': [], 'mount_status': {'CCI': 'ok', 'ACP': 'ok'}, 'updated_at': now}
        elif path == '/api/task-history':
            data = {'site': site, 'tasks': [dict(task, status='failed', status_label='失败', started_at=now,
                    ended_at=now, duration_seconds=96000, url='https://console.ms-sc-01.maoshanwangtech.com/acp/list/detail?id=test')],
                    'source_status': {'ACP': {'status': 'ok', 'updated_at': now}}, 'updated_at': now}
        else: data = {}
        await route.fulfill(json=data)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 393, 'height': 780})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            await page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='networkidle')
            await expect(page.locator('#summary-tasks')).to_have_text('1')
            await page.evaluate((ROOT / 'static/mobile.js').read_text())
            await expect(page.locator('#mobile-task-back')).to_have_count(1)
            for width in (320, 393, 430):
                await page.set_viewport_size({'width': width, 'height': 780})
                assert await page.locator('.sidebar nav').evaluate('e => getComputedStyle(e).position') == 'fixed', 'Phone navigation should be fixed at the bottom'
                for route_name in ('overview', 'tasks', 'acp', 'waiting', 'storage', 'history'):
                    nav = page.locator(f'.nav-item[href="#{route_name}"]')
                    box = await nav.bounding_box()
                    assert box and box['y'] >= 690 and box['x'] >= 0 and box['x'] + box['width'] <= width + 1, (width, route_name, box)
                    await nav.click()
                    await expect(page.locator('.workspace-view:visible')).to_have_count(1)
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, route_name, 'page overflow')
                    if route_name in ('overview', 'waiting', 'storage'):
                        body = {'overview': '#cluster-body', 'waiting': '#waiting-body', 'storage': '#storage-body'}[route_name]
                        await expect(page.locator(body)).to_contain_text({'overview': 'modeltoolchain_test', 'waiting': 'test-submitter', 'storage': 'afs-test'}[route_name])
                        await expect(page.locator(body + ' tr').first).to_be_visible()
                        assert await page.locator(body).evaluate('e => e.closest(".table-scroll").scrollWidth <= e.closest(".table-scroll").clientWidth + 1'), (width, route_name, 'table overflow')
                        assert await page.locator(body).evaluate('e => e.getBoundingClientRect().bottom <= e.closest(".cluster-panel").getBoundingClientRect().bottom'), (width, route_name, 'last card is clipped')
                        assert await page.locator(body + ' td[data-label]').count() > 0, 'Cards retain field labels'
                    if width == 393:
                        await page.screenshot(path=str(ROOT / f'data/mobile-ui-{route_name}.png'), full_page=True)
                    if route_name == 'history':
                        await expect(page.locator('#task-history-body')).to_contain_text('失败')
                        await expect(page.locator('#task-history-body .button')).to_have_text('重新启动')
                await page.locator('.nav-item[href="#tasks"]').click()
                await page.locator('.task-card').first.click()
                await expect(page.locator('#mobile-task-back')).to_be_in_viewport()
                await expect(page.locator('#detail-title')).to_have_text(name)
                await page.locator('#detail-tab-logs').click()
                await expect(page.locator('#logs-output')).to_contain_text('training log')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Long log lines should scroll within the log panel'
                await page.locator('.nav-item[href="#overview"]').click()
                await page.locator('.nav-item[href="#tasks"]').click()
                await expect(page.locator('#detail-tab-monitor')).to_have_attribute('aria-selected', 'true')
                await expect(page.locator('#detail-panel')).not_to_be_visible()
                await page.locator('.task-card').first.click()
                await page.locator('#detail-tab-logs').click()
                await page.locator('#mobile-task-back').click()
                await expect(page.locator('.task-card').first).to_be_visible()
                await expect(page.locator('#detail-tab-monitor')).to_have_attribute('aria-selected', 'true')
            # The on-screen keyboard reduces available height. Dialog content must still scroll.
            await page.set_viewport_size({'width': 320, 'height': 430})
            await page.evaluate('''() => {
                const dialog = document.getElementById('acp-create-dialog');
                const command = document.createElement('textarea'); command.className = 'restart-command';
                command.value = 'python training.py ' + '--argument long-value '.repeat(30);
                dialog.prepend(command); dialog.showModal();
            }''')
            dialog_box = await page.locator('#acp-create-dialog').bounding_box()
            assert dialog_box['x'] >= 0 and dialog_box['x'] + dialog_box['width'] <= 320
            assert dialog_box['y'] >= 0 and dialog_box['y'] + dialog_box['height'] <= 430
            await page.locator('#acp-create-cancel').click()
            await page.set_viewport_size({'width': 1440, 'height': 1000})
            await page.locator('.nav-item[href="#overview"]').click()
            assert await page.locator('.sidebar nav').evaluate('e => getComputedStyle(e).position') != 'fixed'
            assert await page.locator('#cluster-body').evaluate('e => getComputedStyle(e).display') == 'table-row-group'
            await page.screenshot(path=str(ROOT / 'data/mobile-ui-desktop.png'), full_page=True)
            assert not errors, errors
            await browser.close()
            print('Mobile UI passed: six routes, bottom navigation, labeled cards, long content, detail navigation, desktop preservation; no cloud writes.')
    finally:
        server.shutdown()
        server.server_close()


asyncio.run(main())
