"""Embedded xterm UI checks; all cloud targets, shells and output are mocked."""
import asyncio,base64,functools,json,threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse,urlencode
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
 base=f'http://127.0.0.1:{server.server_port}'
 errors=[];inputs=[];opens=[];jobs={};mode='single';transport='ws';read_calls=[];kind='CCI';site='maoshanwang'
 def targets():
  console='https://console.sensecore.cn/cn-sh-01' if site=='sensecore' else 'https://console.ms-sc-01.maoshanwangtech.com'
  result=[]
  for instance,container in ([('worker-0',''),('worker-1','')] if kind=='ACP' else [('pod-1','main'),('pod-1','sidecar'),('pod-2','main')]):
   query={'workspace':'/workspace','job_id':'job','pod_name':instance} if kind=='ACP' else {'workspace':'/workspace','app_name':'job','container_instance_name':instance,'container_name':container}
   result.append({'instance':instance,**({'container':container} if kind=='CCI' else {}),'url':console+'/'+kind.lower()+'/terminal?'+urlencode(query)})
  result=result if mode=='multi' else result[:1]
  if mode=='empty':result=[]
  if mode=='untrusted':result[0]['url']='https://untrusted.example/terminal'
  return {'site':site,'task_id':kind+':job','name':'charles <img src=x onerror=alert(1)>','targets':result}
 def output(cursor=1):
  return {'type':'output','cursor':cursor,'sequence':0,'chunks':[base64.b64encode('\x1b[32mILAB terminal ready · 中文\x1b[0m\r\n$ '.encode()).decode()] if cursor==1 else [],'closed':False,'error':None}
 async def api(route):
  path=urlparse(route.request.url).path;data=route.request.post_data_json or {};assert route.request.headers['x-monitor-site']==site
  assert route.request.method=='POST'
  if path=='/api/terminal/targets':
   jobs['targets']=targets();result={'request_id':'targets'}
  elif path=='/api/terminal/open':
   opens.append(data);jobs['open']={'session_id':'mock-session','site':site,'task_id':kind+':job'};result={'request_id':'open'}
  elif path=='/api/terminal/status':result={'pending':False,'result':jobs[data['request_id']]}
  elif path=='/api/terminal/read':
   read_calls.append(data)
   if data['cursor']:
    await asyncio.sleep(.2);result={**output(2),'cursor':data['cursor']}
   else:result=output()
  elif path=='/api/terminal/write':inputs.append(data);result={'type':'ack','sequence':data['sequence']}
  elif path=='/api/terminal/close':result={'closed':True}
  else:raise AssertionError(path)
  try:await route.fulfill(json=result)
  except Exception:pass  # A page closing cancels an outstanding long poll.
 async def ws_handler(ws):
  if transport=='http':await ws.close();return
  async def msg(raw):
   data=json.loads(raw)
   if 'session_id' in data:
    if transport=='delayed':await asyncio.sleep(2)
    try:ws.send(json.dumps({'type':'ready','sequence':0}))
    except Exception:pass
   elif data=={'type':'attach'}:ws.send(json.dumps(output()))
   elif 'sequence' in data:
    inputs.append(data);ws.send(json.dumps({'type':'ack','sequence':data['sequence']}))
   elif data=={'type':'ping'}:ws.send(json.dumps({'type':'pong'}))
  ws.on_message(lambda raw: asyncio.create_task(msg(raw)))
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(**browser_launch_options());ctx=await browser.new_context(viewport={'width':1440,'height':1000})
   await ctx.route('**/api/**',api);await ctx.route_web_socket('**/api/terminal/socket',ws_handler)
   for site in ('maoshanwang','sensecore'):
    for kind in ('CCI','ACP'):
     for mode in ('single','multi','empty','untrusted'):
      page=await ctx.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
      before=len(opens)
      await page.goto(base+f'/terminal?site={site}&task_id={kind}:job')
      if mode in ('empty','untrusted'):
       await expect(page.locator('#terminal-status')).to_contain_text('当前没有' if mode=='empty' else '链接与所选任务不一致')
       assert len(opens)==before
      else:
       if mode=='multi':
        await expect(page.locator('#terminal-picker')).to_be_visible()
        await expect(page.locator('#terminal-instance option')).to_have_count(2)
        await page.locator('#terminal-instance').select_option('worker-1' if kind=='ACP' else 'pod-2')
        await page.locator('#terminal-open').click()
       await expect(page.locator('#terminal-led')).to_have_class('terminal-led connected')
       await expect(page.locator('.xterm-rows')).to_contain_text('ILAB terminal ready')
       await expect(page.locator('.xterm-rows')).to_contain_text('中文')
       await expect(page.locator('#terminal-task img')).to_have_count(0)
       assert page.url.startswith(base)
       await page.locator('.xterm-helper-textarea').fill('')
       await page.locator('.xterm-helper-textarea').press_sequentially('echo test')
       await page.get_by_role('button',name='Ctrl+C',exact=True).click()
       for _ in range(40):
        if any('\x03' in x.get('data','') for x in inputs):break
        await asyncio.sleep(.05)
       assert any('\x03' in x.get('data','') for x in inputs)
       await page.locator('#terminal-font-up').click()
       await page.locator('#terminal-disconnect').click()
       await expect(page.locator('#terminal-transport')).to_have_text('已断开')
       await expect(page.locator('#terminal-status')).to_have_attribute('data-state', 'idle')
       await expect(page.locator('#terminal-status')).to_contain_text('已断开')
      await page.close()
   mode='single';kind='ACP';site='maoshanwang'
   for transport in ('http','delayed'):
    page=await ctx.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
    await page.set_viewport_size({'width':390,'height':844})
    await page.goto(base+f'/terminal?site={site}&task_id=ACP:job')
    await expect(page.locator('#terminal-transport')).to_have_text('兼容连接')
    await expect(page.locator('.xterm-rows')).to_contain_text('ILAB terminal ready')
    await page.wait_for_timeout(800)
    await expect(page.locator('#terminal-transport')).to_have_text('兼容连接')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.locator('#terminal-workspace').scroll_into_view_if_needed()
    await page.screenshot(path=str(ROOT/'data/terminal-embedded-mobile.png'))
    await page.get_by_role('button',name='Ctrl+C',exact=True).click()
    await page.wait_for_timeout(100)
    assert read_calls and any(x.get('session_id')=='mock-session' and '\x03' in x.get('data','') for x in inputs)
    await page.close()
   assert not errors,errors
   await ctx.close();await browser.close()
   print('Embedded terminal UI passed: both sites/kinds, xterm ANSI/Chinese, target selection, input/resize, HTTP fallback, delayed WS ready, 390px layout, no cloud requests.')
 finally:server.shutdown();server.server_close()

asyncio.run(main())
