"""Desktop CCI editor UI checks; API and editor traffic are mocked, never cloud."""
import asyncio
import functools
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options
from v2_browser_smoke import ROOT, Handler


class IDEHandler(Handler):
    def do_GET(self):
        if self.path.split('?', 1)[0] == '/ide':
            self.path = '/static/ide.html'
        super().do_GET()


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(IDEHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    now = datetime.now(timezone.utc).isoformat()
    errors, calls, opens, jobs = [], [], [], {}
    mode, site, serial = 'single', 'maoshanwang', 0
    progress_data = {'stage': 'download_editor', 'downloaded_bytes': 4 * 1048576, 'total_bytes': 8 * 1048576}
    task = {'id': 'CCI:job', 'kind': 'CCI', 'name': 'charles <img src=x onerror=alert(1)>', 'state': 'running', 'replicas': 1, 'gpu_count': 1, 'metrics': {}}

    def targets():
        pairs = [('pod-1', 'main'), ('pod-1', 'sidecar'), ('pod-2', 'main')]
        return {'site': site, 'task_id': 'CCI:job', 'name': task['name'], 'targets': [
            {'instance': i, 'container': c, 'url': 'https://untrusted.example/ignored'}
            for i, c in (pairs if mode == 'multi' else [] if mode == 'empty' else pairs[:1])]}

    async def api(route):
        nonlocal serial
        request = route.request
        path = urlparse(request.url).path
        body = request.post_data_json if request.method == 'POST' else None
        if path.startswith('/api/cci/ide/'):
            assert request.method == 'POST'
            assert request.headers.get('x-monitor-request') == '1'
            assert request.headers.get('x-monitor-site') == site
            calls.append((path, body))
            if path.endswith('/targets'):
                serial += 1
                job = f'targets-{serial}'
                jobs[job] = targets()
                result = {'request_id': job}
            elif path.endswith('/recover'):
                if mode == 'recovernetwork':
                    await route.abort('connectionfailed')
                    return
                serial += 1
                job = f'recover-{serial}'
                jobs[job] = {'site': site, **body, 'port': 43219}
                result = {'request_id': job}
            elif path.endswith('/open'):
                opens.append(body)
                serial += 1
                job = f'open-{serial}'
                jobs[job] = {'site': site, 'task_id': 'CCI:job', 'name': task['name'],
                             'instance': body['instance'], 'container': body['container'],
                             'port': 43219, 'ticket': 'mock_ticket-1234567890abcdef1234567890abcdef12345678'}
                if mode == 'invalidport': jobs[job]['port'] = '43219'
                if mode == 'invalidticket': jobs[job]['ticket'] = '../unsafe?ticket'
                if mode == 'wrongsite': jobs[job]['site'] = 'other'
                if mode == 'wrongtask': jobs[job]['task_id'] = 'CCI:other'
                if mode == 'wrongtarget': jobs[job]['container'] = 'other'
                result = {'request_id': job}
            else:
                job = body['request_id']
                if mode == 'recoverpending' and job.startswith('recover-'):
                    result = {'pending': True, 'message': '正在恢复编辑器连接…'}
                elif mode == 'recovererror' and job.startswith('recover-'):
                    result = {'pending': False, 'error': '模拟连接暂不可用'}
                elif mode == 'error' and job.startswith('open-'):
                    result = {'pending': False, 'error': '模拟安装失败'}
                elif mode == 'stale_selection' and job.startswith('open-') and jobs[job]['instance'] == 'gone-pod':
                    result = {'pending': False, 'error': '所选 CCI 容器已不可用'}
                elif mode == 'progress' and job.startswith('open-'):
                    result = {'pending': True, 'message': '正在准备编辑器…', 'progress': progress_data}
                elif mode in ('pending', 'late') and job.startswith('open-'):
                    if mode == 'late':
                        await asyncio.sleep(.6)
                        result = {'pending': False, 'result': {**jobs[job], 'ticket': 'stale_ticket-1234567890abcdef1234567890abcdef12345678'}}
                    else: result = {'pending': True, 'message': '正在检查已有编辑器并建立连接，请稍候…'}
                else: result = {'pending': False, 'result': jobs[job]}
        elif path == '/api/state':
            result = {'status': 'ok', 'connected': True, 'updated_at': now, 'interval_seconds': 60,
                      'tasks': [task], 'waiting_tasks': [], 'clusters': [], 'source_status': {}}
        elif path == '/api/history': result = {'series': []}
        elif path == '/api/acp/options':
            result = {'template': {'id': 'template', 'name': 'charles template'}, 'gpu_options': [1], 'default_gpus_per_replica': 1, 'clusters': [], 'cluster_order': []}
        else: raise AssertionError(path)
        try: await route.fulfill(json=result)
        except Exception: pass  # Closing or retrying cancels pending browser requests.

    async def page_for(ctx, path='/ide?site=maoshanwang&task_id=CCI:job', *, keep_selection=False):
        page = await ctx.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        if not keep_selection:
            await page.add_init_script('localStorage.removeItem("ilab-ide-recent:maoshanwang");localStorage.removeItem("ilab-ide-recent:sensecore");')
        await page.add_init_script('window.requestTimeouts=[];const originalTimeout=window.setTimeout;window.setTimeout=(fn,ms,...args)=>{window.requestTimeouts.push(ms);return originalTimeout(fn,ms,...args)};')
        await page.goto(base + path)
        return page

    async def editor_page(route):
        if urlparse(route.request.url).path == '/_ilab_frame_status.js':
            await route.fulfill(content_type='application/javascript', body=(ROOT / 'static/ide-frame-status.js').read_text())
            return
        workbench_class = '' if mode == 'framepending' else 'monaco-workbench'
        await route.fulfill(content_type='text/html', body=f'<title>Mock editor</title><script src="/_ilab_frame_status.js" data-parent-origin="{base}"></script><main class="{workbench_class}">Editor fixture</main><textarea aria-label="Unsaved document"></textarea>')

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            ctx = await browser.new_context(viewport={'width': 1440, 'height': 1000})
            await ctx.route('**/api/**', api)
            await ctx.route('http://127.0.0.1:43219/**', editor_page)
            page = await page_for(ctx, '/?site=maoshanwang#tasks')
            await expect(page.locator('#cci-terminal-selected')).to_be_visible()
            link = page.locator('#cci-ide-selected')
            await expect(link).to_be_visible()
            await expect(link).to_have_attribute('href', '/ide?site=maoshanwang&task_id=CCI%3Ajob')
            await expect(link).to_have_attribute('target', '_blank')
            await expect(page.locator('#detail-action-note')).not_to_contain_text('云控制台终端')
            for override in ({'kind': 'ACP'}, {'state': 'waiting'}, {'name': 'someone else'}):
                await page.evaluate('(task)=>document.dispatchEvent(new CustomEvent("monitor-selection",{detail:task}))', {**task, **override})
                await expect(link).to_be_hidden()
            await page.evaluate('(task)=>document.dispatchEvent(new CustomEvent("monitor-selection",{detail:task}))', task)
            await page.set_viewport_size({'width': 760, 'height': 900})
            await expect(link).to_be_hidden()
            await page.close()

            site, mode = 'maoshanwang', 'single'
            page = await page_for(ctx)
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            discovered_at = await page.evaluate('JSON.parse(localStorage.getItem("ilab-ide-recent:maoshanwang")).saved_at')
            await page.close()
            before = len(calls)
            page = await page_for(ctx, keep_selection=True)
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            assert not any(path.endswith('/targets') for path, _ in calls[before:]), 'Healthy reopen must go directly to authenticated open'
            assert await page.evaluate('JSON.parse(localStorage.getItem("ilab-ide-recent:maoshanwang")).saved_at') == discovered_at, 'Fast reuse must not extend discovery freshness'
            await page.evaluate('localStorage.setItem("ilab-ide-recent:maoshanwang",JSON.stringify({task_id:"CCI:job",instance:"gone-pod",container:"main",saved_at:Date.now()}))')
            await page.close()
            mode = 'stale_selection'
            before = len(calls)
            page = await page_for(ctx, keep_selection=True)
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            assert any(path.endswith('/targets') for path, _ in calls[before:])
            assert opens[-1]['instance'] == 'pod-1'
            await page.close()

            for site in ('maoshanwang', 'sensecore'):
                for mode in ('single', 'multi', 'empty', 'invalidport', 'invalidticket', 'wrongsite', 'wrongtask', 'wrongtarget', 'error'):
                    before = len(opens)
                    page = await page_for(ctx, f'/ide?site={site}&task_id=CCI:job')
                    if mode == 'multi':
                        await expect(page.locator('#ide-picker')).to_be_visible()
                        await expect(page.locator('#ide-instance option')).to_have_count(2)
                        await expect(page.locator('#ide-container option')).to_have_count(2)
                        await page.locator('#ide-instance').select_option('pod-2')
                        await expect(page.locator('#ide-container option')).to_have_count(1)
                        await page.locator('#ide-open').click()
                    if mode in ('single', 'multi'):
                        frame = page.locator('#ide-frame')
                        await expect(frame).to_be_visible()
                        await expect(frame).to_have_attribute('src', 'http://127.0.0.1:43219/_ilab_open/mock_ticket-1234567890abcdef1234567890abcdef12345678')
                        assert opens[-1] == {'task_id': 'CCI:job', 'instance': 'pod-2' if mode == 'multi' else 'pod-1', 'container': 'main'}
                        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        box = await frame.bounding_box()
                        assert box['height'] >= 820 and box['y'] <= 160, box
                        await expect(page.locator('#ide-task img')).to_have_count(0)
                        if mode == 'single':
                            await expect(page.locator('#ide-picker')).to_be_hidden()
                            await page.screenshot(path='/tmp/cci-ide-desktop.png')
                    else:
                        await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'error')
                        await expect(page.locator('#ide-frame')).to_be_hidden()
                        if mode == 'empty': assert len(opens) == before
                        if mode == 'error':
                            await expect(page.locator('#ide-status')).to_contain_text('模拟安装失败')
                            mode = 'single'
                            await page.locator('#ide-retry').click()
                            await expect(page.locator('#ide-frame')).to_be_visible()
                    assert 15000 in await page.evaluate('window.requestTimeouts')
                    await page.close()

            site, mode = 'maoshanwang', 'framepending'
            page = await page_for(ctx)
            await expect(page.locator('#ide-frame')).to_be_visible()
            await expect(page.locator('#ide-status')).to_contain_text('正在加载编辑器')
            await page.evaluate('window.dispatchEvent(new MessageEvent("message", {origin:"http://attacker.test",source:document.getElementById("ide-frame").contentWindow,data:{type:"ilab-editor-ready"}}))')
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'loading')
            await page.frame_locator('#ide-frame').locator('main').evaluate('(el)=>el.classList.add("monaco-workbench")')
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            await expect(page.locator('#ide-status')).to_contain_text('秒')
            assert 0 <= float(await page.locator('#ide-status').get_attribute('data-ready-seconds')) < 10
            await page.close()

            site, mode = 'maoshanwang', 'single'
            page = await page_for(ctx)
            frame = page.locator('#ide-frame')
            await expect(frame).to_be_visible()
            draft = page.frame_locator('#ide-frame').get_by_role('textbox')
            await draft.fill('unsaved editor state')
            original_src = await frame.get_attribute('src')
            before = len(opens)
            await page.set_viewport_size({'width': 700, 'height': 900})
            await expect(frame).to_be_visible()
            await expect(draft).to_have_value('unsaved editor state')
            await page.set_viewport_size({'width': 1440, 'height': 1000})
            await page.locator('#ide-retry').click()
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            assert any(path.endswith('/recover') for path, _ in calls)
            assert len(opens) == before
            await expect(frame).to_have_attribute('src', original_src)
            await expect(draft).to_have_value('unsaved editor state')
            mode = 'recovererror'
            await page.locator('#ide-retry').click()
            await expect(page.locator('#ide-status')).to_contain_text('模拟连接暂不可用')
            await expect(frame).to_be_visible()
            await expect(draft).to_have_value('unsaved editor state')
            mode = 'recovernetwork'
            await page.locator('#ide-retry').click()
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'error')
            await expect(draft).to_have_value('unsaved editor state')
            mode = 'recoverpending'
            before_recover = sum(path.endswith('/recover') for path, _ in calls)
            await page.locator('#ide-retry').click()
            await expect(page.locator('#ide-status')).to_contain_text('正在恢复')
            await page.evaluate('for(let i=0;i<10;i++){window.dispatchEvent(new Event("focus"));window.dispatchEvent(new Event("online"));document.dispatchEvent(new Event("visibilitychange"));}')
            await expect(draft).to_have_value('unsaved editor state')
            assert sum(path.endswith('/recover') for path, _ in calls) == before_recover + 1
            mode = 'single'
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            await page.evaluate('window.dispatchEvent(new Event("online"))')
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'ready')
            await expect(draft).to_have_value('unsaved editor state')
            assert len(opens) == before
            await page.close()

            site, mode = 'maoshanwang', 'progress'
            page = await page_for(ctx)
            await expect(page.locator('#ide-progress')).to_be_visible()
            await expect(page.locator('#ide-progress-bar')).to_have_attribute('value', '50')
            await expect(page.locator('#ide-progress-value')).to_have_text('50%')
            await expect(page.locator('#ide-progress-detail')).to_contain_text('4.0 MiB / 8.0 MiB')
            await page.screenshot(path=str(ROOT / 'data/ide-download-progress.png'))
            progress_data = {'stage': 'download_editor', 'downloaded_bytes': 5 * 1048576, 'total_bytes': None}
            await expect(page.locator('#ide-progress-detail')).to_contain_text('已下载 5.0 MiB')
            await expect(page.locator('#ide-progress-bar')).not_to_have_attribute('value', '50')
            assert await page.locator('#ide-progress-bar').get_attribute('value') is None
            progress_data = {'stage': 'extract_editor'}
            await expect(page.locator('#ide-progress-stage')).to_have_text('解压编辑器')
            progress_data = {'stage': 'download_codex', 'downloaded_bytes': 1048576, 'total_bytes': 4 * 1048576}
            await expect(page.locator('#ide-progress-stage')).to_have_text('下载 Codex 插件')
            await expect(page.locator('#ide-progress-bar')).to_have_attribute('value', '25')
            mode = 'error'
            await expect(page.locator('#ide-status')).to_have_attribute('data-state', 'error')
            await expect(page.locator('#ide-progress')).to_be_hidden()
            mode = 'single'
            await page.locator('#ide-retry').click()
            await expect(page.locator('#ide-frame')).to_be_visible()
            await expect(page.locator('#ide-progress')).to_be_hidden()
            await page.close()

            site, mode = 'maoshanwang', 'pending'
            page = await page_for(ctx)
            await expect(page.locator('#ide-status')).to_contain_text('建立连接')
            await expect(page.locator('#ide-status')).not_to_contain_text('首次')
            await expect(page.locator('#ide-frame')).to_be_hidden()
            mode = 'single'
            await page.locator('#ide-retry').click()
            await expect(page.locator('#ide-frame')).to_be_visible()
            await page.close()
            mode = 'late'
            page = await page_for(ctx)
            await expect(page.locator('#ide-status')).to_contain_text('建立连接')
            await page.wait_for_timeout(100)
            mode = 'single'
            await page.locator('#ide-retry').click()
            await expect(page.locator('#ide-frame')).to_have_attribute('src', 'http://127.0.0.1:43219/_ilab_open/mock_ticket-1234567890abcdef1234567890abcdef12345678')
            await page.wait_for_timeout(700)
            await expect(page.locator('#ide-frame')).not_to_have_attribute('src', 'http://127.0.0.1:43219/_ilab_open/stale_ticket-1234567890abcdef1234567890abcdef12345678')
            await page.close()

            for options in ({'viewport': {'width': 390, 'height': 844}}, {'viewport': {'width': 1440, 'height': 900}, 'user_agent': 'Mozilla/5.0 Android Mobile'}, {'viewport': {'width': 1440, 'height': 900}, 'user_agent': 'Mozilla/5.0 iPad'}, {'viewport': {'width': 1440, 'height': 900}, 'has_touch': True}):
                mobile = await browser.new_context(**options)
                await mobile.route('**/api/**', api)
                before = len(calls)
                page = await page_for(mobile)
                await expect(page.locator('#ide-status')).to_contain_text('仅支持桌面')
                await page.wait_for_timeout(100)
                assert len(calls) == before
                await expect(page.locator('#ide-frame')).to_be_hidden()
                await page.close()
                page = await page_for(mobile, '/#tasks')
                await expect(page.locator('#cci-terminal-selected')).to_have_attribute('href', '/terminal?site=maoshanwang&task_id=CCI%3Ajob')
                await expect(page.locator('#cci-ide-selected')).to_have_attribute('hidden', '')
                await expect(page.locator('#cci-ide-selected')).to_be_hidden()
                await mobile.close()
            before = len(calls)
            page = await page_for(ctx, '/ide?site=maoshanwang&task_id=ACP:job')
            await expect(page.locator('#ide-status')).to_contain_text('CCI')
            assert len(calls) == before
            await page.close()
            assert not errors, errors
            await ctx.close()
            await browser.close()
            print('CCI IDE UI passed: desktop entry, both sites, target selection, pending/error/retry, stale results, persistent draft across resize and recovery/error/network failure, deduplicated resume events, isolated iframe URL validation, mobile no API, layout and no page errors. All APIs mocked; no cloud calls.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
