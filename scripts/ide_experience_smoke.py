"""Local fixtures only: preparation updates, Codex readiness and focus preserve drafts."""
import asyncio
import functools
import threading
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
    data = {'site': 'maoshanwang', 'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main', 'port': 43219}
    ticket = 'a' * 48
    calls, errors = [], []
    complete, unavailable = False, False
    def setup():
        return {'optional_pending': not complete, 'codex': {'configured': True, 'installed': complete}, 'appearance': {'installed': complete, 'version': '1.2.0'}}
    async def api(route):
        action = urlparse(route.request.url).path.rsplit('/', 1)[-1]
        calls.append(action)
        if action == 'details':
            if unavailable:
                await route.fulfill(status=503, json={'error': 'fixture unavailable'}); return
            result = {**data, **setup()}
        elif action in ('open', 'targets', 'recover'): result = {'request_id': action}
        elif route.request.post_data_json['request_id'] == 'targets': result = {'pending': False, 'result': {**data, 'targets': [data]}}
        else: result = {'pending': False, 'result': {**data, 'ticket': ticket, **setup()}}
        await route.fulfill(json=result)
    async def editor(route):
        path = urlparse(route.request.url).path
        if path == '/_ilab_frame_status.js':
            await route.fulfill(content_type='application/javascript', body=(ROOT / 'static/ide-frame-status.js').read_text()); return
        if path == '/pre': body = '<iframe id="active-frame" src="/codex" title="Codex"></iframe>'
        elif path == '/codex': body = '<main>Loading fixture</main>'
        else: body = f'<script src="/_ilab_frame_status.js" data-parent-origin="{base}"></script><main class="monaco-workbench">Editor</main><textarea aria-label="Draft"></textarea><iframe class="webview ready" src="/pre?extensionId=openai.chatgpt"></iframe>'
        await route.fulfill(content_type='text/html', body=body)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1440, 'height': 900})
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.route('**/api/**', api)
            await page.route('http://127.0.0.1:43219/**', editor)
            await page.goto(base + '/ide?task_id=CCI:job')
            status = page.locator('#ide-setup-status')
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            await expect(status).to_contain_text('Codex 面板加载中', timeout=10000)
            draft = page.frame_locator('#ide-frame').get_by_role('textbox', name='Draft')
            await draft.fill('unsaved draft')
            before = await page.locator('#ide-frame').bounding_box()
            await page.locator('#ide-focus').click()
            await expect(page.locator('#ide-focus')).to_have_attribute('aria-pressed', 'true')
            after = await page.locator('#ide-frame').bounding_box()
            assert after['height'] > before['height']
            await expect(draft).to_have_value('unsaved draft')
            await expect(page.locator('#ide-task')).to_be_hidden()
            unavailable = True
            await expect(status).to_contain_text('配置状态暂不可用', timeout=15000)
            await expect(draft).to_have_value('unsaved draft')
            unavailable, complete = False, True
            await expect(status).not_to_contain_text('后台准备', timeout=20000)
            await expect(status).not_to_contain_text('未完成')
            # Only UI structure is inspected: a logo is not readiness; a usable
            # onboarding or input control proves the panel actually rendered.
            codex = page.frame_locator('#ide-frame').frame_locator('iframe.webview').frame_locator('#active-frame')
            await codex.locator('main').evaluate("el => { const button = document.createElement('button'); button.textContent = 'Continue'; el.append(button); }")
            await page.locator('#ide-focus').click()
            await expect(status).to_contain_text('Codex 面板已加载', timeout=10000)
            host = page.frame_locator('#ide-frame').locator('iframe.webview')
            await host.evaluate("el => el.style.visibility = 'hidden'")
            await expect(status).to_contain_text('等待面板打开', timeout=10000)
            await host.evaluate("el => el.style.visibility = 'visible'")
            await codex.locator('button').evaluate("el => el.style.visibility = 'hidden'")
            await expect(status).to_contain_text('Codex 面板加载中', timeout=10000)
            await codex.locator('button').evaluate("el => el.style.visibility = 'visible'")
            await expect(status).to_contain_text('Codex 面板已加载', timeout=10000)
            inner = page.frame_locator('#ide-frame').frame_locator('iframe.webview').locator('#active-frame')
            await inner.evaluate("el => el.style.visibility = 'hidden'")
            await expect(status).to_contain_text('Codex 面板加载中', timeout=10000)
            await inner.evaluate("el => el.style.visibility = 'visible'")
            await expect(status).to_contain_text('Codex 面板已加载', timeout=10000)
            count = calls.count('details')
            await page.wait_for_timeout(6000)
            assert calls.count('details') == count, 'Stop preparation polling after completion'
            await page.evaluate("window.dispatchEvent(new MessageEvent('message', {origin:'http://attacker.invalid', source:document.getElementById('ide-frame').contentWindow, data:{type:'ilab-editor-codex',state:'loading'}}))")
            await expect(status).to_contain_text('Codex 面板已加载')
            await page.locator('#ide-focus').click()
            await page.evaluate("window.dispatchEvent(new Event('offline'))")
            await expect(page.locator('#ide-status')).to_be_visible()
            await expect(page.locator('#ide-status')).to_contain_text('网络已离线')
            await expect(draft).to_have_value('unsaved draft')
            assert calls.count('open') == 1
            await page.reload()
            await expect(page.locator('#ide-focus')).to_have_attribute('aria-pressed', 'true')
            assert not errors, errors
            await page.screenshot(path=str(ROOT / 'data/ide-focus-fixture.png'))
            await browser.close()
            print('IDE experience passed: background status updates and backoff, Codex DOM readiness, focus persistence and draft preservation, origin guard. No cloud calls.')
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__': asyncio.run(main())
