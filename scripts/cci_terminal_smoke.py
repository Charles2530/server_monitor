"""CCI/ACP terminal navigation uses mock targets; never reaches a real shell."""
import asyncio,functools,re,threading
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse,urlencode,parse_qs
from playwright.async_api import async_playwright,expect
from browser_support import browser_launch_options
from v2_browser_smoke import ROOT,Handler

class TerminalHandler(Handler):
 def do_GET(self):
  if self.path.split('?',1)[0]=='/terminal':self.path='/static/terminal.html'
  super().do_GET()

async def main():
 server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(TerminalHandler,directory=str(ROOT)))
 threading.Thread(target=server.serve_forever,daemon=True).start()
 now=datetime.now(timezone.utc).isoformat();mode='single';calls=[];navigations=[];errors=[]
 def target(console,instance,container):
  return {'instance':instance,'container':container,'url':console+'/cci/terminal?'+urlencode({'container_name':container,'container_instance_name':instance,'app_name':'app-test','workspace':'/workspace'})}
 async def api(route):
  req=route.request;assert req.method=='GET';site=req.headers.get('x-monitor-site');path=urlparse(req.url).path;calls.append((site,path))
  console='https://console.sensecore.cn/cn-sh-01' if site=='sensecore' else 'https://console.ms-sc-01.maoshanwangtech.com'
  if path=='/api/state':
   task={'id':'CCI:app-test','kind':'CCI','name':'charles cci','state':'running','can_stop':False,'cluster':'cluster','gpu_count':1,'replicas':1,'metrics':{},'updated_at':now}
   data={'tasks':[task,{**task,'id':'ACP:acp-test','kind':'ACP','name':'charles acp','can_stop':True}],'waiting_tasks':[],'clusters':[],
         'status':'ok','connected':True,'updated_at':now,'source_status':{kind:{'status':'ok','updated_at':now} for kind in ('CCI','ACP')}}
  elif path=='/api/history':data={'series':[]}
  elif path=='/api/acp/options':data={'template':{'id':'ACP:template','name':'charles'},'gpu_options':[1]}
  elif path=='/api/acp/terminal-targets':
   assert parse_qs(urlparse(req.url).query)['task_id']==['ACP:acp-test']
   rows=[{'instance':w,'url':console+'/acp/terminal?'+urlencode({'workspace':'/workspace','job_id':'acp-test','pod_name':w})} for w in (['worker-0','worker-1'] if mode=='multi' else ['worker-0'])]
   if mode=='empty':rows=[]
   if mode=='untrusted':rows[0]['url']='https://untrusted.example/terminal'
   if mode=='wrong-task':rows[0]['url']=rows[0]['url'].replace('job_id=acp-test','job_id=foreign')
   data={'site':site,'task_id':'ACP:acp-test','name':'charles acp','targets':rows}
  elif path=='/api/cci/terminal-targets':
   assert parse_qs(urlparse(req.url).query)['task_id']==['CCI:app-test']
   rows=[target(console,'pod-1','main')]
   if mode=='multi':rows += [target(console,'pod-1','sidecar'),target(console,'pod-2','main')]
   if mode=='empty':rows=[]
   if mode=='untrusted':rows[0]['url']='https://untrusted.example/terminal'
   data={'site':site,'task_id':'CCI:app-test','name':'charles cci <img src=x onerror=alert(1)>','targets':rows}
  else:raise AssertionError(path)
  await route.fulfill(json=data)
 async def official(route):
  assert route.request.method=='GET';navigations.append(route.request.url)
  await route.fulfill(content_type='text/html',body='<title>Mock official terminal</title><p>Official terminal destination</p>')
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(**browser_launch_options());ctx=await browser.new_context(viewport={'width':1440,'height':1000})
   await ctx.route('**/api/**',api);await ctx.route(re.compile(r'https://console\.(ms-sc-01\.maoshanwangtech\.com|sensecore\.cn)/.*'),official)
   ctx.on('page',lambda page:page.on('pageerror',lambda error:errors.append(str(error))))
   page=await ctx.new_page();base=f'http://127.0.0.1:{server.server_port}'
   for site in ('maoshanwang','sensecore'):
    mode='single';await page.goto(base+'/?site='+site+'#tasks',wait_until='networkidle')
    await page.locator('.task-card').filter(has_text='charles acp').click()
    link=page.locator('#cci-terminal-selected');await expect(link).to_be_visible()
    await expect(page.locator('#acp-release-restart-selected')).to_be_visible()
    await expect(page.locator('#acp-stop-selected')).to_be_visible()
    for mode in ('single','multi','empty','untrusted','wrong-task'):
     before=len(navigations)
     async with ctx.expect_page() as opened:await link.click()
     popup=await opened.value
     if mode=='multi':
      await expect(popup.locator('#terminal-picker')).to_be_visible()
      await expect(popup.locator('#terminal-instance option')).to_have_count(2)
      await expect(popup.locator('#terminal-container')).not_to_be_visible()
      await expect(popup.locator('#terminal-instance-label')).to_have_text('Worker')
      await popup.locator('#terminal-instance').select_option('worker-1')
      await popup.locator('#terminal-open').click()
     if mode in ('single','multi'):
      await popup.wait_for_url(re.compile(r'https://console\.'),timeout=10000)
      assert urlparse(popup.url).path==('/cn-sh-01' if site=='sensecore' else '')+'/acp/terminal'
      assert parse_qs(urlparse(popup.url).query)['pod_name']==['worker-1' if mode=='multi' else 'worker-0']
     else:
      await expect(popup.locator('#terminal-retry')).to_be_visible()
      await expect(popup.locator('#terminal-status')).to_contain_text('当前没有' if mode=='empty' else '链接与所选任务不一致')
      assert len(navigations)==before
     await popup.close()
    mode='single'
    await page.locator('.task-card').filter(has_text='charles cci').click()
    link=page.locator('#cci-terminal-selected');await expect(link).to_be_visible();await expect(link).to_have_attribute('rel','noopener noreferrer')
    async with ctx.expect_page() as opened:await link.click()
    popup=await opened.value
    await popup.wait_for_url(re.compile(r'https://console\.'),timeout=10000)
    assert urlparse(popup.url).path==('/cn-sh-01' if site=='sensecore' else '')+'/cci/terminal'
    assert parse_qs(urlparse(popup.url).query)['container_instance_name']==['pod-1']
    await popup.close()
    mode='multi'
    async with ctx.expect_page() as opened:await link.click()
    popup=await opened.value;await expect(popup.locator('#terminal-picker')).to_be_visible()
    await expect(popup.locator('#terminal-container option')).to_have_count(2)
    await expect(popup.locator('#terminal-task img')).to_have_count(0)
    await popup.locator('#terminal-container').select_option('sidecar')
    assert parse_qs(urlparse(await popup.locator('#terminal-open').get_attribute('href')).query)['container_name']==['sidecar']
    await popup.locator('#terminal-instance').select_option('pod-2');await expect(popup.locator('#terminal-container option')).to_have_count(1)
    await popup.locator('#terminal-open').click();await popup.wait_for_url(re.compile(r'https://console\.'))
    assert parse_qs(urlparse(popup.url).query)['container_instance_name']==['pod-2'];await popup.close()
    for mode in ('empty','untrusted'):
     before=len(navigations)
     async with ctx.expect_page() as opened:await link.click()
     popup=await opened.value;await expect(popup.locator('#terminal-retry')).to_be_visible()
     await expect(popup.locator('#terminal-status')).to_contain_text('当前没有' if mode=='empty' else '链接与所选任务不一致')
     await expect(popup.locator('#terminal-picker')).not_to_be_visible();assert len(navigations)==before
     mode='single';await popup.locator('#terminal-retry').click();await popup.wait_for_url(re.compile(r'https://console\.'));await popup.close()
   mode='multi'
   async with ctx.expect_page() as opened:await link.click()
   popup=await opened.value;await expect(popup.locator('#terminal-picker')).to_be_visible()
   await popup.set_viewport_size({'width':390,'height':844});await popup.wait_for_function('document.documentElement.scrollWidth<=innerWidth')
   await popup.screenshot(path=str(ROOT/'data/terminal-picker-mock.png'))
   assert not errors,errors
   await popup.close();await page.close();await ctx.close();await browser.close()
   print('CCI/ACP Terminal UI passed: both kinds and sites, one-click new tab, container/instance selection, empty/error retry, trusted URL guards, mobile; no real terminal or cloud mutation.')
 finally:server.shutdown();server.server_close()

asyncio.run(main())
