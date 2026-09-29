"""Partition-bound drafts and previews against local fixtures; no cloud mutations."""
import asyncio
import functools
import threading
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
from browser_support import browser_launch_options
from playwright.async_api import async_playwright,expect
from v2_browser_smoke import Handler,ROOT

async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    now=datetime.now(timezone.utc).isoformat();calls=[];slow_b=False;fail_b=False;slow_preview=False
    async def api(route):
        nonlocal slow_b,fail_b,slow_preview
        req=route.request;path=urlparse(req.url).path;query=parse_qs(urlparse(req.url).query)
        body=req.post_data_json if req.method=='POST' else None
        calls.append((path,body))
        if path=='/api/state':data={'status':'ok','connected':True,'updated_at':now,'tasks':[],'waiting_tasks':[],'clusters':[],'source_status':{}}
        elif path=='/api/acp/options':
            key=query.get('partition_id',['a'])[0]
            if key=='b' and slow_b:await asyncio.sleep(1)
            if key=='b' and fail_b:
                await route.fulfill(status=503,json={'code':'source_unavailable'});return
            data={'template':{'id':'ACP:'+key,'name':'charles_'+key,'created_at':now},'gpu_options':[1,2] if key=='a' else [1,8],'default_gpus_per_replica':1,'default_command':'echo '+key,'command_source':{'id':'ACP:'+key,'name':'charles_'+key},'clusters':[{'id':key+'-gpu','name':key+' GPU','gpu_options':[1,2] if key=='a' else [1,8]}],'partitions':[{'id':k,'name':'AFS '+k,'template':{'id':'ACP:'+k,'name':'charles_'+k}} for k in ('a','b')],'partition':{'id':key,'name':'AFS '+key},'template_config':{'images':['registry/image:'+key],'afs_mounts':[{'zone':key,'id':key+'-disk','subdir':'/','mount_path':'/mnt/'+key}]}}
        elif path=='/api/acp/preview':
            if slow_preview:await asyncio.sleep(1)
            assert body['partition_id']=='b' and body['cluster_id']=='b-gpu'
            data={**body,'preview_id':'c'*32,'expires_at':now,'template':{'id':'ACP:b','name':'charles_b'},'partition':{'id':'b','name':'AFS b'},'template_config':{'images':['registry/image:b'],'afs_mounts':[{'zone':'b','id':'b-disk','subdir':'/','mount_path':'/mnt/b'}]},'cluster':{'id':'b-gpu','name':'b GPU'},'total_gpus':1,'vcpu_per_replica':14,'memory_gib_per_replica':60,'will_queue':False}
        else:raise AssertionError(path)
        await route.fulfill(json=data)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(**browser_launch_options())
            page=await browser.new_page(viewport={'width':1280,'height':950});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            await page.route('**/api/**',api)
            base=f'http://127.0.0.1:{server.server_port}/'
            await page.goto(base+'?site=sensecore#acp',wait_until='networkidle')
            await expect(page.locator('#acp-partition')).to_have_value('a')
            await expect(page.locator('#acp-template-config')).to_contain_text('/mnt/a')
            await page.locator('#acp-command').fill('echo my a draft')
            await page.locator('#acp-cluster').select_option('a-gpu')
            await page.locator('#acp-gpus').select_option('2')
            await page.locator('#acp-partition').select_option('b')
            await expect(page.locator('#acp-command')).to_have_value('echo b')
            await expect(page.locator('#acp-template-config')).to_contain_text('registry/image:b')
            await expect(page.locator('#acp-template-config')).not_to_contain_text('/mnt/a')
            assert await page.locator('#acp-gpus option').all_text_contents()==['1 张','8 张']
            await expect(page.locator('#acp-cluster')).to_have_value('')
            await page.locator('#acp-partition').select_option('a')
            await expect(page.locator('#acp-command')).to_have_value('echo my a draft')
            await expect(page.locator('#acp-cluster')).to_have_value('a-gpu')
            await expect(page.locator('#acp-gpus')).to_have_value('2')
            # A slower B response must never replace a newly selected A configuration.
            slow_b=True
            await page.locator('#acp-partition').select_option('b')
            await expect(page.locator('#acp-preview-button')).to_be_disabled()
            await page.locator('#acp-partition').select_option('a')
            await page.wait_for_timeout(1300)
            await expect(page.locator('#acp-partition')).to_have_value('a')
            await expect(page.locator('#acp-template-config')).to_contain_text('/mnt/a')
            slow_b=False;fail_b=True
            await page.locator('#acp-partition').select_option('b')
            await expect(page.locator('#acp-options-retry')).to_be_visible()
            await expect(page.locator('#acp-preview-button')).to_be_disabled()
            fail_b=False
            await page.locator('#acp-options-retry').click()
            await expect(page.locator('#acp-template-config')).to_contain_text('/mnt/b')
            await page.locator('#acp-cluster').select_option('b-gpu')
            slow_preview=True
            await page.locator('#acp-preview-button').click()
            await page.locator('#acp-partition').select_option('a')
            await page.wait_for_timeout(1300)
            await expect(page.locator('#acp-create-dialog')).not_to_be_visible()
            slow_preview=False
            await page.locator('#acp-partition').select_option('b')
            await expect(page.locator('#acp-cluster')).to_have_value('b-gpu')
            await page.locator('#acp-preview-button').click()
            await expect(page.locator('#acp-create-dialog')).to_be_visible()
            for text in ('AFS b','registry/image:b','/mnt/b'):
                await expect(page.locator('#acp-preview-details')).to_contain_text(text)
            await page.locator('#acp-create-cancel').click()
            await page.locator('.nav-item[href="#overview"]').click()
            await page.locator('.nav-item[href="#acp"]').click()
            await expect(page.locator('#acp-partition')).to_have_value('b')
            for width in (390,360):
                await page.set_viewport_size({'width':width,'height':950})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.evaluate('''() => { const key='charles-monitor.acp.create.pending.sensecore'; localStorage.setItem(key, JSON.stringify({preview_id:'dddddddddddddddddddddddddddddddd'})); window.dispatchEvent(new StorageEvent('storage',{key})); }''')
            await expect(page.locator('#acp-partition')).to_be_disabled()
            await page.locator('#acp-create-resume').click()
            await expect(page.locator('#acp-create-confirm')).to_be_enabled()
            await page.locator('#acp-create-cancel').click()
            await page.evaluate('''() => { const key='charles-monitor.acp.create.pending.sensecore'; localStorage.removeItem(key); window.dispatchEvent(new StorageEvent('storage',{key})); }''')
            await expect(page.locator('#acp-partition')).to_be_enabled()
            await page.goto(base+'?site=maoshanwang#acp',wait_until='networkidle')
            await expect(page.locator('#acp-partition')).to_have_count(0)
            assert await page.locator('#acp-create-form input, #acp-create-form select, #acp-create-form textarea').count()==5
            assert not errors,errors
            assert not any(path.endswith(('/create','/stop')) for path,_ in calls)
            await browser.close()
            print('Partitions passed: isolated templates/drafts, cluster/GPU choices, stale-response guard, failure/retry, preview binding, mobile and unchanged maoshanwang form.')
    finally:server.shutdown();server.server_close()

asyncio.run(main())
