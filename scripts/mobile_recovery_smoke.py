"""Mobile auth loss must not erase unknown cloud operations. All writes are mocked."""
import asyncio,functools,threading
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
from playwright.async_api import async_playwright,expect
from browser_support import browser_launch_options
from v2_browser_smoke import ROOT,Handler

async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    now=datetime.now(timezone.utc).isoformat();denied=True;calls=[]
    async def api(route):
        request=route.request;path=urlparse(request.url).path
        if path=='/api/state':data={'status':'ok','connected':True,'updated_at':now,'tasks':[{'id':'ACP:test','name':'charles test','kind':'ACP','state':'running','can_stop':True,'updated_at':now,'metrics':{}}],'waiting_tasks':[],'clusters':[],'source_status':{'ACP':{'status':'ok','updated_at':now}}}
        elif path=='/api/history':data={'series':[]}
        elif path=='/api/acp/options':data={'template':{'id':'ACP:template','name':'charles template'},'gpu_options':[1]}
        elif path in ('/api/acp/create','/api/acp/stop'):
            calls.append((path,request.post_data_json))
            if denied:
                await route.fulfill(status=403,json={'code':'mobile_access_denied','error':'请在电脑恢复手机管理权限。'});return
            data={'id':'ACP:new','name':'charles test','state':'RUNNING'}
        elif path=='/api/refresh':data={}
        else:raise AssertionError(path)
        await route.fulfill(json=data)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(**browser_launch_options());context=await browser.new_context();page=await context.new_page()
            await page.route('**/api/**',api)
            for site in ('maoshanwang','sensecore'):
                suffix='.sensecore' if site=='sensecore' else ''
                for kind,payload in [('create',{'preview_id':'previous-preview'}),('stop',{'task_id':'ACP:test','display_name':'charles test','request_id':'previous-request'})]:
                    await page.goto(f'http://127.0.0.1:{server.server_port}/?site={site}#acp',wait_until='networkidle')
                    key='charles-monitor.acp.'+kind+'.pending'+suffix
                    await page.evaluate('([key,value])=>localStorage.setItem(key,JSON.stringify(value))',[key,payload])
                    await page.reload(wait_until='networkidle');denied=True
                    await page.locator('#acp-'+kind+'-resume').click();await page.locator('#acp-'+kind+'-confirm').click()
                    await expect(page.locator('#acp-'+kind+'-message')).to_contain_text('恢复手机管理权限')
                    assert await page.evaluate('key=>JSON.parse(localStorage.getItem(key))',key)==payload
                    await page.reload(wait_until='networkidle');denied=False
                    await page.locator('#acp-'+kind+'-resume').click();await page.locator('#acp-'+kind+'-confirm').click()
                    await expect(page.locator('#acp-'+kind+'-dialog')).not_to_be_visible()
                    assert await page.evaluate('key=>localStorage.getItem(key)',key) is None
                    assert calls[-2:]==[('/api/acp/'+kind,payload)]*2
            await page.close();await context.close();await browser.close()
            print('Mobile auth recovery passed for both sites and create/stop: unknown IDs survive403/reload and are reused; all cloud writes mocked.')
    finally:server.shutdown();server.server_close()

asyncio.run(main())
