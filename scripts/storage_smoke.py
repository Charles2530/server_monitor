"""AFS page regression with simulated APIs; no remote requests or writes."""
import asyncio
import functools
import threading
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from browser_support import browser_launch_options
from playwright.async_api import async_playwright,expect
from v2_browser_smoke import Handler

ROOT=Path(__file__).resolve().parent.parent

async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    now=datetime.now(timezone.utc).isoformat();calls=[];failed=False;empty=False;loading=True
    async def api(route):
        req=route.request;path=urlparse(req.url).path;site=req.headers.get('x-monitor-site')
        assert req.method=='GET', 'storage navigation cannot mutate cloud resources'
        if path=='/api/tokenhub/state':
            await route.fulfill(json={'status':'disconnected','connected':False,'sections':{}});return
        assert site in ('sensecore','maoshanwang')
        calls.append((path,site))
        if path=='/api/state':data={'status':'ok','connected':True,'updated_at':now,'tasks':[],'waiting_tasks':[],'clusters':[],'source_status':{}}
        elif path=='/api/acp/options':data={'template':{'id':'ACP:test','name':'charles','created_at':now},'gpu_options':[1],'clusters':[], 'template_config':{'images':['registry/test:v1'],'afs_mounts':[]}}
        elif path=='/api/storage':
            if failed:
                await route.fulfill(status=503,json={'error':'云接口暂不可用'});return
            rows=[{'id':'volume-a','name':'Shared <img src=x onerror=alert(1)>','zone':'cn-sh-01a','state':'ACTIVE','total_bytes':2e12,'used_bytes':.5e12,'free_bytes':1.5e12,'used_percent':25,'mounts':[{'kind':'ACP','task_name':'charles a','task_state':'STOPPED','created_at':now,'subdir':'/charles','mount_path':'/mnt/a'}]},
                  {'id':'volume-b','name':'Unmeasured','zone':'cn-sh-01b','state':'ACTIVE','total_bytes':1e12,'used_bytes':None,'free_bytes':None,'used_percent':None,'mounts':[]}]
            if loading:
                for row in rows:row['mounts']=[]
            data={'mount_refreshing':loading,'mount_updated_at':None if loading else now,'site':site,'volumes':[] if empty else rows,'status':'partial','warnings':['1 个存储卷的容量信息不完整。'],'mount_status':{'CCI':'ok','ACP':'ok'},'updated_at':now}
        else:raise AssertionError(path)
        await route.fulfill(json=data)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(**browser_launch_options())
            page=await browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.route('**/api/**',api)
            base=f'http://127.0.0.1:{server.server_port}/'
            await page.goto(base+'?site=sensecore#overview',wait_until='networkidle')
            assert not any(path=='/api/storage' for path,_ in calls), 'storage is loaded on demand'
            await page.locator('.nav-item[href="#storage"]').click()
            await expect(page.locator('#storage-count')).to_have_text('2 / 2')
            await expect(page.locator('.workspace-view:visible')).to_have_count(1)
            await expect(page.locator('#storage-status')).to_contain_text('挂载记录后台读取中')
            await expect(page.locator('#storage-summary')).to_contain_text('500 GB')
            await page.locator('#storage-related').check()
            await expect(page.locator('#storage-body')).to_contain_text('挂载记录正在后台读取，稍后补齐')
            loading=False
            await expect(page.locator('#storage-body')).to_contain_text('/mnt/a',timeout=7000)
            await page.locator('#storage-related').uncheck()
            await expect(page.locator('#storage-body img')).to_have_count(0)
            await expect(page.locator('#storage-body')).to_contain_text('STOPPED')
            await expect(page.locator('#storage-body')).to_contain_text('使用率未知')
            await expect(page.locator('#storage-summary')).to_contain_text('1 个未知（部分汇总）')
            await page.locator('#storage-zone').select_option('cn-sh-01b')
            await expect(page.locator('#storage-count')).to_have_text('1 / 2')
            await expect(page.locator('#storage-body')).not_to_contain_text('/mnt/a')
            await page.locator('#storage-zone').select_option('')
            await page.locator('#storage-search').fill('/mnt/a')
            await expect(page.locator('#storage-count')).to_have_text('1 / 2')
            await page.locator('#storage-search').fill('no match')
            await expect(page.locator('#storage-body')).to_contain_text('没有匹配')
            await page.locator('#storage-search').fill('')
            await page.locator('#storage-related').check()
            await expect(page.locator('#storage-count')).to_have_text('1 / 2')
            await page.locator('#storage-related').uncheck()
            failed=True
            await page.locator('#storage-refresh').click()
            await expect(page.locator('#storage-warning')).to_contain_text('保留上次成功数据')
            await expect(page.locator('#storage-body')).to_contain_text('volume-a')
            failed=False
            await page.locator('#storage-refresh').click()
            await expect(page.locator('#storage-warning')).not_to_contain_text('保留上次成功数据')
            for width in (390,360):
                await page.set_viewport_size({'width':width,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await expect(page.locator('.nav-item[href="#storage"]')).to_be_visible()
            await page.set_viewport_size({'width':1440,'height':1000})
            await page.screenshot(path=str(ROOT/'data/storage-mock.png'),full_page=True)
            failed=True
            await page.goto(base+'?site=maoshanwang#storage',wait_until='networkidle')
            await expect(page.locator('#storage-body')).to_contain_text('存储数据未获取')
            await expect(page.locator('#storage-body')).not_to_contain_text('未返回可访问')
            failed=False;empty=True
            await page.locator('#storage-refresh').click()
            await expect(page.locator('#storage-body')).to_contain_text('未返回可访问')
            await page.goto(base+'?site=maoshanwang#acp',wait_until='networkidle')
            await expect(page.locator('#acp-template-config')).to_contain_text('registry/test:v1')
            await expect(page.locator('#acp-template-config')).to_contain_text('未配置 AFS 挂载')
            await expect(page.locator('#acp-partition')).to_have_count(0)
            assert not errors,errors
            assert {site for path,site in calls if path=='/api/storage'}=={'sensecore','maoshanwang'}
            await browser.close()
            print('Storage passed: lazy load, site binding, filters, unknown capacity, historical mounts, safe text, error retention/retry, empty state, mobile, maoshanwang template config.')
    finally:server.shutdown();server.server_close()

asyncio.run(main())
