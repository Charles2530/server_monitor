"""Local static UI with mocked APIs: no cloud traffic or mutations."""
import asyncio
import functools
import threading
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from browser_support import browser_launch_options
from playwright.async_api import async_playwright,expect
from v2_browser_smoke import Handler

ROOT=Path(__file__).resolve().parent.parent

async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    now=datetime.now(timezone.utc).isoformat()
    state={'status':'ok','connected':True,'updated_at':now,'interval_seconds':60,
           'tasks':[{'id':'ACP:test','kind':'ACP','name':'charles test','cluster':'modeltoolchain_test','state':'running','gpu_count':1,'replicas':1,'can_stop':True,'metrics':{},'url':'https://console.ms-sc-01.maoshanwangtech.com/acp/list/detail?id=task-one'}],
           'waiting_tasks':[],'clusters':[{'id':'/subscriptions/public/resourceGroups/public/zones/test/aec2s/public','name':'Public AEC2','available_gpus':50},{'id':'private','name':'Private GPU','available_gpus':2}],'source_status':{'CCI':{'status':'ok'},'ACP':{'status':'ok','updated_at':now}}}
    async def api(route):
        path=route.request.url.split('/api/')[1].split('?')[0]
        assert route.request.method=='GET','Navigation must not perform mutations'
        if path=='state':data=state
        elif path=='tokenhub/state':data={'status':'disconnected','connected':False,'sections':{}}
        elif path=='task-history':data={'site':'maoshanwang','tasks':[],'loading':False,'source_status':{},'filtered_stopped':{},'warnings':[]}
        elif path=='utilization-queue':data={'site':route.request.headers.get('x-monitor-site','maoshanwang'),'tasks':[],'loading':False,'status':'ok','updated_at':now,'warnings':[]}
        elif path=='storage':data={'site':'maoshanwang','volumes':[],'warnings':[],'mount_status':{},'updated_at':now,'status':'ok'}
        elif path=='history':data={'series':[{'metric':'gpu','label':'GPU','points':[[datetime.now(timezone.utc).timestamp(),50]]}]}
        elif path=='acp/options':data={'template':{'id':'ACP:test','name':'charles test'},'gpu_options':[1,2,4,8],'default_gpus_per_replica':1}
        else:raise AssertionError(path)
        await route.fulfill(json=data)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(**browser_launch_options())
            page=await browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
            await page.route('**/api/**',api)
            base=f'http://127.0.0.1:{server.server_port}/'
            await page.goto(base,wait_until='networkidle')
            await expect(page.locator('.workspace-view:visible')).to_have_count(1)
            await expect(page.locator('.summary-grid')).to_be_visible()
            await expect(page.locator('#overview-view #clusters-section')).to_be_visible()
            await expect(page.locator('.nav-item')).to_have_count(7)
            await expect(page.locator('#acp-create-form')).not_to_be_visible()
            await expect(page.locator('#cluster-body')).not_to_contain_text('Public AEC2')
            await expect(page.locator('#cluster-body')).to_contain_text('Private GPU')
            await expect(page.locator('#summary-available')).to_have_text('2')
            for route,title in [('tokenhub','TokenHub'),('history','任务历史'),('storage','存储管理'),('acp','ACP 任务'),('waiting','队列管理'),('tasks','运行任务'),('overview','资源总览')]:
                await page.locator(f'.nav-item[href="#{route}"]').click()
                await expect(page.locator('#page-title')).to_have_text(title)
                await expect(page.locator('.workspace-view:visible')).to_have_count(1)
                await expect(page.locator('.nav-item[aria-current="page"]')).to_have_attribute('href','#'+route)
                assert await page.evaluate('scrollY')==0
            await page.locator('.nav-item[href="#acp"]').click()
            await page.locator('#acp-name').fill('charles preserved')
            await page.locator('#acp-command').fill('python train.py')
            await page.locator('.nav-item[href="#tasks"]').click()
            await expect(page.locator('#history-chart canvas')).to_be_visible()
            await expect(page.locator('.detail-heading #task-console-link')).to_be_visible()
            await expect(page.locator('#task-console-link')).to_have_attribute('href',state['tasks'][0]['url'])
            await expect(page.locator('#task-console-link')).to_have_attribute('target','_blank')
            assert await page.locator('#history-chart canvas').evaluate('e=>e.getBoundingClientRect().width')>250
            await page.locator('#task-search').fill('charles')
            await page.go_back()
            await expect(page.locator('#acp-command')).to_have_value('python train.py')
            await page.go_forward()
            await expect(page.locator('#task-search')).to_have_value('charles')
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#page-title')).to_have_text('运行任务')
            await page.set_viewport_size({'width':1280,'height':720})
            assert await page.locator('#detail-panel').evaluate('e=>e.getBoundingClientRect().bottom<=innerHeight'), 'task details require page scrolling'
            await page.goto(base+'#__proto__',wait_until='networkidle')
            await expect(page.locator('#page-title')).to_have_text('资源总览')
            await page.goto(base+'#clusters-section',wait_until='networkidle')
            await expect(page.locator('#clusters-section')).to_be_visible()
            await expect(page.locator('.nav-item[aria-current="page"]')).to_have_attribute('href','#overview')
            await page.goto(base+'#clusters',wait_until='networkidle')
            await expect(page.locator('#overview-view #clusters-section')).to_be_visible()
            await expect(page.locator('#page-title')).to_have_text('资源总览')
            assert await page.evaluate('location.hash') == '#overview'
            await page.set_viewport_size({'width':390,'height':844})
            for route in ('overview','tasks','acp','waiting','storage','history'):
                link=page.locator(f'.nav-item[href="#{route}"]')
                await expect(link).to_be_visible();await link.click()
                await expect(page.locator('.workspace-view:visible')).to_have_count(1)
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await page.goto(base+'?site=sensecore#overview',wait_until='networkidle')
            await expect(page.locator('#cluster-body')).not_to_contain_text('Public AEC2')
            await expect(page.locator('#summary-available')).to_have_text('2')
            assert not errors,errors
            await browser.close()
            print('Navigation passed: seven isolated pages with TokenHub and cluster overview, active titles, no anchor scrolling, preserved form/search, back/forward, reload, legacy URLs, chart sizing, mobile navigation.')
    finally:server.shutdown();server.server_close()

asyncio.run(main())
