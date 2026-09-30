"""Restart UI checks with mock APIs only; never submits a real cloud task."""
import asyncio,functools,threading
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
from browser_support import browser_launch_options
from playwright.async_api import async_playwright,expect
from v2_browser_smoke import Handler,ROOT

async def main():
 server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT)))
 threading.Thread(target=server.serve_forever,daemon=True).start()
 now=datetime.now(timezone.utc).isoformat();calls=[];fail_create=False;slow=False
 async def api(route):
  nonlocal fail_create
  req=route.request;path=urlparse(req.url).path;site=req.headers.get('x-monitor-site');body=req.post_data_json if req.method=='POST' else None
  calls.append((site,path,body))
  template={'id':'ACP:old','name':'charles original failed','state':'FAILED'}
  config={'images':['original/image'],'afs_mounts':[{'id':'original-afs','zone':'cn-sh-01b','mount_path':'/original','subdir':'/'}]}
  if body is not None:assert req.headers['x-monitor-request']=='1'
  if path=='/api/state':data={'status':'ok','connected':True,'updated_at':now,'tasks':[],'waiting_tasks':[],'clusters':[],'source_status':{}}
  elif path=='/api/acp/options':data={'template':{'id':'ACP:newest','name':'charles unrelated'},'gpu_options':[1]}
  elif path=='/api/task-history':
   rows=[{**template,'kind':'ACP','status':'failed','status_label':'失败','created_at':now,'url':'https://console.ms-sc-01.maoshanwangtech.com/acp/list/detail?id=old'},
         {**template,'id':'CCI:old','kind':'CCI','status':'stopped','status_label':'已停止'}]
   data={'site':site,'tasks':rows,'loading':False,'source_status':{},'filtered_stopped':{},'warnings':[]}
  elif path=='/api/acp/restart-options':
   if slow:await asyncio.sleep(.8)
   data={'template':template,'template_config':config,'revision':'r1','command':'python original.py','cluster':'original-cluster','replicas':2,'resource_spec':'original-spec'}
  elif path=='/api/acp/restart-preview':
   assert body['task_id']=='ACP:old' and body['revision']=='r1'
   data={'restart':True,'preview_id':'restart-stable','expires_at':'2099-01-01T00:00:00Z','template':template,'template_config':config,
         'selection_mode':'manual','cluster':{'id':'original-cluster','name':'original cluster'},'replicas':2,'gpus_per_replica':8,'total_gpus':16,
         'vcpu_per_replica':112,'memory_gib_per_replica':1920,'command':(body['command']+'\nsleep inf').strip(),'will_queue':True,'partition':{'name':'B 区'}}
  elif path=='/api/acp/create':
   assert body=={'preview_id':'restart-stable'}
   key='charles-monitor.acp.create.pending'+('.sensecore' if site=='sensecore' else '')
   assert await req.frame.evaluate('(key)=>JSON.parse(localStorage.getItem(key))',key)==body
   if fail_create:
    fail_create=False;await route.abort('failed');return
   data={'id':'ACP:new','name':'charles original failed','state':'STARTING'}
  elif path=='/api/refresh':data={}
  else:raise AssertionError(path)
  await route.fulfill(json=data)
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(**browser_launch_options());context=await browser.new_context(viewport={'width':1440,'height':1000});page=await context.new_page()
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)));await page.route('**/api/**',api)
   base=f'http://127.0.0.1:{server.server_port}/'
   for site in ('maoshanwang','sensecore'):
    await page.goto(base+f'?site={site}#history',wait_until='networkidle')
    restart=page.locator('#task-history-body button');await expect(restart).to_have_count(1)
    await restart.click();await expect(page.locator('#acp-restart-command')).to_have_value('python original.py')
    await expect(page.locator('#acp-restart-details')).to_contain_text('original-afs')
    await page.locator('#acp-restart-command').fill('python changed.py')
    await page.locator('#acp-restart-restore').click();await expect(page.locator('#acp-restart-command')).to_have_value('python original.py')
    await page.locator('#acp-restart-command').fill('python retry.py')
    await page.locator('#acp-restart-preview').click()
    await expect(page.locator('#acp-create-title')).to_have_text('确认重新启动 ACP 任务')
    await expect(page.locator('#acp-preview-details')).to_contain_text('python retry.py\nsleep inf')
    await expect(page.locator('#acp-preview-details')).to_contain_text('保留原任务记录')
    assert not any(path=='/api/acp/create' for sid,path,_ in calls if sid==site)
    await page.locator('#acp-create-cancel').click();await expect(page.locator('#acp-restart-command')).to_have_value('python retry.py')
    await page.locator('#acp-restart-preview').click();fail_create=True
    await page.locator('#acp-create-confirm').click();await expect(page.locator('#acp-create-message')).to_contain_text('创建结果未知')
    saved=await page.evaluate('JSON.stringify(localStorage)');assert 'python retry.py' not in saved
    await page.reload(wait_until='networkidle')
    before=len([x for x in calls if x[1]=='/api/acp/restart-options'])
    await restart.click();await expect(page.locator('#acp-create-dialog')).to_be_visible()
    assert len([x for x in calls if x[1]=='/api/acp/restart-options'])==before
    await page.locator('#acp-create-confirm').click();await expect(page.locator('#acp-create-dialog')).not_to_be_visible()
    await expect(page.locator('#acp-message')).to_contain_text('ACP:new')
    slow=True;await restart.click();await page.locator('#acp-restart-cancel').click();await asyncio.sleep(1)
    await expect(page.locator('#acp-restart-dialog')).not_to_be_visible();await expect(page.locator('#acp-restart-command')).to_have_value('');slow=False
   await page.set_viewport_size({'width':390,'height':844});await restart.click()
   await expect(page.locator('#acp-restart-command')).to_be_enabled()
   assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   await page.screenshot(path=str(ROOT/'data/restart-mock.png'),full_page=True)
   assert not errors,errors
   assert not any(path in ('/api/acp/stop','/api/acp/preview') for _,path,_ in calls)
   await page.close();await context.close();await browser.close()
   print('Restart UI passed: both sites, ACP-only, original command, edit/restore, preview/cancel, unknown outcome/reload, stable token, late response, mobile.')
 finally:server.shutdown();server.server_close()

asyncio.run(main())
