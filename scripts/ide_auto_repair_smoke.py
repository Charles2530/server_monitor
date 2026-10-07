"""Local-only integration: actual proxy tickets/CSP/cookies, fixture cloud APIs."""
import asyncio
import functools
import secrets
import threading
from collections import Counter
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
    html = '<html><head><script id="vscode-workbench-web-configuration" type="application/json">{}</script></head><body><main class="monaco-workbench">Fixture</main><textarea aria-label="Draft"></textarea></body></html>'
    proxy = EditorProxy(12345,'remote=private',base,transport=httpx.MockTransport(lambda r:httpx.Response(200,text=html,headers={'content-type':'text/html'})))
    await proxy.start()
    calls, jobs = Counter(), {}
    missing, fail_entry, fail_api, pending_prepare, expire_entries = False, True, False, False, False
    data = {'site':'maoshanwang','task_id':'CCI:job','instance':'pod','container':'main'}
    async def api(route):
        nonlocal missing
        action=urlparse(route.request.url).path.rsplit('/',1)[-1]
        calls[action]+=1
        if fail_api:
            await route.abort('connectionfailed'); return
        if action == 'status':
            operation=jobs[route.request.post_data_json['request_id']]
            if pending_prepare:
                await route.fulfill(json={'pending':True,'message':'正在准备编辑器…'}); return
            if operation == 'recover' and missing:
                result={'pending':False,'error':'编辑器连接已过期'}
            else:
                result={'pending':False,'result':{**data,'port':proxy.port}}
                if operation=='targets': result['result']['targets']=[data]
                if operation=='open':
                    result['result']['ticket']=proxy.ticket()
                    if expire_entries:
                        proxy.browser_keys.clear()
                        proxy.tickets[result['result']['ticket']]-=120
                    missing=False
        else:
            key=secrets.token_hex(8); jobs[key]=action; result={'request_id':key}
        await route.fulfill(json=result)
    async def entry(route):
        nonlocal fail_entry
        if fail_entry:
            fail_entry=False
            await route.abort('connectionrefused')
        else: await route.continue_()
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(**browser_launch_options())
            page=await browser.new_page(viewport={'width':1440,'height':900})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            await page.add_init_script('window.testNow=0;const originalNow=Date.now;Date.now=()=>originalNow()+window.testNow;')
            await page.route('**/api/**',api)
            await page.route(proxy.origin+'/_ilab_open/**',entry)
            await page.goto(base+'/ide?task_id=CCI:job')
            status=page.locator('#ide-status')
            await expect(page.locator('#ide-frame')).to_be_visible()
            await page.locator('#ide-retry').click()
            await expect(status).to_have_attribute('data-state','ready',timeout=10000)
            assert calls['open']==2, calls
            draft=page.frame_locator('#ide-frame').get_by_role('textbox',name='Draft')
            await draft.fill('keep unsaved work')
            old_src=await page.locator('#ide-frame').get_attribute('src')
            # Simulate lost proxy authentication and session after monitor restart.
            proxy.cookie_name='fixture_after_restart';proxy.browser_keys.clear();missing=True
            await page.evaluate('window.dispatchEvent(new Event("online"))')
            await expect(status).to_have_attribute('data-state','ready',timeout=10000)
            await expect(draft).to_have_value('keep unsaved work')
            assert calls['open']==3, calls
            assert await page.locator('#ide-frame').get_attribute('src')==old_src
            assert (await page.request.get(proxy.origin+'/_ilab_frame_status.js')).status==200
            assert await page.locator('iframe[data-ide-reauth]').count()==0
            # Manual reconnect uses full preparation and reauth, without destroying buffers.
            await page.locator('#ide-retry').click()
            await expect(status).to_have_attribute('data-state','ready',timeout=10000)
            assert calls['open']==4, calls
            await expect(draft).to_have_value('keep unsaved work')
            # Offline never starts cloud/monitor repair; coming back triggers it.
            await page.evaluate('Object.defineProperty(navigator,"onLine",{configurable:true,get:()=>false});window.dispatchEvent(new Event("offline"))')
            before=dict(calls)
            await page.locator('#ide-retry').click()
            assert dict(calls)==before
            await expect(draft).to_have_value('keep unsaved work')
            await page.evaluate('Object.defineProperty(navigator,"onLine",{configurable:true,get:()=>true});window.dispatchEvent(new Event("online"))')
            await expect(status).to_have_attribute('data-state','ready',timeout=10000)
            # Initial API failure recovers automatically after its backoff.
            fail_api=True
            await page.reload()
            await expect(status).to_have_attribute('data-state','error',timeout=15000)
            fail_api=False
            await page.evaluate('window.testNow+=130000;window.dispatchEvent(new Event("focus"))')
            await expect(status).to_have_attribute('data-state','ready',timeout=10000)
            # A page that stops answering workbench probes must be replaced,
            # retaining the old document's buffer for recovery.
            await draft.fill('draft in unresponsive workbench')
            await page.frame_locator('#ide-frame').locator('main').evaluate('el=>el.classList.remove("monaco-workbench")')
            await page.evaluate('window.dispatchEvent(new Event("online"))')
            await expect(status).to_have_attribute('data-state','ready',timeout=15000)
            await expect(page.locator('#ide-preserved-frame')).to_be_attached(timeout=15000)
            await expect(page.frame_locator('#ide-preserved-frame').locator('textarea')).to_have_value('draft in unresponsive workbench')
            await page.get_by_role('combobox',name='切换保留的编辑内容').select_option(index=1)
            await expect(page.locator('#ide-preserved-frame')).to_be_visible()
            await page.get_by_role('combobox',name='切换保留的编辑内容').select_option(index=0)
            await expect(page.locator('#ide-frame')).to_be_visible()
            # Permanent remote failure keeps the workbench DOM mounted, but must
            # still rebuild instead of claiming cookie refresh alone repaired it.
            await draft.fill('draft before permanent remote failure')
            await page.frame_locator('#ide-frame').locator('body').evaluate('el=>{ const box=document.createElement("div");box.className="monaco-dialog-box";box.innerHTML="<p class=dialog-message-detail>Cannot reconnect. Please reload the window.</p>";el.append(box); }')
            await page.evaluate('window.dispatchEvent(new Event("online"))')
            await expect(page.locator('#ide-preserved-frame-2')).to_be_attached(timeout=10000)
            await expect(status).to_have_attribute('data-state','ready',timeout=10000)
            await expect(page.frame_locator('#ide-preserved-frame-2').locator('textarea')).to_have_value('draft before permanent remote failure')
            await page.locator('#ide-focus').click()
            await expect(page.locator('#ide-preserved-select')).to_be_visible()
            await page.locator('#ide-focus').click()
            # No manual click is needed when the initial iframe refuses a connection.
            fail_entry=True
            await page.reload()
            await expect(page.locator('#ide-frame')).to_be_visible()
            await page.evaluate('window.testNow+=61000;window.dispatchEvent(new Event("focus"))')
            await expect(status).to_have_attribute('data-state','ready',timeout=10000)
            # Going offline during a pending preparation stops its 400ms poll.
            pending_prepare=True
            await page.reload()
            await expect(status).to_contain_text('正在准备编辑器')
            await page.evaluate('Object.defineProperty(navigator,"onLine",{configurable:true,get:()=>false});window.dispatchEvent(new Event("offline"))')
            snapshot=dict(calls)
            await page.wait_for_timeout(1200)
            assert dict(calls)==snapshot, (snapshot,dict(calls))
            pending_prepare=False
            await page.evaluate('Object.defineProperty(navigator,"onLine",{configurable:true,get:()=>true});window.dispatchEvent(new Event("online"))')
            await expect(status).to_have_attribute('data-state','ready',timeout=10000)
            # Repeated failed entries must back off even when /open succeeds.
            expire_entries=True
            await page.reload()
            await expect(status).to_contain_text('5 秒后自动修复',timeout=10000)
            await page.evaluate('window.testNow+=6000;window.dispatchEvent(new Event("focus"))')
            await expect(status).to_contain_text('10 秒后自动修复',timeout=10000)
            await page.evaluate('window.testNow+=11000;window.dispatchEvent(new Event("focus"))')
            await expect(status).to_contain_text('20 秒后自动修复',timeout=10000)
            assert not errors,errors
            await browser.close()
            print('PASS manual/automatic failed-entry repair, lost-session reauth, drafts across permanent/unresponsive workbench replacement, offline cancellation/resume, startup retry, growing backoff')
    finally:
        await proxy.close();server.shutdown();server.server_close()

if __name__=='__main__':asyncio.run(main())
