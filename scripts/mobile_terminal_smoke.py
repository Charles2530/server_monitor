"""Touch terminal regression with fixture APIs; never opens a cloud shell."""
import asyncio
import base64
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options
from v2_browser_smoke import ROOT, Handler

class TerminalHandler(Handler):
    def do_GET(self):
        if self.path.split('?', 1)[0] == '/terminal': self.path = '/static/terminal.html'
        super().do_GET()

async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(TerminalHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    inputs, errors, reads, opens = [], [], [], []
    sequence = 0
    fail_next = False
    fail_write = False
    async def api(route):
        nonlocal sequence, fail_next, fail_write
        path = urlsplit(route.request.url).path
        data = route.request.post_data_json or {}
        assert route.request.method == 'POST'
        if path.endswith('/targets'):
            result = {'request_id': 'targets'}
        elif path.endswith('/status'):
            result = {'pending': False, 'result': {'site': 'maoshanwang', 'task_id': 'CCI:job', 'name': 'charles ' + 'long name ' * 12,
                'targets': [{'instance': 'pod', 'container': 'main', 'url': 'https://console.ms-sc-01.maoshanwangtech.com/cci/terminal?workspace=/w&app_name=job&container_instance_name=pod&container_name=main'}]} if data['request_id'] == 'targets' else {'session_id': 'one-shell', 'site': 'maoshanwang', 'task_id': 'CCI:job'}}
        elif path.endswith('/open'):
            opens.append(data); result = {'request_id': 'open'}
        elif path.endswith('/write'):
            assert data['sequence'] == sequence + 1, (data, sequence)
            sequence = data['sequence']; inputs.append(data)
            if fail_write and data.get('data'):
                fail_write = False
                await route.abort('failed'); return
            result = {'type': 'ack', 'sequence': sequence}
        elif path.endswith('/read'):
            reads.append(data)
            if fail_next:
                fail_next = False
                await route.fulfill(status=503, json={'error': 'simulated network interruption'}); return
            if data['cursor'] and data.get('wait', True): await asyncio.sleep(.15)
            result = {'type': 'output', 'cursor': 1, 'sequence': sequence, 'chunks': [] if data['cursor'] else [base64.b64encode(b'Touch terminal ready\r\n$ ').decode()], 'closed': False, 'error': None}
        elif path.endswith('/close'): result = {'closed': True}
        else: raise AssertionError(path)
        try: await route.fulfill(json=result)
        except Exception: pass  # Page teardown cancels reads.
    async def sent(text, before):
        for _ in range(60):
            if ''.join(x['data'] for x in inputs[before:]) == text: return
            await asyncio.sleep(.05)
        raise AssertionError((text, inputs[before:]))
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            context = await browser.new_context(viewport={'width':393,'height':780}, is_mobile=True, has_touch=True)
            await context.route('**/api/**', api)
            await context.route_web_socket('**/api/terminal/socket', lambda ws: ws.close())
            page = await context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.add_init_script("Object.defineProperty(window,'Terminal',{configurable:true,set(Base){delete window.Terminal;window.Terminal=class extends Base{constructor(...args){super(...args);window.testTerm=this;}};}})")
            await page.goto(f'http://127.0.0.1:{server.server_port}/terminal?task_id=CCI:job')
            await expect(page.locator('#terminal-transport')).to_have_text('兼容连接')
            await expect(page.locator('.xterm-rows')).to_contain_text('Touch terminal ready')
            # Layout assertions use actual xterm dimensions, not just hidden overflow.
            for width, height, min_rows in ((393,780,25),(393,350,12),(844,390,15),(844,180,5)):
                await page.set_viewport_size({'width':width,'height':height})
                await page.wait_for_timeout(120)
                dimensions = await page.evaluate('({rows:testTerm.rows,height:document.querySelector("#terminal-screen").getBoundingClientRect().height,width:document.documentElement.scrollWidth})')
                print('mobile layout',width,height,dimensions,flush=True)
                assert dimensions['rows'] >= min_rows, (width,height,dimensions)
                assert dimensions['width'] <= width
                for button in ('#terminal-mobile-keyboard','#terminal-more'):
                    await expect(page.locator(button)).to_be_in_viewport()
                if height == 180: await page.screenshot(path=str(ROOT/'data/terminal-mobile-landscape.png'))
            # Android/browser keyboards may resize only visualViewport, leaving innerHeight intact.
            await page.set_viewport_size({'width':844,'height':780})
            await page.evaluate("""() => {
                Object.defineProperties(visualViewport, {height:{configurable:true,get:()=>180}, offsetTop:{configurable:true,get:()=>10}});
                visualViewport.dispatchEvent(new Event('resize'));
            }""")
            await page.wait_for_timeout(120)
            visible = await page.evaluate("({rows:testTerm.rows, top:document.body.getBoundingClientRect().top, bottom:document.body.getBoundingClientRect().bottom, inner:innerHeight})")
            assert visible['inner'] == 780 and visible['rows'] >= 5 and visible['top'] == 10 and visible['bottom'] <= 190, visible
            await page.evaluate("delete visualViewport.height;delete visualViewport.offsetTop;visualViewport.dispatchEvent(new Event('resize'))")
            await page.locator('#terminal-more').tap()
            await expect(page.locator('#terminal-menu')).to_be_visible()
            for selector in ('#terminal-paste','#terminal-copy','#terminal-disconnect','#terminal-retry','#terminal-official'):
                await expect(page.locator(selector)).to_be_visible()
            await page.locator('#terminal-menu-close').tap()
            await expect(page.locator('#terminal-menu')).not_to_be_visible()
            await page.set_viewport_size({'width':393,'height':350})
            await page.locator('#terminal-mobile-keyboard').tap()
            await expect(page.locator('.xterm-helper-textarea')).to_be_focused()
            before = len(inputs)
            await page.locator('[data-terminal-key=tab]').tap()
            await sent('\t', before)
            await expect(page.locator('.xterm-helper-textarea')).to_be_focused()
            # Terminal-mode arrows and one-shot Ctrl should each be emitted once.
            await page.evaluate("new Promise(r=>testTerm.write('\\x1b[?1h',r))")
            before = len(inputs)
            await page.locator('[data-terminal-key=up]').tap()
            await sent('\x1bOA', before)
            await page.evaluate("new Promise(r=>testTerm.write('\\x1b[?1l',r))")
            before = len(inputs)
            await page.locator('[data-terminal-key=up]').tap()
            await sent('\x1b[A', before)
            before = len(inputs)
            await page.locator('[data-terminal-key=ctrl]').tap()
            await page.locator('.xterm-helper-textarea').press('b')
            await page.locator('.xterm-helper-textarea').press('x')
            await sent('\x02x', before)
            # Ctrl digits/punctuation must use terminal mappings, never accidental XOFF.
            for char, control in (('3','\x1b'),('4','\x1c'),('6','\x1e'),('8','\x7f'),('[','\x1b'),('\\','\x1c'),(']','\x1d'),('@','\x00'),('_','\x1f'),('?','\x7f'),('1','1'),('.','.')):
                before = len(inputs)
                await page.locator('[data-terminal-key=ctrl]').tap()
                await page.locator('.xterm-helper-textarea').press_sequentially(char)
                await sent(control, before)
            # IME candidates must not race a shortcut Enter; committed text appears once.
            cdp = await context.new_cdp_session(page)
            before = len(inputs)
            await cdp.send('Input.imeSetComposition', {'text':'ni','selectionStart':2,'selectionEnd':2})
            await page.locator('[data-terminal-key=enter]').tap()
            await page.wait_for_timeout(100)
            assert not ''.join(x['data'] for x in inputs[before:]), inputs[before:]
            await cdp.send('Input.insertText', {'text':'你'})
            await sent('你', before)
            await page.locator('[data-terminal-key=enter]').tap()
            await sent('你\r', before)
            # A read failure and lost write ACK resume the existing shell; no input replay.
            fail_next = True
            for _ in range(100):
                if any(r.get('wait') is False for r in reads): break
                await asyncio.sleep(.05)
            assert any(r.get('wait') is False for r in reads), 'HTTP resume must use an immediate state read'
            await expect(page.locator('#terminal-led')).to_have_class('terminal-led connected')
            recoveries = sum(r.get('wait') is False for r in reads)
            before = len(inputs); fail_write = True
            await page.locator('.xterm-helper-textarea').press('z')
            for _ in range(100):
                if sum(r.get('wait') is False for r in reads) > recoveries: break
                await asyncio.sleep(.05)
            assert sum(r.get('wait') is False for r in reads) > recoveries, 'Lost write ACK must trigger recovery'
            await expect(page.locator('#terminal-led')).to_have_class('terminal-led connected')
            await expect(page.locator('#terminal-status')).to_contain_text('已恢复',timeout=10000)
            await page.wait_for_timeout(150)
            assert ''.join(x['data'] for x in inputs[before:]) == 'z', inputs[before:]
            assert len(opens) == 1, opens
            await page.screenshot(path=str(ROOT/'data/terminal-mobile-portrait.png'))
            # Presentation fixtures only; no commands are sent to a shell.
            await page.evaluate("""() => new Promise(resolve => {
                testTerm.reset();
                document.querySelector('#terminal-input-notice').textContent = '';
                document.querySelector('.terminal-keyboard').scrollLeft = 0;
                testTerm.write('\\x1b[38;2;160;219;194mcharles@worker-0\\x1b[0m \\x1b[38;2;145;160;181m~/workspace\\x1b[0m\\r\\n$ ls -lh\\r\\n\\x1b[38;2;137;183;236mcheckpoints/  configs/  datasets/\\x1b[0m\\r\\ntrain.py      README.md\\r\\n\\r\\n$ python --version\\r\\nPython 3.11.9\\r\\n\\r\\n$ ', resolve);
            })""")
            await page.set_viewport_size({'width':393,'height':780})
            await page.wait_for_timeout(150)
            await page.screenshot(path=str(ROOT/'data/terminal-polish-portrait.png'))
            await page.locator('#terminal-more').tap()
            await page.screenshot(path=str(ROOT/'data/terminal-polish-tools.png'))
            await page.locator('#terminal-connection summary').tap()
            await page.screenshot(path=str(ROOT/'data/terminal-polish-connection.png'))
            await page.locator('#terminal-menu-close').tap()
            await page.set_viewport_size({'width':844,'height':180})
            await page.wait_for_timeout(150)
            await page.screenshot(path=str(ROOT/'data/terminal-polish-landscape.png'))
            assert not errors, errors
            await browser.close()
            print('Mobile terminal passed: touch layout, IME, cursor modes, Ctrl, same-shell HTTP recovery and no replay.')
    finally:
        server.shutdown();server.server_close()

if __name__ == '__main__': asyncio.run(main())
