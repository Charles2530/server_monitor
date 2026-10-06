"""Local command editor/log-console regression checks. All API traffic is mocked."""
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
    now = datetime.now(timezone.utc).isoformat()
    tasks = [{'id': f'{kind}:{index}', 'kind': kind, 'name': f'charles {kind}', 'state': 'running', 'metrics': {}, 'can_stop': kind == 'ACP'} for index, kind in enumerate(('ACP', 'CCI'))]
    snapshot = {'status': 'ok', 'connected': True, 'updated_at': now, 'interval_seconds': 60, 'tasks': tasks, 'waiting_tasks': [], 'clusters': [], 'source_status': {'CCI': {'status': 'ok'}, 'ACP': {'status': 'ok'}}}
    options_gate = asyncio.Event()
    calls = []
    mode = {'slow': False, 'error': False, 'empty': False, 'fallback': False, 'targets_error': False, 'options_error': False, 'long': False, 'mismatch': False, 'replacement': False, 'all_empty': False}
    stale_gate = asyncio.Event()

    async def api(route):
        request = route.request
        assert request.method == 'GET', 'Read-only smoke must never mutate cloud state'
        url = urlsplit(request.url)
        path = url.path
        query = {key: value[0] for key, value in parse_qs(url.query).items()}
        calls.append((path, query))
        if path == '/api/state': data = snapshot
        elif path == '/api/tokenhub/state': data = {'status': 'disconnected', 'connected': False, 'sections': {}}
        elif path == '/api/history': data = {'series': []}
        elif path == '/api/acp/options':
            await options_gate.wait()
            if mode['options_error']:
                await route.fulfill(status=503, json={'code': 'template_unavailable', 'error': 'unavailable'})
                return
            data = {'template': {'id': 'ACP:template', 'name': 'charles template', 'state': 'RUNNING'}, 'gpu_options': [1, 2, 4, 8], 'default_gpus_per_replica': 1, 'default_command': 'sleep inf' if mode['fallback'] else 'echo template\npython train.py\nsleep inf', 'command_source': None if mode['fallback'] else {'id': 'ACP:template', 'name': 'charles template'}}
        elif path == '/api/logs/targets':
            if mode['targets_error']:
                await route.fulfill(status=503, json={'code': 'logs_unavailable', 'error': 'unavailable'})
                return
            data = {'task_id': query['task_id'], 'kind': query['task_id'].split(':')[0], 'name': 'charles', 'workers': [{'name': 'worker-new' if mode['replacement'] else 'worker-0', 'state': 'RUNNING', 'containers': ['main', 'sidecar']}, {'name': 'worker-1', 'state': 'RUNNING', 'containers': ['main']}], 'updated_at': now}
            data['workers'].insert(0, {'name': 'empty-worker', 'state': 'STARTING', 'containers': []})
            if mode['all_empty']:
                for worker in data['workers']: worker['containers'] = []
        elif path == '/api/logs':
            assert query['tail'] == '1000'
            if mode['mismatch']:
                await route.fulfill(status=409, json={'code': 'target_mismatch', 'error': 'changed'})
                return
            if mode['slow']:
                mode['slow'] = False
                await stale_gate.wait()
                data = {**query, 'text': 'STALE TARGET RESPONSE', 'entries': 1, 'updated_at': now}
            elif mode['error']:
                await route.fulfill(status=503, json={'code': 'auth_required', 'error': 'session expired'})
                return
            else:
                data = {**query, 'text': '' if mode['empty'] else f"{query['task_id']} {query['worker']} {query['container']}\n<img src=x onerror=alert(1)>\nlast line" + ('\nlong line' * 800 if mode['long'] else ''), 'entries': 0 if mode['empty'] else 3, 'updated_at': now, 'truncated': False, 'eof': True}
        else: raise AssertionError(path)
        await route.fulfill(json=data)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1280, 'height': 720})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.add_init_script("""window.logTimers = []; const originalTimeout = window.setTimeout; window.setTimeout = (fn, ms, ...args) => { if (ms === 10000) window.logTimers.push(fn); window.lastTimeout = ms; return originalTimeout(fn, ms, ...args); };""")
            await page.route('**/api/**', api)
            base = f'http://127.0.0.1:{server.server_port}/'
            await page.goto(base + '#acp', wait_until='domcontentloaded')
            await page.locator('#acp-command').fill('echo my draft\npython mine.py')
            options_gate.set()
            await expect(page.locator('#acp-preview-button')).to_be_enabled()
            await expect(page.locator('#acp-command')).to_have_value('echo my draft\npython mine.py')
            await expect(page.locator('#acp-command-lines')).to_have_text('1\n2')
            # Clipboard permission can resolve after the user has edited or selected another range.
            await page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{readText:()=>{window.pasteReads=(window.pasteReads||0)+1; return new Promise(resolve=>window.resolvePaste=resolve)}}})")
            await page.locator('#acp-command').fill('echo old')
            await page.locator('#acp-command').evaluate('e => e.setSelectionRange(5, 8)')
            await page.locator('#acp-command-paste').click()
            await page.locator('#acp-command').fill('echo edited while waiting')
            await page.locator('#acp-command').evaluate('e => e.select()')
            await page.evaluate("window.resolvePaste('CLIPBOARD')")
            await expect(page.locator('#acp-command')).to_have_value('echo edited while waiting')
            await expect(page.locator('#acp-command-notice')).to_contain_text('已变化')
            await page.locator('#acp-command').fill('prefix OLD suffix')
            await page.locator('#acp-command').evaluate('e => e.setSelectionRange(7, 10)')
            await page.locator('#acp-command-paste').click()
            await expect(page.locator('#acp-command-paste')).to_be_disabled()
            await page.locator('#acp-command-paste').dispatch_event('click')
            assert await page.evaluate('window.pasteReads') == 2, 'only one clipboard read may be pending'
            await page.locator('#acp-command').evaluate('e => e.setSelectionRange(0, 6)')
            await page.evaluate("window.resolvePaste('NEW')")
            await expect(page.locator('#acp-command')).to_have_value('prefix NEW suffix')
            await expect(page.locator('#acp-command-paste')).to_be_enabled()
            # A change then undo to identical content still invalidates the pending paste.
            await page.locator('#acp-command-paste').click()
            await page.locator('#acp-command').fill('temporary edit')
            await page.locator('#acp-command').fill('prefix NEW suffix')
            await page.evaluate("window.resolvePaste('DO NOT APPLY')")
            await expect(page.locator('#acp-command')).to_have_value('prefix NEW suffix')
            # Mark the draft touched as soon as paste begins, before template options arrive.
            options_gate.clear()
            await page.reload(wait_until='domcontentloaded')
            await page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{readText:()=>new Promise(resolve=>window.resolvePaste=resolve)}})")
            await page.locator('#acp-command-paste').click()
            options_gate.set()
            await expect(page.locator('#acp-preview-button')).to_be_enabled()
            await expect(page.locator('#acp-command')).to_have_value('')
            await page.evaluate("window.resolvePaste('echo clipboard')")
            await expect(page.locator('#acp-command')).to_have_value('echo clipboard')
            await page.locator('#acp-command-restore').click()
            await expect(page.locator('#acp-command')).to_have_value('echo template\npython train.py\nsleep inf')
            await page.locator('#acp-command-expand').click()
            await expect(page.locator('#acp-command-expand')).to_have_attribute('aria-expanded', 'true')
            await page.screenshot(path='/tmp/server-monitor-command-editor.png', full_page=True)
            await page.locator('#acp-command').fill('draft after expansion')
            await page.locator('#acp-command-expand').click()
            await expect(page.locator('#acp-command')).to_have_value('draft after expansion')
            await page.locator('#acp-command').evaluate('e => e.style.height = "300px"')
            await page.wait_for_timeout(50)
            assert await page.evaluate('document.querySelector("#acp-command-lines").clientHeight === document.querySelector("#acp-command").clientHeight'), 'line gutter must follow editor resize'
            await page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{readText: async()=>{throw Error('denied')}, writeText: async()=>{throw Error('denied')}}})")
            await page.locator('#acp-command-paste').click()
            await expect(page.locator('#acp-command-notice')).to_contain_text('粘贴')
            await expect(page.locator('#acp-command')).to_have_value('draft after expansion')
            await page.locator('.nav-item[href="#tasks"]').click()
            await page.locator('.nav-item[href="#acp"]').click()
            await expect(page.locator('#acp-command')).to_have_value('draft after expansion')
            assert await page.locator('#acp-create-form input, #acp-create-form select, #acp-create-form textarea').count() == 5
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#acp-command')).to_have_value('echo template\npython train.py\nsleep inf')
            mode['fallback'] = True
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#acp-command')).to_have_value('sleep inf')
            mode['options_error'] = True
            await page.reload(wait_until='networkidle')
            await page.locator('#acp-command').fill('preserve across retry')
            mode['options_error'] = False
            await page.locator('#acp-options-retry').click()
            await expect(page.locator('#acp-preview-button')).to_be_enabled()
            await expect(page.locator('#acp-command')).to_have_value('preserve across retry')
            await page.locator('.nav-item[href="#tasks"]').click()
            assert not [path for path, _ in calls if path.startswith('/api/logs')], 'monitor view must not fetch logs'
            await page.locator('#detail-tab-logs').click()
            await expect(page.locator('#logs-output')).to_contain_text('ACP:0 worker-0 main')
            await page.screenshot(path='/tmp/server-monitor-task-logs.png', full_page=True)
            assert await page.locator('#detail-panel').evaluate('e => e.getBoundingClientRect().bottom <= innerHeight'), await page.locator('#detail-panel').evaluate('e => e.getBoundingClientRect().toJSON()')
            assert await page.locator('#logs-output img').count() == 0
            await expect(page.locator('#logs-output')).to_contain_text('<img')
            await page.locator('#logs-worker').select_option('empty-worker')
            await expect(page.locator('#logs-status')).to_contain_text('暂无')
            await expect(page.locator('#logs-worker option')).to_have_count(3)
            await page.locator('#logs-worker').select_option('worker-0')
            await expect(page.locator('#logs-output')).to_contain_text('ACP:0 worker-0 main')
            mode['all_empty'] = True
            await page.locator('.task-card').filter(has_text='charles CCI').click()
            await expect(page.locator('#logs-status')).to_contain_text('暂无')
            await expect(page.locator('#logs-worker option')).to_have_count(3)
            await page.locator('#logs-worker').select_option('worker-0')
            mode['all_empty'] = False
            await page.locator('#logs-refresh').click()
            await expect(page.locator('#logs-output')).to_contain_text('CCI:1 worker-0 main')
            await page.locator('.task-card').filter(has_text='charles ACP').click()
            await expect(page.locator('#logs-output')).to_contain_text('ACP:0 worker-0 main')
            await page.locator('#logs-container').select_option('sidecar')
            await expect(page.locator('#logs-output')).to_contain_text('worker-0 sidecar')
            await page.locator('#logs-worker').select_option('worker-1')
            await expect(page.locator('#logs-output')).to_contain_text('worker-1 main')
            mode['error'] = True
            await page.locator('#logs-refresh').click()
            await expect(page.locator('#logs-message')).to_contain_text('会话')
            await expect(page.locator('#logs-output')).to_contain_text('worker-1 main')
            await expect(page.locator('#logs-status')).to_contain_text('旧')
            mode['error'] = False
            mode['slow'] = True
            await page.locator('#logs-refresh').click()
            count = len([path for path, _ in calls if path == '/api/logs'])
            await page.evaluate('window.logTimers.forEach(fn => fn())')
            await page.wait_for_timeout(100)
            assert count == len([path for path, _ in calls if path == '/api/logs']), 'no overlapping log request'
            await page.locator('.task-card').filter(has_text='charles CCI').click()
            await expect(page.locator('#logs-output')).to_contain_text('CCI:1 worker-0 main')
            stale_gate.set()
            await page.wait_for_timeout(100)
            await expect(page.locator('#logs-output')).not_to_contain_text('STALE')
            mode['long'] = True
            await page.locator('#logs-refresh').click()
            await expect(page.locator('#logs-output')).to_contain_text('long line')
            assert await page.locator('#logs-output').evaluate('e => e.scrollTop + e.clientHeight >= e.scrollHeight - 2')
            await page.locator('#logs-follow').uncheck()
            await page.locator('#logs-output').evaluate('e => e.scrollTop = 0')
            await page.locator('#logs-refresh').click()
            await expect(page.locator('#logs-refresh')).to_be_enabled()
            assert await page.locator('#logs-output').evaluate('e => e.scrollTop') == 0
            await page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, value:true}); document.dispatchEvent(new Event('visibilitychange'))")
            count = len([path for path, _ in calls if path.startswith('/api/logs')])
            await page.evaluate('window.logTimers.forEach(fn => fn())')
            await page.wait_for_timeout(100)
            assert count == len([path for path, _ in calls if path.startswith('/api/logs')])
            await page.evaluate("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
            await expect(page.locator('#logs-refresh')).to_be_enabled()
            mode['long'] = False
            mode['mismatch'] = True
            await page.locator('#logs-refresh').click()
            await expect(page.locator('#logs-message')).to_contain_text('身份')
            mode['mismatch'] = False
            mode['replacement'] = True
            await page.locator('#logs-refresh').click()
            await expect(page.locator('#logs-output')).to_contain_text('worker-new main')
            await expect(page.locator('#logs-output')).not_to_contain_text('worker-0 main')
            mode['empty'] = True
            await page.locator('#logs-refresh').click()
            await expect(page.locator('#logs-status')).to_contain_text('暂无日志')
            await page.locator('#task-search').fill('NO MATCHING TASK')
            await expect(page.locator('#detail-panel')).not_to_be_visible()
            count = len([path for path, _ in calls if path.startswith('/api/logs')])
            await page.evaluate('window.logTimers.forEach(fn => fn())')
            await page.wait_for_timeout(100)
            assert count == len([path for path, _ in calls if path.startswith('/api/logs')])
            await page.locator('#task-search').fill('')
            await expect(page.locator('#detail-panel')).to_be_visible()
            await page.locator('#detail-tab-monitor').click()
            count = len([path for path, _ in calls if path.startswith('/api/logs')])
            await page.evaluate('window.logTimers.forEach(fn => fn())')
            await page.wait_for_timeout(100)
            assert count == len([path for path, _ in calls if path.startswith('/api/logs')])
            await expect(page.locator('#history-chart canvas')).to_be_visible()
            await page.locator('#detail-tab-logs').click()
            await page.locator('.nav-item[href="#overview"]').click()
            await page.wait_for_timeout(100)
            count = len([path for path, _ in calls if path.startswith('/api/logs')])
            await page.evaluate('window.logTimers.forEach(fn => fn())')
            await page.wait_for_timeout(100)
            assert count == len([path for path, _ in calls if path.startswith('/api/logs')])
            await page.set_viewport_size({'width': 390, 'height': 844})
            for name in ('acp', 'tasks'):
                await page.locator(f'.nav-item[href="#{name}"]').click()
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
            assert not errors, errors
            await browser.close()
            print('Command/log smoke passed: default command and async draft safety; restore, expand, denied paste; five inputs; plain-text logs, worker/container selection, old data/errors, stale responses, polling lifecycle, chart restoration, mobile overflow.')
    finally:
        server.shutdown()
        server.server_close()

if __name__ == '__main__':
    asyncio.run(main())
