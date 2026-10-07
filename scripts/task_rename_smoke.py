"""Task rename UI against simulated APIs only; never writes to cloud tasks."""
import asyncio
import functools
import threading
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect
from v2_browser_smoke import Handler

ROOT = Path(__file__).resolve().parent.parent


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    calls, errors = [], []
    mode = {'error': None, 'slow': False, 'reads': 0}
    gate = asyncio.Event()
    before = datetime.now(timezone.utc) - timedelta(minutes=1)
    tasks = [{'id': kind + ':job-' + str(i), 'name': 'charles_' + kind, 'kind': kind, 'state': 'running', 'metrics': {}}
             for i, kind in enumerate(('CCI', 'ACP'))]
    snapshot = {'status':'ok', 'connected':True, 'updated_at':before.isoformat(), 'tasks':tasks,
                'waiting_tasks':[], 'clusters':[], 'source_status':{'CCI':{'status':'ok'}, 'ACP':{'status':'ok'}}}

    async def api(route):
        request = route.request
        path = urlsplit(request.url).path
        if path == '/api/tasks/rename':
            assert request.method == 'POST'
            assert request.headers['x-monitor-request'] == '1'
            data = request.post_data_json
            calls.append((request.headers['x-monitor-site'], data))
            if mode['slow']: await asyncio.wait_for(gate.wait(), 8)
            if mode['error']:
                await route.fulfill(status=409, json={'error':'名称已变化' if mode['error']=='name_changed' else '结果尚未确认', 'code':mode['error']}); return
            data = {'site':request.headers['x-monitor-site'], 'task_id':data['task_id'], 'name':data['display_name'],
                    'expected_name':data['expected_name'], 'verified_at':datetime.now(timezone.utc).isoformat()}
        elif path == '/api/state':
            mode['reads'] += 1
            data = snapshot
        elif path == '/api/history':
            data = {'series':[{'metric':'cpu','label':'worker','points':[[before.timestamp(),30]]}]}
        elif path == '/api/tokenhub/state': data = {'status':'disconnected','connected':False,'sections':{}}
        elif path == '/api/acp/options': data = {'template':{'id':'ACP:job-1','name':'charles_ACP'},'gpu_options':[1]}
        else: data = {}
        await route.fulfill(json=data)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            base = f'http://127.0.0.1:{server.server_port}/'
            for site in ('maoshanwang','sensecore'):
                await page.goto(base + '?site=' + site + '#tasks', wait_until='domcontentloaded')
                edit = page.get_by_role('button',name='重命名任务',exact=True)
                dialog = page.locator('#task-rename-dialog')
                field = page.get_by_label('新任务名称',exact=True)
                save = page.locator('#task-rename-save')
                await expect(edit).to_be_visible()
                title_box = await page.locator('#detail-title').bounding_box()
                edit_box = await edit.bounding_box()
                assert edit_box['x'] >= title_box['x'] + title_box['width'] - 1
                await edit.click()
                await expect(field).to_have_value('charles_CCI')
                await field.fill('charles_cancelled')
                await page.keyboard.press('Escape')
                await expect(dialog).not_to_be_visible()
                assert len(calls) == (0 if site == 'maoshanwang' else 1)
                await edit.click()
                await field.fill('other_user')
                await save.click()
                await expect(dialog).to_contain_text('charles')
                assert len(calls) == (0 if site == 'maoshanwang' else 1)
                await field.fill('charles_new_' + site)
                await page.keyboard.press('Enter')
                await expect(dialog).not_to_be_visible()
                await expect(page.locator('#detail-title')).to_have_text('charles_new_' + site)
                await expect(page.locator('.task-card.selected')).to_contain_text('charles_new_' + site)
                assert calls[-1][0] == site and calls[-1][1]['task_id'] == 'CCI:job-0'
                # An older poll cannot replace the already verified title.
                await page.evaluate("document.dispatchEvent(new CustomEvent('monitor-refresh'))")
                await expect(page.locator('#detail-title')).to_have_text('charles_new_' + site)
            await page.locator('.task-card').filter(has_text='charles_ACP').click()
            await edit.click()
            await expect(field).to_have_value('charles_ACP')
            mode['slow'] = True
            await field.fill('charles_ACP_renamed')
            await save.click()
            await expect(save).to_be_disabled()
            await expect(page.locator('[data-site-switch]').first).to_be_disabled()
            await page.keyboard.press('Escape')
            await expect(dialog).to_be_visible()
            assert len(calls) == 3
            gate.set()
            await expect(dialog).not_to_be_visible()
            await expect(page.locator('#detail-title')).to_have_text('charles_ACP_renamed')
            await expect(page.locator('[data-site-switch]').first).to_be_enabled()
            assert calls[-1][1]['task_id'] == 'ACP:job-1'
            mode['slow'] = False
            mode['error'] = 'name_changed'
            await edit.click()
            await field.fill('charles_conflict')
            await save.click()
            await expect(dialog).to_contain_text('名称已变化')
            await expect(page.locator('#detail-title')).to_have_text('charles_ACP_renamed')
            await dialog.get_by_role('button',name='取消',exact=True).click()
            mode['error'] = 'outcome_unknown'
            await edit.click()
            await field.fill('charles_uncertain')
            await save.click()
            await expect(dialog.get_by_role('button',name='核对结果',exact=True)).to_be_visible()
            await expect(field).to_be_disabled()
            original = calls[-1][1]
            await page.reload(wait_until='domcontentloaded')
            await page.locator('.task-card').filter(has_text='charles_ACP').click()
            await edit.click()
            await expect(field).to_have_value('charles_uncertain')
            mode['error'] = None
            await dialog.get_by_role('button',name='核对结果',exact=True).click()
            await expect(dialog).not_to_be_visible()
            assert calls[-1][1] == {**original, 'check_only': True}
            await expect(page.locator('#detail-title')).to_have_text('charles_uncertain')
            # Long names and mobile layouts keep the button and form in view.
            tasks[1]['name'] = 'charles_' + 'x' * 220
            snapshot['updated_at'] = datetime.now(timezone.utc).isoformat()
            snapshot['source_status']['ACP']['updated_at'] = snapshot['updated_at']
            await page.evaluate("document.dispatchEvent(new CustomEvent('monitor-refresh'))")
            await expect(page.locator('.task-card').filter(has_text=tasks[1]['name'])).to_be_visible()
            await page.set_viewport_size({'width':390,'height':844})
            await page.locator('.task-card').filter(has_text=tasks[1]['name']).click()
            await edit.click()
            await expect(field).to_have_value('charles_' + 'x' * 220)
            assert await dialog.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            await page.screenshot(path=str(ROOT/'data/task-rename-mobile.png'))
            await dialog.get_by_role('button',name='取消',exact=True).click()
            await page.set_viewport_size({'width':1440,'height':1000})
            await page.locator('.task-card').filter(has_text='charles_CCI').click()
            await edit.click()
            await expect(field).to_have_value('charles_CCI')
            await page.screenshot(path=str(ROOT/'data/task-rename-desktop.png'))
            # Switching selection invalidates unsent editing, without touching the new task.
            await page.evaluate("document.dispatchEvent(new CustomEvent('monitor-selection',{detail:{id:'ACP:job-1',kind:'ACP',name:'charles_ACP'}}))")
            await expect(dialog).not_to_be_visible()
            await page.locator('#theme-toggle').click()
            await edit.click()
            await page.screenshot(path=str(ROOT/'data/task-rename-dark.png'))
            assert await dialog.evaluate('e=>getComputedStyle(e).backgroundColor') != 'rgb(255, 255, 255)'
            await dialog.get_by_role('button',name='取消',exact=True).click()
            assert not errors, errors
            print('Task rename browser checks passed: both sites/kinds, cancel, save, validation, stale polls, conflict, unknown recovery, mobile, selection.')
            await browser.close()
    finally:
        server.shutdown()


asyncio.run(main())
