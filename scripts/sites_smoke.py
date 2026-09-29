"""Site switching and request identity checks against mocked APIs only."""
import asyncio
import functools
import threading
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
from browser_support import browser_launch_options
from playwright.async_api import async_playwright,expect
from v2_browser_smoke import Handler,ROOT

async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    now=datetime.now(timezone.utc).isoformat();requests=[];writing=asyncio.Event();release=asyncio.Event()
    async def api(route):
        request=route.request;site=request.headers.get('x-monitor-site')
        assert site in ('sensecore','maoshanwang'), 'Every API request must name its site'
        path=request.url.split('/api/')[1].split('?')[0];requests.append((site,path))
        task={'id':'ACP:shared','kind':'ACP','name':'charles '+site,'cluster':site+' cluster','state':'running','gpu_count':1,'replicas':1,'can_stop':True,'metrics':{}}
        if path=='state':
            data={'site':{'id':site},'status':'ok','connected':True,'updated_at':now,'interval_seconds':60,'tasks':[task],'waiting_tasks':[], 'clusters':[], 'source_status':{k:{'status':'ok','updated_at':now} for k in ('CCI','ACP','CCI_queue','ACP_queue')}}
        elif path=='history':data={'series':[]}
        elif path=='acp/options':data={'template':{'id':task['id'],'name':task['name']},'gpu_options':[1],'default_gpus_per_replica':1,'default_command':'echo '+site,'command_source':{'id':task['id'],'name':task['name']}}
        elif path=='logs/targets':data={'task_id':task['id'],'workers':[{'name':'worker','containers':['pytorch']}]}
        elif path=='logs':data={'task_id':task['id'],'worker':'worker','container':'pytorch','text':site+' logs','entries':1,'updated_at':now}
        elif path=='acp/preview':data={'preview_id':'b'*32,'expires_at':now,'template':{'id':task['id'],'name':task['name']},'cluster':{'id':'gpu','name':site},'replicas':1,'gpus_per_replica':1,'total_gpus':1,'vcpu_per_replica':12,'memory_gib_per_replica':64,'command':'echo '+site,'will_queue':False}
        elif path=='acp/create':
            writing.set();await release.wait();data={'id':'ACP:created','message':'已提交测试响应'}
        else:raise AssertionError(path)
        if path=='acp/options' and site=='sensecore':data.update(partitions=[{'id':'a','name':'AFS a'}],partition={'id':'a','name':'AFS a'},template_config={'images':['image:a'],'afs_mounts':[]})
        await route.fulfill(json=data)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(**browser_launch_options())
            page=await browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)));await page.route('**/api/**',api)
            base=f'http://127.0.0.1:{server.server_port}/'
            await page.goto(base+'#acp',wait_until='networkidle')
            await expect(page.locator('#current-site')).to_have_text('maoshanwang')
            await expect(page.locator('#acp-command')).to_have_value('echo maoshanwang')
            await page.locator('[data-site-switch]').last.click()
            await expect(page.locator('#current-site')).to_have_text('sensecore')
            await expect(page.locator('#acp-command')).to_have_value('echo sensecore')
            assert await page.evaluate('location.hash')=='#acp'
            await page.locator('.nav-item[href="#tasks"]').click()
            await expect(page.locator('#detail-title')).to_have_text('charles sensecore')
            await page.locator('#detail-tab-logs').click()
            await expect(page.locator('#logs-output')).to_have_text('sensecore logs')
            # A pending write on maoshanwang must not appear on sensecore.
            await page.evaluate("localStorage.setItem('charles-monitor.acp.create.pending',JSON.stringify({preview_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}))")
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#acp-create-resume')).not_to_be_visible()
            await page.locator('.nav-item[href="#acp"]').click()
            await page.locator('#acp-preview-button').click()
            await expect(page.locator('#acp-create-dialog')).to_be_visible()
            await page.locator('#acp-create-confirm').click()
            await asyncio.wait_for(writing.wait(),5)
            for button in await page.locator('[data-site-switch]').all():await expect(button).to_be_disabled()
            release.set()
            await expect(page.locator('#acp-create-dialog')).not_to_be_visible()
            await expect(page.locator('[data-site-switch]').first).to_be_enabled()
            assert await page.evaluate("localStorage.getItem('charles-monitor.acp.create.pending.sensecore')") is None
            await page.locator('[data-site-switch]').first.click()
            await expect(page.locator('#current-site')).to_have_text('maoshanwang')
            await expect(page.locator('#acp-create-resume')).to_be_visible()
            await page.go_back()
            await expect(page.locator('#current-site')).to_have_text('sensecore')
            await expect(page.locator('#acp-create-resume')).not_to_be_visible()
            for width in (1024,768,390):
                await page.set_viewport_size({'width':width,'height':900})
                await expect(page.locator('#current-site')).to_be_visible()
                await expect(page.locator('[data-site-switch]').first).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
            assert ('sensecore','logs') in requests and ('maoshanwang','acp/options') in requests
            assert not errors,errors
            await browser.close()
            print('Sites passed: immutable request site, command/log refresh, independent pending writes, disabled switch during cloud write, back navigation and responsive switch.')
    finally:release.set();server.shutdown();server.server_close()

asyncio.run(main())
