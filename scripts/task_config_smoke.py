"""Read-only ACP configuration modal checks; no cloud writes."""
import asyncio
import functools
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect
from v2_browser_smoke import Handler

ROOT = Path(__file__).resolve().parent.parent

async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tasks = [{'id': 'ACP:example', 'kind': 'ACP', 'name': 'charles selected', 'state': 'running', 'metrics': {}, 'can_stop': True}]
    snapshot = {'status': 'ok', 'connected': True, 'updated_at': datetime.now(timezone.utc).isoformat(), 'interval_seconds': 60, 'tasks': tasks, 'waiting_tasks': [], 'clusters': [], 'source_status': {'CCI': {'status': 'ok'}, 'ACP': {'status': 'ok'}}}
    command = "echo '<img src=x onerror=alert(1)>'\npython train.py --path " + 'long/' * 100 + '\nsleep inf\n'
    calls = []
    mode = {'error': False, 'mismatch': False, 'slow': False}
    gate = asyncio.Event()

    async def api(route):
        assert route.request.method == 'GET'
        url = urlsplit(route.request.url)
        if url.path == '/api/state': data = snapshot
        elif url.path == '/api/history': data = {'series': []}
        elif url.path == '/api/acp/task-config':
            calls.append(route.request.headers.get('x-monitor-site'))
            if mode['slow']: await gate.wait()
            if mode['error']:
                await route.fulfill(status=503, json={'code': 'unavailable'}); return
            data = {'site': calls[-1], 'task_id': 'ACP:wrong' if mode['mismatch'] else parse_qs(url.query)['task_id'][0], 'name': 'charles selected', 'cluster': 'modeltoolchain-test', 'zone': 'ms-sc-01a', 'framework': 'PYTORCH', 'state': 'RUNNING', 'roles': [{'name': 'Worker', 'replicas': 1, 'image': 'registry/' + 'image_' * 100, 'command': command, 'resources': [{'name': 'gpu-spec'}]}, {'name': 'Evaluator', 'replicas': 0, 'image': 'eval:latest', 'command': '', 'resources': []}], 'afs_mounts': [{'id': 'disk', 'zone': 'a', 'subdir': '/datasets', 'mount_path': '/mnt/data'}, {'id': 'disk2', 'zone': 'a', 'subdir': '/', 'mount_path': '/mnt/other'}]}
        else: data = {}
        await route.fulfill(json=data)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1440, 'height': 960})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            base = f'http://127.0.0.1:{server.server_port}/'
            for site in ('maoshanwang', 'sensecore'):
                await page.goto(base + '?site=' + site + '#tasks', wait_until='domcontentloaded')
                button = page.locator('#task-config-open')
                await expect(button).to_be_visible()
                before = len(calls)
                assert before == (0 if site == 'maoshanwang' else 1)
                await button.click()
                dialog = page.locator('#task-config-dialog')
                await expect(dialog.locator('code').first).to_have_text(command)
                assert calls[-1] == site
                assert await dialog.locator('img,script').count() == 0
                await expect(dialog).to_contain_text('/mnt/other')
                await expect(dialog).to_contain_text('此角色的启动命令为空')
                await page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied=text}}})")
                await dialog.get_by_role('button', name='复制命令').first.click()
                assert await page.evaluate('window.copied') == command
                await dialog.get_by_role('button', name='自动换行').first.click()
                await expect(dialog.locator('.task-config-gutter').first).to_be_visible()
                await page.evaluate("document.dispatchEvent(new CustomEvent('monitor-selection',{detail:{id:'ACP:example',kind:'ACP',name:'charles selected'}}))")
                await expect(dialog).to_be_visible()
                await page.set_viewport_size({'width':390,'height':844})
                assert await dialog.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
                await page.screenshot(path=str(ROOT/'data/task-config-mobile.png'))
                await page.keyboard.press('Escape')
                await expect(dialog).not_to_be_visible()
                await page.set_viewport_size({'width':1440,'height':960})
            mode['error'] = True
            await button.click()
            await expect(dialog).to_contain_text('读取配置失败')
            mode['error'] = False
            await dialog.get_by_role('button', name='重新读取').click()
            await expect(dialog.locator('code').first).to_have_text(command)
            await page.screenshot(path=str(ROOT/'data/task-config-desktop.png'))
            await dialog.get_by_role('button', name='关闭任务配置').click()
            mode['mismatch'] = True
            await button.click()
            await expect(dialog).to_contain_text('不匹配')
            await page.keyboard.press('Escape')
            mode['mismatch'] = False
            mode['slow'] = True
            await button.click()
            await expect(dialog).to_contain_text('正在读取')
            await page.evaluate("document.dispatchEvent(new CustomEvent('monitor-selection',{detail:{id:'CCI:other',kind:'CCI',name:'charles CCI'}}))")
            await expect(dialog).not_to_be_visible()
            await expect(button).not_to_be_visible()
            gate.set()
            await page.wait_for_timeout(100)
            assert not errors, errors
            await browser.close()
            print('PASS: two sites, selected task, read-only, safe text, copy, multi-role/AFS, empty command, retries, identity checks, stale request, Escape, mobile layout')
    finally:
        gate.set(); server.shutdown()

if __name__ == '__main__': asyncio.run(main())
