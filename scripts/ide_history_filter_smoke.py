"""Browser fixture for project-scoped Codex history filtering."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options

ROOT = Path(__file__).resolve().parents[1]


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**browser_launch_options())
        page = await browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        async def handle_route(route):
            request = route.request
            req = request
            path = req.url.split('/', 3)[-1].split('?')[0]
            if path == 'status.js':
                body = (ROOT / 'static/ide-frame-status.js').read_text()
            elif path == 'wrapper':
                body = '<iframe id="ide-frame" src="/editor" style="width:900px;height:700px"></iframe>'
            elif path == 'editor':
                body = '''<script src="/status.js" data-parent-origin="http://editor.test"></script>
                    <main class="monaco-workbench" style="width:900px;height:700px">
                    <iframe class="webview" src="/pre?extensionId=openai.chatgpt" style="width:800px;height:600px"></iframe></main>'''
            elif path == 'pre':
                body = '<iframe id="active-frame" src="/panel" style="width:700px;height:500px"></iframe>'
            elif path == 'panel':
                body = '''<main><h1>Chats</h1><div id="rows"></div><script>
                    for (const title of ['Project history', 'Unrelated history', 'Nested project history']) {
                      const row = document.createElement('button'); row.dataset.testRow = '1';
                      row.append(document.createTextNode(title));
                      const archive = document.createElement('button'); archive.textContent = 'Archive chat'; row.append(archive);
                      document.querySelector('#rows').append(row);
                    }
                </script></main>'''
            else:
                body = ''
            await route.fulfill(content_type='text/html; charset=utf-8', body=body)

        await page.route('**/*', handle_route)
        await page.goto('http://editor.test/wrapper')
        editor = page.frame_locator('#ide-frame')
        panel = editor.frame_locator('iframe.webview').frame_locator('#active-frame')
        await expect(panel.locator('[data-test-row]').first).to_be_attached()
        await page.locator('#ide-frame').evaluate("(frame) => frame.contentWindow.postMessage({type:'ilab-codex-history', workspace:'/workspace/project', records:[{thread_id:'1', display_title:'Project history', cwd:'/workspace/project', updated_at:2},{thread_id:'2', display_title:'Nested project history', cwd:'/workspace/project/src', updated_at:1}]}, 'http://editor.test')")
        await expect(panel.locator('[data-test-row]').nth(0)).to_be_visible()
        await expect(panel.locator('[data-test-row]').nth(1)).to_be_hidden()
        await expect(panel.locator('[data-test-row]').nth(2)).to_be_visible()
        await expect(panel.locator('[data-ilab-history-filter]')).to_contain_text('当前项目聊天')
        await panel.locator('[data-ilab-history-toggle]').click()
        await expect(panel.locator('[data-test-row]').nth(1)).to_be_visible()
        assert not errors, errors
        await browser.close()
        print('PASS project history filtering')


if __name__ == '__main__':
    asyncio.run(main())
