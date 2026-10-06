"""Verify task-card identity and focus in Chromium with local fixture APIs only."""
import asyncio
import copy
import functools
import json
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?', 1)[0] == '/':
            self.path = '/static/index.html'
        super().do_GET()

    def log_message(self, *args):
        pass


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    now = datetime.now(timezone.utc).isoformat()
    snapshot = {'status': 'ok', 'connected': True, 'updated_at': now, 'interval_seconds': 60,
                'clusters': [], 'waiting_tasks': [], 'source_status': {
                    kind: {'status': 'ok'} for kind in ('CCI', 'ACP', 'CCI_queue', 'ACP_queue')},
                'tasks': [{'id': f'ACP:job-{i}', 'kind': 'ACP', 'name': f'charles task {i}',
                           'state': 'running', 'gpu_count': 1, 'replicas': 1, 'cluster': 'fixture',
                           'updated_at': now, 'metrics': {'gpu': 10}} for i in range(100)]}
    errors, unexpected = [], []

    async def api(route):
        path = urlsplit(route.request.url).path
        if path == '/api/state':
            payload = copy.deepcopy(snapshot)
        elif path == '/api/history':
            payload = {'series': []}
        elif path == '/api/utilization-queue':
            payload = {'site': 'maoshanwang', 'tasks': [], 'loading': False, 'status': 'ok', 'warnings': []}
        elif path == '/api/acp/options':
            payload = {'template': {'id': 'fixture', 'name': 'fixture', 'created_at': now},
                       'gpu_options': [1], 'cluster_order': [], 'clusters': []}
        else:
            unexpected.append(path)
            await route.fulfill(status=503, json={'error': 'Unexpected fixture API'})
            return
        await route.fulfill(json=payload)

    async def local_only(route):
        if urlsplit(route.request.url).hostname not in ('127.0.0.1', 'localhost'):
            unexpected.append(route.request.url)
            await route.abort()
        else:
            await route.continue_()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/*', local_only)
                await page.route('**/api/**', api)
                await page.goto(f'http://127.0.0.1:{server.server_port}/#tasks')
                cards = page.locator('.task-card')
                await expect(cards).to_have_count(100)
                await page.evaluate("""() => {
                    window.savedCards = Array.from(document.querySelectorAll('.task-card'));
                    window.listChanges = 0;
                    new MutationObserver(records => { listChanges += records.length; })
                        .observe(document.getElementById('task-list'), {childList: true});
                }""")
                await cards.nth(8).click()
                await expect(page.locator('#detail-title')).to_have_text('charles task 8')
                await expect(cards.nth(8)).to_have_attribute('aria-pressed', 'true')
                assert await page.evaluate("savedCards.every((card, i) => card === document.querySelectorAll('.task-card')[i])")
                assert await page.evaluate('document.activeElement === savedCards[8]')
                await cards.nth(8).click()
                assert await page.evaluate('listChanges') == 0

                snapshot['tasks'][8]['metrics']['gpu'] = 42
                await page.evaluate("document.dispatchEvent(new CustomEvent('monitor-refresh'))")
                await expect(cards.nth(8)).to_contain_text('42')
                assert await page.evaluate("savedCards.every((card, i) => i === 8 || card === document.querySelectorAll('.task-card')[i])")
                assert await page.evaluate("savedCards[8] !== document.querySelectorAll('.task-card')[8]")

                await page.locator('#task-search').fill('ACP:job-99')
                await expect(cards).to_have_count(1)
                assert await page.evaluate("document.querySelector('.task-card') === savedCards[99]")
                await page.locator('#task-search').fill('does-not-exist')
                await expect(cards).to_have_count(0)
                await expect(page.locator('#task-list')).to_contain_text('没有符合条件的任务')
                await page.locator('#task-search').fill('')
                await expect(cards).to_have_count(100)
                assert not errors, errors
                assert not unexpected, unexpected
                print(json.dumps({'result': 'passed', 'tasks': 100, 'selection_list_mutations': 0,
                                  'checks': ['card identity', 'keyboard focus', 'same selection',
                                             'one metric update', 'search reuse', 'empty and restore',
                                             'no JS errors', 'local fixture APIs only']}))
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
