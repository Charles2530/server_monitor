"""Real isolated proxy and expired-entry HTML/CSP with fixture editor APIs only."""
import asyncio
import functools
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright, expect

from browser_support import browser_launch_options
from cci_ide_smoke import IDEHandler
from server.ide_proxy import EditorProxy
from v2_browser_smoke import ROOT


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(IDEHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    editor = '<!doctype html><html><head><script id="vscode-workbench-web-configuration" type="application/json">{}</script></head><body><main class="monaco-workbench">Editor fixture</main><textarea aria-label="Unsaved document"></textarea></body></html>'
    proxy = EditorProxy(12345, 'fixture=private', base, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=editor, headers={'content-type': 'text/html'})))
    await proxy.start()
    calls, errors = [], []
    opened = 0
    data = {'site': 'maoshanwang', 'task_id': 'CCI:job', 'instance': 'pod-1', 'container': 'main'}

    async def api(route):
        nonlocal opened
        path = urlparse(route.request.url).path.rsplit('/', 1)[-1]
        calls.append(path)
        if path == 'targets':
            result = {'request_id': 'targets'}
        elif path == 'open':
            opened += 1
            result = {'request_id': 'open'}
        elif path == 'status':
            if route.request.post_data_json['request_id'] == 'targets':
                result = {'pending': False, 'result': {**data, 'targets': [data]}}
            else:
                ticket = proxy.ticket()
                if opened == 1:
                    proxy.tickets[ticket] -= 120
                result = {'pending': False, 'result': {**data, 'port': proxy.port, 'ticket': ticket}}
        else:
            raise AssertionError(path)
        await route.fulfill(json=result)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1440, 'height': 900})
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            await page.goto(base + '/ide?task_id=CCI:job')
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'error')
            await expect(page.locator('#ide-status')).to_contain_text('入口已过期')
            await expect(page.frame_locator('#ide-frame').get_by_text('入口已过期，请点击上方“重新连接”。')).to_be_visible()
            await page.locator('#ide-retry').click()
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            draft = page.frame_locator('#ide-frame').get_by_role('textbox', name='Unsaved document')
            await draft.fill('preserve my draft')
            assert opened == 2 and 'recover' not in calls
            assert not errors, errors
            await page.screenshot(path=str(ROOT / 'data/ide-entry-recovered.png'))
            await browser.close()
            print('IDE entry integration passed: real expired 403 HTML/CSP notifies parent; explicit retry gets fresh ticket and loads workbench. No cloud calls.')
    finally:
        await proxy.close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
