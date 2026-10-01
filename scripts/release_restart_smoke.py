"""Release-restart confirmation/recovery with mock APIs; no real cloud writes."""
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
 now=datetime.now(timezone.utc).isoformat();calls=[];partial=False;fresh=True;slow=False
 task={'id':'ACP:original','kind':'ACP','name':'charles original','state':'running','can_stop':True,'cluster':'original-cluster','gpu_count':8,'replicas':2,'updated_at':now,'metrics':{}}
 template={'id':task['id'],'name':task['name'],'state':'RUNNING'}
 config={'images':['original/image'],'afs_mounts':[{'id':'original-afs','zone':'cn-sh-01a','mount_path':'/data','subdir':'/'}]}
 async def api(route):
  nonlocal partial
  req=route.request;path=urlparse(req.url).path;site=req.headers.get('x-monitor-site');body=req.post_data_json if req.method=='POST' else None
  calls.append((site,path,body))
  if body is not None:assert req.headers.get('x-monitor-request')=='1'
  if path=='/api/state':data={'status':'ok','connected':True,'updated_at':now,'tasks':[task,{**task,'id':'CCI:readonly','kind':'CCI','name':'charles container'}],'waiting_tasks':[],'clusters':[],'source_status':{'ACP':{'status':'ok' if fresh else 'error','updated_at':now},'CCI':{'status':'ok','updated_at':now}}}
  elif path=='/api/history':data={'series':[]}
  elif path=='/api/acp/options':data={'template':{'id':'ACP:another','name':'charles unrelated'},'gpu_options':[1]}
  elif path=='/api/acp/release-restart-options':
   if slow:await asyncio.sleep(.8)
   data={'release_restart':True,'template':template,'template_config':config,'command':'python original.py','revision':'original-revision','cluster':'original-cluster','replicas':2,'resource_spec':'original-spec'}
  elif path=='/api/acp/release-restart-preview':
   assert body=={'task_id':'ACP:original','revision':'original-revision','command':'python changed.py'}
   data={'release_restart':True,'restart':True,'preview_id':'release-stable','expires_at':'2099-01-01T00:00:00Z','template':template,'template_config':config,
         'selection_mode':'manual','cluster':{'id':'original-cluster','name':'original-cluster'},'replicas':2,'gpus_per_replica':4,'total_gpus':8,
         'vcpu_per_replica':56,'memory_gib_per_replica':960,'command':'python changed.py\nsleep inf','will_queue':True,'partition':{'name':'A 区'}}
  elif path=='/api/acp/create':
   assert body=={'preview_id':'release-stable'}
   key='charles-monitor.acp.create.pending'+('.sensecore' if site=='sensecore' else '')
   assert await req.frame.evaluate('key=>JSON.parse(localStorage.getItem(key))',key)==body
   if partial:
    partial=False;await route.fulfill(status=409,json={'code':'release_incomplete','error':'新任务 ACP:new 已提交；原任务 ACP:original 的释放尚未确认。核对此次请求不会重复创建。'});return
   data={'release_restart':True,'id':'ACP:new','name':'charles original','state':'WAIT_QUOTA','original_id':'ACP:original','original_state':'SUSPENDING'}
  elif path=='/api/refresh':data={}
  else:raise AssertionError(path)
  await route.fulfill(json=data)
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(**browser_launch_options());context=await browser.new_context(viewport={'width':1440,'height':1000});page=await context.new_page()
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)));await page.route('**/api/**',api)
   base=f'http://127.0.0.1:{server.server_port}/'
   for site in ('maoshanwang','sensecore'):
    await page.goto(base+f'?site={site}#tasks',wait_until='networkidle')
    release=page.locator('#acp-release-restart-selected');await expect(release).to_be_visible()
    await page.locator('.task-card').filter(has_text='charles container').click();await expect(release).not_to_be_visible()
    await page.locator('.task-card').filter(has_text='charles original').click();await expect(release).to_be_enabled()
    fresh=False;await page.locator('#refresh-button').click();await expect(release).to_be_disabled()
    fresh=True;await page.locator('#refresh-button').click();await expect(release).to_be_enabled()
    await release.click();await expect(page.locator('#acp-restart-title')).to_have_text('释放重启 ACP 任务')
    await expect(page.locator('#acp-restart-command')).to_have_value('python original.py')
    await expect(page.locator('#acp-restart-description')).to_contain_text('先提交新任务')
    await page.locator('#acp-restart-command').fill('python changed.py');await page.locator('#acp-restart-preview').click()
    await expect(page.locator('#acp-create-title')).to_have_text('确认释放重启 ACP 任务')
    await expect(page.locator('#acp-preview-details')).to_contain_text('随后释放的原任务')
    await expect(page.locator('#acp-preview-details')).to_contain_text('ACP:original')
    assert not any(path=='/api/acp/create' for sid,path,_ in calls if sid==site)
    await page.locator('#acp-create-cancel').click();await expect(page.locator('#acp-restart-command')).to_have_value('python changed.py')
    await page.locator('#acp-restart-preview').click();partial=True;await page.locator('#acp-create-confirm').click()
    await expect(page.locator('#acp-create-message')).to_contain_text('原任务 ACP:original 的释放尚未确认')
    assert 'python changed.py' not in await page.evaluate('JSON.stringify(localStorage)')
    await page.reload(wait_until='networkidle');await expect(release).to_be_disabled()
    await page.locator('#acp-create-resume').click();await page.locator('#acp-create-confirm').click()
    await expect(page.locator('#acp-create-dialog')).not_to_be_visible();await expect(page.locator('#acp-message')).to_contain_text('释放重启已提交')
    await expect(page.locator('#acp-message')).to_contain_text('ACP:original（SUSPENDING）')
    await page.locator('#refresh-button').click();await expect(release).to_be_enabled()
    slow=True;await release.click();await page.locator('#acp-restart-cancel').click();await asyncio.sleep(1)
    await expect(page.locator('#acp-restart-command')).to_have_value('');slow=False
   await page.set_viewport_size({'width':390,'height':844});await expect(release).to_be_visible()
   initial=await page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll("body *")].filter(el=>el.getBoundingClientRect().right>innerWidth).map(el=>({tag:el.tagName,id:el.id,cls:el.className,width:el.getBoundingClientRect().width})).slice(0,8)})')
   await page.wait_for_function('document.documentElement.scrollWidth<=innerWidth',timeout=3000)
   if initial['scroll']>initial['width']:print('Resize settled after chart resize event:',initial)
   await release.click();await expect(page.locator('#acp-restart-command')).to_be_enabled()
   await page.screenshot(path=str(ROOT/'data/release-restart-mock.png'),full_page=True)
   assert not errors,errors
   assert not any(path in ('/api/acp/stop','/api/acp/preview','/api/acp/restart-preview') for _,path,_ in calls)
   await page.close();await context.close();await browser.close()
   print('Release restart UI passed: both sites, running ACP only, stale-data lock, original command/edit, explicit create-before-stop confirmation, partial outcome/reload, same request, cancelled read, mobile.')
 finally:server.shutdown();server.server_close()

asyncio.run(main())
