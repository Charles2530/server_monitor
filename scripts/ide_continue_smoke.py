"""Local fixtures: auto-continue only the pinned Codex informational welcome."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options

ROOT = Path(__file__).resolve().parents[1]
WELCOME = '''<section><h1></h1><div><ul>
<li><span>Decide how much autonomy you want to grant</span><span>For more details, see the <a href="https://developers.openai.com/codex/ide">Codex docs</a></span></li>
<li><span>Codex can make mistakes</span><span>Review the code it writes and commands it runs</span></li>
<li><span>Powered by your ChatGPT account</span><span>Uses your plan’s rate limits and <a href="https://chatgpt.com/#settings/DataControls">training data preferences</a></span></li>
</ul></div><div><div><button type="button">Continue</button></div></div></section>'''

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(**browser_launch_options())
        async def case(name, *, wrapper=False, extension='openai.chatgpt', body=WELCOME, host_style='', active_style='', foreign=False, succeeds=False, disabled=False):
            page = await browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            if disabled:
                body = body.replace('type="button"', 'type="button" disabled')
            async def route(r):
                path = r.request.url.split('/', 3)[-1].split('?')[0]
                if path == 'status.js':
                    await r.fulfill(content_type='application/javascript', body=(ROOT / 'static/ide-frame-status.js').read_text()); return
                if path == 'wrapper':
                    html = '<iframe src="/editor" style="width:900px;height:700px"></iframe>'
                elif path == 'pre':
                    html = f'<iframe id="active-frame" title="Codex" style="width:700px;height:600px;{active_style}" src="/panel"></iframe>'
                elif path == 'panel':
                    html = body + '''<script>document.addEventListener('click', e => {if(e.target.tagName === 'BUTTON') {document.body.dataset.clicks = String(Number(document.body.dataset.clicks || 0) + 1);}});</script>'''
                else:
                    host = 'http://foreign.test' if foreign else ''
                    html = f'<script src="/status.js" data-parent-origin="http://editor.test"></script><main class="monaco-workbench">Editor</main><iframe class="webview" style="width:800px;height:650px;{host_style}" src="{host}/pre?extensionId={extension}"></iframe>'
                await r.fulfill(content_type='text/html', body=html)
            await page.route('**/*', route)
            await page.goto('http://editor.test/' + ('wrapper' if wrapper else 'editor'))
            scope = page.frame_locator('iframe') if wrapper else page
            panel = scope.frame_locator('iframe.webview').frame_locator('#active-frame')
            await expect(panel.locator('body')).to_be_attached()
            if succeeds:
                await expect(panel.locator('body')).to_have_attribute('data-clicks', '1', timeout=7000)
                # The same welcome may remain while the extension handles its event.
                await page.wait_for_timeout(2500)
                await expect(panel.locator('body')).to_have_attribute('data-clicks', '1')
                await page.reload()
                await expect(panel.locator('body')).to_have_attribute('data-clicks', '1', timeout=7000)
            else:
                await page.wait_for_timeout(4500)
                assert await panel.locator('body').get_attribute('data-clicks') is None, name
            assert not errors, (name, errors)
            await page.close()
            print('PASS', name)
        await case('direct startup and reload, one click per document', succeeds=True)
        await asyncio.gather(
            case('embedded startup and reload', wrapper=True, succeeds=True),
            case('unrelated Continue', body='<button>Continue</button>'),
            case('different extension', extension='other.extension'),
            case('foreign webview', foreign=True),
            case('hidden host', host_style='visibility:hidden'),
            case('transparent host', host_style='opacity:0'),
            case('hidden active frame', active_style='display:none'),
            case('disabled button', disabled=True),
            case('changed welcome', body=WELCOME.replace('Codex can make mistakes', 'Accept the terms of service')),
            case('permission control', body=WELCOME.replace('<ul>', '<input type="checkbox"><ul>')),
            case('permission dialog', body='<div role="dialog">' + WELCOME + '</div>'),
            case('chat containing welcome text', body='<textarea aria-label="Prompt"></textarea>' + WELCOME),
            case('changed link', body=WELCOME.replace('https://developers.openai.com/codex/ide', 'https://example.test/terms')),
            case('multiple Continue buttons', body=WELCOME + '<button>Continue</button>'),
        )
        await browser.close()

if __name__ == '__main__': asyncio.run(main())
