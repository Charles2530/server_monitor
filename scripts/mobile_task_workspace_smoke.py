"""Task navigation on phones, with local fixture APIs and no cloud operations."""
import asyncio
import base64
import functools
import json
import subprocess
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options
from v2_browser_smoke import ROOT, Handler

class TaskHandler(Handler):
    def do_GET(self):
        path=urlsplit(self.path).path
        if path in ('/terminal','/ide'): self.path='/static'+path+'.html'
        super().do_GET()

async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(TaskHandler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    now=datetime.now(timezone.utc).isoformat()
    tasks=[dict(id='CCI:'+name,name='charles '+name,kind='CCI',cluster='modeltoolchain',state='running',gpu_count=8,replicas=1,updated_at=now,metrics={'gpu':30}) for name in ('alpha','bravo')]
    requests=[]; errors=[]; sequence=0
    source_status={k:{'status':'ok','updated_at':now} for k in ('CCI','ACP')}
    terminal_task='CCI:bravo'; terminal_site='maoshanwang'; terminal_targets=[('pod','main')]
    async def api(route):
        nonlocal sequence,terminal_task,terminal_site
        path=urlsplit(route.request.url).path; query=parse_qs(urlsplit(route.request.url).query)
        body=route.request.post_data_json or {}
        requests.append((path,route.request.headers.get('x-monitor-site'),body,query))
        assert route.request.method=='GET' or path.startswith('/api/terminal/'), path
        if path=='/api/state': data=dict(tasks=tasks,clusters=[],waiting_tasks=[],connected=True,status='ok',updated_at=now,source_status=source_status)
        elif path=='/api/history':data={'series':[]}
        elif path=='/api/logs/targets':data={'task_id':query['task_id'][0],'workers':[{'name':'worker','containers':['main']}]}
        elif path=='/api/logs':data={'task_id':query['task_id'][0],'worker':'worker','container':'main','text':'log for '+query['task_id'][0],'entries':1,'updated_at':now}
        elif path=='/api/terminal/targets':
            terminal_task=body['task_id'];terminal_site=route.request.headers.get('x-monitor-site','maoshanwang');data={'request_id':'targets'}
        elif path=='/api/terminal/status':
            base='https://console.sensecore.cn/cn-sh-01' if terminal_site=='sensecore' else 'https://console.ms-sc-01.maoshanwangtech.com'
            result={'site':terminal_site,'task_id':terminal_task,'name':'charles bravo','targets':[{'instance':instance,'container':container,'url':base+'/cci/terminal?workspace=/w&app_name='+terminal_task.split(':')[1]+'&container_instance_name='+instance+'&container_name='+container} for instance,container in terminal_targets]} if body['request_id']=='targets' else {'session_id':'shell','site':terminal_site,'task_id':terminal_task}
            data={'pending':False,'result':result}
        elif path=='/api/terminal/open':data={'request_id':'open'}
        elif path=='/api/terminal/read':
            if body.get('cursor'):await asyncio.sleep(.15)
            data={'type':'output','cursor':1,'sequence':sequence,'chunks':[] if body.get('cursor') else [base64.b64encode(b'Task shell ready\r\n$ ').decode()],'closed':False,'error':None}
        elif path=='/api/terminal/write':
            sequence=body['sequence'];data={'type':'ack','sequence':sequence}
        elif path=='/api/terminal/close':data={'closed':True}
        else:data={}
        await route.fulfill(json=data)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(**browser_launch_options())
            context=await browser.new_context(viewport={'width':393,'height':780},has_touch=True,is_mobile=True)
            await context.route('**/api/**',api)
            await context.route_web_socket('**/api/terminal/socket',lambda ws:ws.close())
            page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            base=f'http://127.0.0.1:{server.server_port}'
            await page.goto(base+'/',wait_until='networkidle')
            await expect(page).to_have_url(base+'/#tasks')
            await expect(page.locator('#task-list')).to_be_visible()
            await expect(page.locator('#detail-panel')).not_to_be_visible()
            await expect(page.locator('.mobile-cci-terminal')).to_have_count(2)
            await page.locator('#task-search').fill('bravo')
            await page.screenshot(path=str(ROOT/'data/mobile-task-workspace-list.png'),full_page=False)
            # Primary CCI flow: directly from its card to its terminal, no detail hop.
            await page.locator('.mobile-cci-terminal').tap()
            await expect(page.locator('.xterm-rows')).to_contain_text('Task shell ready')
            assert [b for path,site,b,q in requests if path=='/api/terminal/targets'][-1]['task_id']=='CCI:bravo'
            await page.locator('#terminal-mobile-back').tap()
            await expect(page.locator('#detail-title')).to_have_text('charles bravo')
            await expect(page.locator('#detail-panel')).to_be_visible()
            await page.locator('#mobile-task-back').tap()
            await expect(page.locator('#task-list')).to_be_visible()
            await page.locator('.task-card').tap()
            await expect(page.locator('#detail-title')).to_have_text('charles bravo')
            await expect(page.locator('#mobile-task-tools')).to_be_in_viewport()
            await expect(page.locator('#cci-terminal-selected')).to_be_in_viewport()
            await expect(page.locator('#mobile-editor-note')).to_contain_text('电脑')
            await page.locator('#detail-tab-logs').tap()
            await expect(page.locator('#logs-output')).to_contain_text('CCI:bravo')
            await page.locator('#cci-terminal-selected').tap()
            await expect(page).to_have_url(__import__('re').compile('/terminal\\?'))
            await expect(page.locator('.xterm-rows')).to_contain_text('Task shell ready')
            await page.locator('#terminal-mobile-back').tap()
            await expect(page.locator('#detail-title')).to_have_text('charles bravo')
            await expect(page.locator('#detail-panel')).to_be_visible()
            await expect(page.locator('#task-search')).to_have_value('bravo')
            await expect(page.locator('#detail-tab-logs')).to_have_attribute('aria-selected','true')
            await expect(page.locator('#logs-output')).to_contain_text('CCI:bravo')
            for width,height in ((320,640),(393,780),(844,390),(844,220)):
                await page.set_viewport_size({'width':width,'height':height})
                await expect(page.locator('#detail-panel')).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,height)
                assert await page.evaluate("matchMedia('(pointer:coarse)').matches"), 'Touch fixture must remain coarse'
                await page.locator('#mobile-task-tools').scroll_into_view_if_needed()
                await expect(page.locator('#cci-terminal-selected')).to_be_in_viewport()
            await page.set_viewport_size({'width':393,'height':780})
            await page.screenshot(path=str(ROOT/'data/mobile-task-workspace-logs.png'),full_page=False)
            # Old APK styles/scripts are injected after document load.
            await page.add_style_tag(content=subprocess.check_output(['git','show','5e706d7:static/mobile.css'],cwd=ROOT,text=True))
            await page.evaluate(subprocess.check_output(['git','show','5e706d7:static/mobile.js'],cwd=ROOT,text=True))
            await expect(page.locator('#mobile-task-tools')).to_have_count(1)
            await expect(page.locator('#mobile-task-back')).to_have_count(1)
            await expect(page.locator('#cci-terminal-selected')).to_be_in_viewport()
            await page.locator('#theme-toggle').tap()
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(ROOT/'data/mobile-task-workspace-dark.png'),full_page=False)
            await page.locator('#mobile-task-back').tap()
            await expect(page.locator('#task-list')).to_be_visible()
            await page.locator('.task-card').tap()
            await expect(page.locator('#detail-panel')).to_be_visible()
            await page.go_back()
            await expect(page.locator('#task-list')).to_be_visible()
            await page.go_forward()
            await expect(page.locator('#detail-panel')).to_be_visible()
            await expect(page.locator('#detail-title')).to_have_text('charles bravo')
            await page.reload(wait_until='networkidle')
            await expect(page.locator('#detail-panel')).to_be_visible()
            await expect(page.locator('#detail-title')).to_have_text('charles bravo')
            # Time range must survive tool navigation immediately, without a poll.
            for hours in (24,168):
                await page.locator('#detail-tab-monitor').tap()
                await page.locator(f'.range-button[data-hours="{hours}"]').tap()
                await page.locator('#cci-terminal-selected').tap()
                await expect(page.locator('.xterm-rows')).to_contain_text('Task shell ready')
                await page.locator('#terminal-mobile-back').tap()
                await expect(page.locator(f'.range-button[data-hours="{hours}"]')).to_have_attribute('aria-pressed','true')
            # Global overview remains available and must not inherit list scroll.
            await page.evaluate('MonitorTaskContext.remember({scroll:205})')
            await page.locator('.nav-item[href="#overview"]').tap()
            await expect(page.locator('#overview-view')).to_be_visible()
            await page.wait_for_timeout(100)
            assert await page.evaluate('scrollY') == 0
            before_logs=len([1 for path,site,b,q in requests if path=='/api/logs'])
            await page.locator('.nav-item[href="#tasks"]').tap()
            await expect(page.locator('#task-list')).to_be_visible()
            await page.wait_for_timeout(150)
            assert len([1 for path,site,b,q in requests if path=='/api/logs'])==before_logs
            await expect(page.locator('#detail-tab-monitor')).to_have_attribute('aria-selected','true')
            # A partial collection cannot replace the requested task with another shell target.
            source_status['CCI']['status']='error'
            await page.goto(base+'/?task_id=CCI:missing#tasks',wait_until='networkidle')
            await expect(page.locator('#mobile-task-notice')).to_contain_text('暂时无法确认')
            await expect(page.locator('#detail-panel')).not_to_be_visible()
            assert 'task_id=' in page.url
            source_status['CCI']['status']='ok'
            await page.evaluate("document.dispatchEvent(new CustomEvent('monitor-refresh'))")
            await expect(page.locator('#mobile-task-notice')).to_contain_text('已不在运行列表')
            # A missing target must not silently open the first available task.
            await page.goto(base+'/?site=sensecore&task_id=CCI:missing#tasks',wait_until='networkidle')
            await expect(page.locator('#mobile-task-notice')).to_contain_text('已不在运行列表')
            await expect(page.locator('#task-list')).to_be_visible()
            await expect(page.locator('#detail-panel')).not_to_be_visible()
            # A fresh entry honors an explicit route, even on phones.
            await page.goto(base+'/#overview',wait_until='networkidle')
            await expect(page.locator('#overview-view')).to_be_visible()
            # No stored context from the first site may hide tasks on the second site.
            await page.goto(base+'/?site=sensecore#tasks',wait_until='networkidle')
            await expect(page.locator('#task-search')).to_have_value('')
            await expect(page.locator('.task-card')).to_have_count(2)
            await page.locator('.task-card').nth(1).tap()
            await page.locator('#detail-tab-logs').tap()
            await expect(page.locator('#logs-output')).to_contain_text('CCI:bravo')
            assert any(path=='/api/logs' and site=='sensecore' and q.get('task_id')==['CCI:bravo'] for path,site,b,q in requests)
            await page.locator('[data-site-switch]').first.tap()
            await expect(page).to_have_url(__import__('re').compile(r'site=maoshanwang#tasks$'))
            assert 'task_id=' not in page.url
            await expect(page.locator('#task-list')).to_be_visible()
            # A real CCI target choice stays explicit. Storage failure must not break return.
            terminal_targets[:]=[('pod-a','main'),('pod-b','trainer')]
            isolated=await browser.new_context(viewport={'width':393,'height':780},has_touch=True,is_mobile=True)
            await isolated.route('**/api/**',api)
            await isolated.route_web_socket('**/api/terminal/socket',lambda ws:ws.close())
            await isolated.add_init_script("Object.defineProperty(window,'sessionStorage',{get(){throw new Error('storage blocked')}})")
            fresh=await isolated.new_page();fresh.on('pageerror',lambda e:errors.append(str(e)))
            before=len([1 for path,site,b,q in requests if path=='/api/terminal/open'])
            await fresh.goto(base+'/terminal?site=sensecore&task_id=CCI:alpha&task_view=logs',wait_until='networkidle')
            await expect(fresh.locator('#terminal-instance')).to_be_visible()
            assert len([1 for path,site,b,q in requests if path=='/api/terminal/open'])==before
            await fresh.locator('#terminal-instance').select_option('pod-b')
            await fresh.locator('#terminal-open').tap()
            await expect(fresh.locator('.xterm-rows')).to_contain_text('Task shell ready')
            opened=[(site,b) for path,site,b,q in requests if path=='/api/terminal/open'][-1]
            assert opened[0]=='sensecore' and opened[1]['task_id']=='CCI:alpha' and opened[1]['instance']=='pod-b' and opened[1]['container']=='trainer',opened
            await fresh.locator('#terminal-mobile-back').tap()
            await expect(fresh.locator('#detail-title')).to_have_text('charles alpha')
            await expect(fresh.locator('#detail-tab-logs')).to_have_attribute('aria-selected','true')
            await fresh.locator('.nav-item[href="#overview"]').tap()
            await expect(fresh.locator('#overview-view')).to_be_visible()
            await expect(fresh.locator('.nav-item')).to_have_count(7)
            await isolated.close()
            # Fresh desktop retains overview and tools below the chart.
            desktop=await browser.new_page(viewport={'width':1440,'height':1000})
            await desktop.route('**/api/**',api)
            await desktop.goto(base+'/',wait_until='networkidle')
            await expect(desktop).to_have_url(base+'/#overview')
            await expect(desktop.locator('#mobile-task-tools')).not_to_be_visible()
            assert not errors,errors
            await browser.close()
            print('Mobile task workspace passed: default, exact task/log return, filters, sites, missing target, history, reload, portrait/landscape and desktop.')
    finally:server.shutdown();server.server_close()

if __name__=='__main__':asyncio.run(main())
