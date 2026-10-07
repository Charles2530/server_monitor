"""Connection log behavior on local shells with all transport traffic mocked."""
import asyncio
import base64
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options
from v2_browser_smoke import ROOT, Handler


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    errors, sockets = [], []
    jobs = {}

    async def api(route):
        path = urlparse(route.request.url).path
        data = route.request.post_data_json or {}
        if path == '/api/terminal/targets':
            jobs['targets'] = {'site': 'maoshanwang', 'task_id': 'CCI:job', 'name': 'log fixture', 'targets': [
                {'instance': 'pod', 'container': 'main', 'url': 'https://console.ms-sc-01.maoshanwangtech.com/cci/terminal?workspace=/w&app_name=job&container_instance_name=pod&container_name=main'}]}
            result = {'request_id': 'targets'}
        elif path == '/api/terminal/open':
            jobs['open'] = {'site': 'maoshanwang', 'task_id': 'CCI:job', 'session_id': 'secret-session-ticket'}
            result = {'request_id': 'open'}
        elif path == '/api/terminal/status':
            result = {'pending': False, 'result': jobs[data['request_id']]}
        elif path == '/api/terminal/close':
            result = {'closed': True}
        else:
            raise AssertionError(path)
        await route.fulfill(json=result)

    async def websocket(ws):
        sockets.append(ws)
        def message(raw):
            data = json.loads(raw)
            if 'session_id' in data:
                ws.send(json.dumps({'type': 'ready', 'sequence': 0}))
            elif data.get('type') == 'attach':
                ws.send(json.dumps({'type': 'output', 'cursor': 1, 'chunks': [base64.b64encode(b'PRIVATE_TERMINAL_OUTPUT\r\n').decode()]}))
            elif 'sequence' in data:
                ws.send(json.dumps({'type': 'ack', 'sequence': data['sequence']}))
            elif data.get('type') == 'ping':
                ws.send(json.dumps({'type': 'pong'}))
        ws.on_message(message)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            await context.route('**/api/**', api)
            await context.route_web_socket('**/api/terminal/socket', websocket)
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(base + '/static/terminal.html?task_id=CCI:job')
            await expect(page.locator('#terminal-led')).to_have_class('terminal-led connected')
            assert await page.evaluate('!!window.MonitorConnectionLog'), 'Connection log API is missing'
            await expect(page.locator('.terminal-toolbar .connection-current-state')).to_have_text('已连接')
            await page.locator('.terminal-toolbar .connection-log-button').click()
            await expect(page.locator('#connection-log-dialog')).to_be_visible()
            await expect(page.locator('#connection-log-list')).to_contain_text('WebSocket')
            await expect(page.locator('#connection-log-list')).to_contain_text('正在建立终端连接。')
            await expect(page.locator('#connection-log-list')).not_to_contain_text('会话已过期')
            await expect(page.locator('#connection-log-list')).not_to_contain_text('PRIVATE_TERMINAL_OUTPUT')
            await page.keyboard.press('Escape')
            await expect(page.locator('#connection-log-dialog')).not_to_be_visible()
            await expect(page.locator('.terminal-toolbar .connection-log-button')).to_be_focused()
            await page.locator('#terminal-fullscreen').click()
            await page.locator('.terminal-toolbar .connection-log-button').click()
            await expect(page.locator('#terminal-workspace #connection-log-dialog')).to_be_visible()
            await page.locator('#connection-log-close').click()
            await page.locator('#terminal-fullscreen').click()
            await page.locator('.xterm-helper-textarea').press_sequentially('PRIVATE_USER_COMMAND')
            await sockets[-1].close()
            await expect(page.locator('#connection-log-list')).to_contain_text('正在恢复原 Shell 连接')
            await expect(page.locator('.terminal-toolbar .connection-current-state')).to_have_text('已连接')
            await page.evaluate("window.dispatchEvent(new Event('offline'))")
            await expect(page.locator('.terminal-toolbar .connection-current-state')).to_have_text('离线')
            socket_count = len(sockets)
            await page.evaluate("window.dispatchEvent(new Event('online'))")
            await expect(page.locator('.terminal-toolbar .connection-current-state')).to_have_text('已连接')
            assert len(sockets) == socket_count, 'Online event must probe the existing healthy transport'
            await page.locator('#terminal-disconnect').click()
            await expect(page.locator('.terminal-toolbar .connection-current-state')).to_have_text('离线')
            contents = await page.locator('#connection-log-list').text_content()
            assert all(secret not in contents for secret in ('PRIVATE_USER_COMMAND', 'PRIVATE_TERMINAL_OUTPUT', 'secret-session-ticket'))
            for width in (1440, 390, 320):
                await page.set_viewport_size({'width': width, 'height': 900})
                await expect(page.locator('.terminal-toolbar .connection-log-button')).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            await page.set_viewport_size({'width': 320, 'height': 400})
            await page.locator('.terminal-toolbar .connection-log-button').click()
            await expect(page.locator('#connection-log-close')).to_be_in_viewport()
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.close()

            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(base + '/static/ide.html')
            assert await page.evaluate('!!window.MonitorConnectionLog')
            await page.evaluate("""() => {
                document.querySelector('#ide-frame').hidden = false;
                document.body.classList.add('ide-focused');
                MonitorConnectionLog.update('ready', '编辑器已就绪');
            }""")
            button = page.locator('.connection-log-button')
            await expect(button).to_be_visible()
            await expect(page.locator('.connection-current-state')).to_have_text('已连接')
            await page.evaluate("MonitorConnectionLog.note('API open · HTTP 503 · 152ms', 'error')")
            await expect(page.locator('#connection-log-list')).to_contain_text('API open · HTTP 503 · 152ms')
            await page.evaluate("MonitorConnectionLog.note('WebSocket 已关闭（代码 1006）。', 'error')")
            await expect(page.locator('#connection-log-list')).to_contain_text('代码 1006')
            await page.evaluate("MonitorConnectionLog.note('API open · HTTP 503 · 152ms SECRET_SUFFIX', 'error')")
            await expect(page.locator('#connection-log-list')).not_to_contain_text('SECRET_SUFFIX')
            before = await page.locator('#connection-log-list li').count()
            await page.evaluate("MonitorConnectionLog.update('ready', 'pod / main · 编辑器已就绪 · 22 秒')")
            assert await page.locator('#connection-log-list li').count() == before, 'Heartbeat must not flood logs'
            await page.evaluate("MonitorConnectionLog.update('checking', '正在探测后台连接状态。')")
            await expect(page.locator('.connection-current-state')).to_have_text('检查中')
            await expect(page.locator('.connection-monitor')).to_have_attribute('data-state', 'checking')
            await expect(page.locator('#connection-log-list li').last).to_contain_text('检查中正在探测后台连接状态。')
            await page.evaluate("MonitorConnectionLog.update('error', 'password=PRIVATE_PASSWORD https://bad.example/path?ticket=PRIVATE_TICKET PRIVATE_RAW_ERROR')")
            contents = await page.locator('#connection-log-list').text_content()
            assert all(secret not in contents for secret in ('PRIVATE_PASSWORD', 'PRIVATE_TICKET', 'PRIVATE_RAW_ERROR', 'https://bad.example'))
            await page.evaluate("MonitorConnectionLog.update('loading', '正在恢复编辑器认证和连接；当前编辑内容已保留…')")
            await expect(page.locator('.connection-current-state')).to_have_text('重连中')
            await button.click()
            await expect(page.locator('#connection-log-dialog')).to_be_visible()
            await expect(page.locator('#connection-log-updated')).not_to_be_empty()
            await page.evaluate("""() => { for (let i = 0; i < 230; i++) MonitorConnectionLog.note('正在检查连接。', 'loading'); }""")
            await expect(page.locator('#connection-log-list li')).to_have_count(200)
            await page.wait_for_timeout(100)
            assert await page.locator('#connection-log-list').evaluate('(el) => el.scrollHeight - el.scrollTop - el.clientHeight < 3')
            await expect(page.locator('.connection-current-state')).to_have_text('重连中')
            screenshots = ROOT / 'data' / 'connection-logs'
            screenshots.mkdir(parents=True, exist_ok=True)
            for theme in ('light', 'dark'):
                await page.evaluate('(value) => document.documentElement.dataset.theme = value', theme)
                for width in (1440, 390, 320):
                    await page.set_viewport_size({'width': width, 'height': 900})
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (theme, width)
                    await expect(page.locator('#connection-log-close')).to_be_in_viewport()
                    if width in (1440, 390):
                        await page.screenshot(path=str(screenshots / f'log-{theme}-{width}.png'), animations='disabled')
            await page.locator('#connection-log-close').click()
            await expect(button).to_be_focused()
            await page.reload()
            assert await page.locator('#connection-log-list li').count() < 200, 'Logs must not persist across reload'
            assert not errors, errors
            await browser.close()
            print('Connection logs passed: Terminal lifecycle/reconnect/offline, private data exclusion, focus-mode IDE, 200-row cap, auto-scroll, focus restore, light/dark and 320–1440px; mocked traffic only.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
