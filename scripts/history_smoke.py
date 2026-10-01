"""History page UI tests with mock APIs; no live task mutations."""
import asyncio,functools,threading
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
 now=datetime.now(timezone.utc).isoformat();calls=[];loading=True;failed=False;source_failed=False;empty=False
 async def api(route):
  req=route.request;path=urlparse(req.url).path;site=req.headers.get('x-monitor-site');calls.append(path)
  assert req.method=='GET'
  if path=='/api/state':data={'status':'ok','connected':True,'updated_at':now,'tasks':[],'waiting_tasks':[],'clusters':[],'source_status':{}}
  elif path=='/api/acp/options':data={'template':{'id':'ACP:test','name':'charles'},'gpu_options':[1]}
  elif path=='/api/task-history':
   if failed:await route.fulfill(status=503,json={'error':'暂时不可用'});return
   origin='https://console.sensecore.cn/cn-sh-01' if site=='sensecore' else 'https://console.ms-sc-01.maoshanwangtech.com'
   rows=[{'id':'ACP:job-'+str(i),'kind':'ACP','name':('charles <img src=x onerror=alert(1)>' if i==0 else 'charles '+str(i)),
          'status':'failed' if i==0 else 'stopped','status_label':'失败' if i==0 else '已停止','cluster':'gpu-cluster',
          'created_at':now,'started_at':now,'ended_at':now,'duration_seconds':120,'url':origin+'/acp/list/detail?id=job-'+str(i)} for i in range(25)]
   data={'site':site,'tasks':[] if loading or empty else rows,'loading':loading,'source_status':{'ACP':{'status':'error' if source_failed else 'loading' if loading else 'ok','updated_at':now},'CCI':{'status':'ok','updated_at':now}},'filtered_stopped':{'ACP':4,'CCI':2},'warnings':[]}
  else:raise AssertionError(path)
  await route.fulfill(json=data)
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(**browser_launch_options());page=await browser.new_page(viewport={'width':1440,'height':1000})
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)));await page.route('**/api/**',api)
   base=f'http://127.0.0.1:{server.server_port}/'
   await page.goto(base+'#overview',wait_until='networkidle');assert '/api/task-history' not in calls
   await page.locator('.nav-item[href="#history"]').click()
   await expect(page.locator('#task-history-body')).to_contain_text('正在读取')
   loading=False
   await expect(page.locator('#task-history-count')).to_have_text('25 / 25',timeout=7000)
   await expect(page.locator('#task-history-body tr')).to_have_count(20)
   await expect(page.locator('#task-history-body img')).to_have_count(0)
   await expect(page.locator('#task-history-excluded')).to_contain_text('ACP 4 条、CCI 2 条')
   await expect(page.locator('#task-history-warning')).not_to_be_visible()
   await expect(page.locator('#history-total')).to_have_text('25')
   await expect(page.locator('#history-failed')).to_have_text('1')
   await expect(page.locator('#history-stopped')).to_have_text('24')
   await expect(page.locator('#history-succeeded')).to_have_text('0')
   await expect(page.locator('#history-chart')).not_to_be_visible()
   await expect(page.locator('#task-history-body a').first).to_have_attribute('target','_blank')
   await page.locator('#task-history-next').click();await expect(page.locator('#task-history-body tr')).to_have_count(5)
   await page.locator('#task-history-state').select_option('failed');await expect(page.locator('#task-history-count')).to_have_text('1 / 25')
   await expect(page.locator('#history-total')).to_have_text('25')
   await expect(page.locator('#task-history-reset')).to_be_visible()
   await page.locator('#task-history-reset').click()
   await expect(page.locator('#task-history-count')).to_have_text('25 / 25')
   await expect(page.locator('#task-history-reset')).not_to_be_visible()
   await page.locator('#task-history-state').select_option('');await page.locator('#task-history-search').fill('job-24')
   await expect(page.locator('#task-history-count')).to_have_text('1 / 25')
   await page.locator('#task-history-search').fill('');await page.locator('#task-history-kind').select_option('CCI')
   await expect(page.locator('#task-history-body')).to_contain_text('没有匹配')
   await page.locator('#task-history-kind').select_option('');await page.locator('#task-history-size').select_option('50')
   await expect(page.locator('#task-history-body tr')).to_have_count(25)
   failed=True;await page.locator('#task-history-refresh').click()
   await expect(page.locator('#task-history-warning')).to_contain_text('保留上次结果')
   await expect(page.locator('#task-history-body tr')).to_have_count(25)
   failed=False;await page.locator('#task-history-refresh').click()
   await expect(page.locator('#task-history-warning')).not_to_contain_text('保留上次结果')
   source_failed=True;await page.locator('#task-history-refresh').click()
   await expect(page.locator('#task-history-warning')).to_be_visible()
   await expect(page.locator('#task-history-warning')).to_contain_text('ACP 历史读取失败')
   source_failed=False;empty=True;await page.locator('#task-history-refresh').click()
   await expect(page.locator('#task-history-body')).to_contain_text('暂无符合条件')
   await expect(page.locator('#history-total')).to_have_text('0')
   await expect(page.locator('#task-history-warning')).not_to_be_visible()
   empty=False;await page.locator('#task-history-refresh').click()
   await expect(page.locator('#history-total')).to_have_text('25')
   for width in (390,360):
    await page.set_viewport_size({'width':width,'height':844})
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await expect(page.locator('.nav-item[href="#history"]')).to_be_visible()
    assert await page.locator('#task-history-body tr').first.evaluate('e => e.getBoundingClientRect().width <= innerWidth - 30')
    await expect(page.locator('#task-history-body tr').first.locator('.history-state')).to_be_visible()
    await expect(page.locator('#task-history-body tr').first.locator('button')).to_be_visible()
   await page.goto(base+'?site=sensecore#history',wait_until='networkidle')
   await expect(page.locator('#task-history-body a').first).to_have_attribute('href','https://console.sensecore.cn/cn-sh-01/acp/list/detail?id=job-0')
   await page.set_viewport_size({'width':1440,'height':1000})
   await page.screenshot(path=str(ROOT/'data/history-mock.png'),full_page=True)
   assert not errors,errors
   assert '/api/history' not in calls,'history list must not request metric curves'
   await browser.close();print('History UI passed: lazy loading, paging, filters, safe names, trusted links, both sites, errors, mobile, no metric queries or mutations.')
 finally:server.shutdown();server.server_close()
asyncio.run(main())
