"""Queue links for both sites, with mock data and no cloud operations."""
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
 now=datetime.now(timezone.utc).isoformat();urls={};errors=[]
 async def api(route):
  request=route.request;assert request.method=='GET'
  path=urlparse(request.url).path
  site=request.headers['x-monitor-site']
  console='https://console.sensecore.cn/cn-sh-01' if site=='sensecore' else 'https://console.ms-sc-01.maoshanwangtech.com'
  if path=='/api/state':
   urls[site]=[console+'/acp/list/detail?id=queued&workspace=workspace&tab=detail',console+'/cci/app/detail?workspace=workspace&name=queued']
   rows=[{'id':kind+':queued','name':'another user '+kind,'kind':kind,'state':'STARTING','owner':'another user','url':url} for kind,url in zip(('ACP','CCI'),urls[site])]
   rows.extend({'id':'ACP:invalid-'+str(i),'kind':'ACP','state':'STARTING','name':'invalid','url':url} for i,url in enumerate(('javascript:alert(1)','https://untrusted.example/task',console.replace('https://','https://user:password@'),None)))
   data={'status':'ok','connected':True,'updated_at':now,'tasks':[],'waiting_tasks':rows,'clusters':[],
         'source_status':{kind:{'status':'ok','updated_at':now} for kind in ('ACP','CCI','ACP_queue','CCI_queue')}}
  elif path=='/api/utilization-queue':data={'site':site,'tasks':[],'loading':False,'status':'ok','updated_at':now,'warnings':[]}
  elif path=='/api/acp/options':data={'template':{'id':'ACP:test','name':'charles'},'gpu_options':[1]}
  else:raise AssertionError(path)
  await route.fulfill(json=data)
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(**browser_launch_options());context=await browser.new_context();page=await context.new_page()
   page.on('pageerror',lambda e:errors.append(str(e)));await page.route('**/api/**',api)
   for site in ('maoshanwang','sensecore'):
    await page.goto(f'http://127.0.0.1:{server.server_port}/?site={site}#waiting',wait_until='networkidle')
    links=page.locator('#waiting-body a');await expect(links).to_have_count(2)
    for index,url in enumerate(urls[site]):
     await expect(links.nth(index)).to_have_attribute('href',url)
     await expect(links.nth(index)).to_have_attribute('target','_blank')
     await expect(links.nth(index)).to_have_attribute('rel','noopener noreferrer')
    await expect(page.locator('#waiting-body button')).to_have_count(0)
    await expect(page.locator('#waiting-body tr').first).to_contain_text('只读')
   assert not errors,errors
   await page.close();await context.close();await browser.close()
   print('Queue links passed: CCI/ACP, both sites, all users, new tab, untrusted and missing URLs omitted; no mutation.')
 finally:server.shutdown();server.server_close()

asyncio.run(main())
