"""Local-only regressions for IDE API outages and restarted preparation jobs."""
import asyncio
import functools
import threading
from collections import Counter
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options
from cci_ide_smoke import IDEHandler
from v2_browser_smoke import ROOT


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(IDEHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    data = {'site': 'maoshanwang', 'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main', 'port': 43219, 'ticket': 'a' * 48}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            async def case(mode):
                page = await browser.new_page(viewport={'width':1440,'height':900})
                calls = Counter()
                async def api(route):
                    action = urlparse(route.request.url).path.rsplit('/', 1)[-1]
                    calls[action] += 1
                    if mode == 'outage' or (mode == action and calls[action] == 1):
                        await route.abort('connectionfailed'); return
                    if mode == 'forbidden':
                        await route.fulfill(status=403, json={'error':'禁止访问', 'code':'forbidden'}); return
                    if action == 'status':
                        if mode in ('restart', 'expired') and (mode == 'expired' or calls[action] == 1):
                            await route.fulfill(status=404, json={'error':'编辑器请求已过期，请重试。','code':'ide_expired'}); return
                        job = route.request.post_data_json['request_id']
                        result = {'pending':False,'result':{**data, 'targets':[data]} if job == 'targets' else data}
                    else: result = {'request_id':action}
                    await route.fulfill(json=result)
                async def editor(route):
                    if urlparse(route.request.url).path == '/_ilab_frame_status.js':
                        await route.fulfill(content_type='application/javascript',body=(ROOT/'static/ide-frame-status.js').read_text()); return
                    await route.fulfill(content_type='text/html',body=f'<script src="/_ilab_frame_status.js" data-parent-origin="{base}"></script><main class="monaco-workbench">Editor</main><textarea aria-label="Draft"></textarea>')
                await page.route('**/api/**',api)
                await page.route('http://127.0.0.1:43219/**',editor)
                await page.goto(base+'/ide?task_id=CCI:job')
                status = page.locator('#ide-status')
                if mode in ('outage','forbidden','expired'):
                    await expect(status).to_have_attribute('data-state','error',timeout=12000)
                    if mode == 'outage':
                        await expect(status).to_contain_text('本机监控服务暂时无法连接')
                        assert calls['targets'] == 3, calls
                    elif mode == 'expired': assert calls['targets'] == 2, calls
                    else: assert calls['targets'] == 1, calls
                    await expect(page.locator('#ide-retry')).to_be_visible()
                else:
                    await expect(status).to_have_attribute('data-state','ready',timeout=12000)
                    if mode == 'restart': assert calls['targets'] == 2, calls
                    else: assert calls[mode] >= 2, calls
                await page.close()
                print('PASS',mode,dict(calls))
            await case('targets')
            await asyncio.gather(*(case(mode) for mode in ('open','status','restart','outage','forbidden','expired')))
            await browser.close()
    finally:
        server.shutdown(); server.server_close()

if __name__ == '__main__': asyncio.run(main())
