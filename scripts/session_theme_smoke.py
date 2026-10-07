"""Saved appearance and cross-tab theme sync on local session shells; no cloud traffic."""
import asyncio
import functools
import threading
from http.server import ThreadingHTTPServer

from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options
from v2_browser_smoke import ROOT, Handler


async def readable(locator, background_selector=None):
    colors = await locator.evaluate('''(el, selector) => {
        const values = color => color.match(/[\\d.]+/g).slice(0, 3).map(Number);
        return {foreground: values(getComputedStyle(el).color),
                background: values(getComputedStyle(selector ? el.closest(selector) : el).backgroundColor)};
    }''', background_selector)
    def luminance(rgb):
        channels = [c / 255 / 12.92 if c / 255 <= .04045 else ((c / 255 + .055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * w for c, w in zip(channels, (.2126, .7152, .0722)))
    a, b = luminance(colors['foreground']), luminance(colors['background'])
    assert (max(a, b) + .05) / (min(a, b) + .05) >= 4.5, colors


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    errors = []
    screenshots = ROOT / 'data' / 'visual-refresh'
    screenshots.mkdir(parents=True, exist_ok=True)

    async def api(route):
        path = route.request.url.split('/api/')[1]
        if path == 'terminal/targets':
            result = {'request_id': 'targets'}
        elif path == 'terminal/status':
            result = {'pending': False, 'result': {'site': 'maoshanwang', 'task_id': 'CCI:job', 'name': 'charles theme test', 'targets': [
                {'instance': instance, 'container': 'main', 'url': f'https://console.ms-sc-01.maoshanwangtech.com/cci/terminal?workspace=/w&app_name=job&container_instance_name={instance}&container_name=main'}
                for instance in ('pod-1', 'pod-2')]}}
        else:
            raise AssertionError(f'Unexpected mocked request: {path}')
        await route.fulfill(json=result)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            await context.route('**/api/**', api)
            controller = await context.new_page()
            await controller.goto(base + '/static/favicon.svg')
            await controller.evaluate("localStorage.setItem('ilab.appearance', 'dark')")
            for shell in ('terminal', 'ide'):
                page = await context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(f'{base}/static/{shell}.html', wait_until='networkidle')
                await expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
                for theme in ('light', 'dark'):
                    await controller.evaluate('(value) => localStorage.setItem("ilab.appearance", value)', theme)
                    await expect(page.locator('html')).to_have_attribute('data-theme', theme)
                    # The no-task error is real shell UI, and must remain readable in both themes.
                    await readable(page.locator(f'#{shell}-status'), '.session-status')
                    for width in (1440, 1024, 768, 390, 320):
                        await page.set_viewport_size({'width': width, 'height': 900})
                        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (shell, width, theme)
                        if width in (1440, 390):
                            await page.screenshot(path=str(screenshots / f'{shell}-{theme}-{width}.png'), full_page=True, animations='disabled')
                    await page.reload(wait_until='networkidle')
                    await expect(page.locator('html')).to_have_attribute('data-theme', theme)
                await page.close()
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(base + '/static/terminal.html?task_id=CCI:job', wait_until='networkidle')
            picker = page.locator('#terminal-instance')
            await expect(picker).to_be_visible()
            for theme in ('light', 'dark'):
                await controller.evaluate('(value) => localStorage.setItem("ilab.appearance", value)', theme)
                await expect(page.locator('html')).to_have_attribute('data-theme', theme)
                await picker.hover()
                # Check the final hover color, after the existing 150 ms transition.
                await page.wait_for_timeout(200)
                await readable(picker)
                await page.screenshot(path=str(screenshots / f'terminal-picker-{theme}.png'), animations='disabled')
            assert not errors, errors
            await browser.close()
            print('Session theme passed: both shells, persisted light/dark, cross-tab sync, five widths, no page errors; mocked APIs only.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
