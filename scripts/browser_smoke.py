"""Local-only browser smoke test; needs the dashboard server running."""
import asyncio
import json
import re
from pathlib import Path
from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect

ROOT=Path(__file__).resolve().parent.parent

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(**browser_launch_options())
        page=await browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto('http://127.0.0.1:8766/#tasks',wait_until='networkidle')
        await page.locator('.task-card').first.wait_for(timeout=90000)
        total=await page.locator('.task-card').count()
        assert total>0
        await page.get_by_role('button',name='ACP',exact=True).click()
        assert await page.locator('.task-card').count()>0
        assert 'ACP' in await page.locator('.task-card').first.inner_text()
        await page.locator('.task-card').first.click()
        await expect(page.locator('#history-chart')).to_have_attribute('aria-label',re.compile('8个图表系列'))
        await page.get_by_role('button',name='7 天',exact=True).click()
        await page.get_by_role('searchbox').fill('no-such-charles-task-qa')
        assert await page.get_by_text('没有符合条件的任务',exact=True).is_visible()
        await page.get_by_role('searchbox').fill('')
        await page.get_by_role('button',name='全部',exact=True).click()
        await page.get_by_role('button',name='1 小时',exact=True).click()
        await page.locator('.task-card').filter(has_text='ACP:').first.click()
        await expect(page.locator('#history-chart')).to_have_attribute('aria-label',re.compile('8个图表系列'))
        await page.evaluate('window.scrollTo(0,0)')
        await page.screenshot(path=str(ROOT/'data/dashboard-desktop.png'),full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'mobile horizontal overflow'
        await page.screenshot(path=str(ROOT/'data/dashboard-mobile.png'),full_page=True)
        failure={'status':'partial','connected':True,'updated_at':'2026-09-29T07:00:00Z','interval_seconds':60,'tasks':[],'clusters':[],'source_status':{'CCI':{'status':'error'},'ACP':{'status':'error'}},'error':'任务列表获取失败'}
        await page.route('**/api/state',lambda route:route.fulfill(json=failure))
        await page.reload(wait_until='networkidle')
        assert await page.locator('#summary-tasks').inner_text()=='—'
        assert await page.get_by_text('任务数量暂时未知',exact=True).is_visible()
        failure.update(status='ok',source_status={'CCI':{'status':'ok'},'ACP':{'status':'ok'}})
        await page.reload(wait_until='networkidle')
        assert await page.locator('#summary-tasks').inner_text()=='0'
        assert await page.get_by_text('当前没有运行任务',exact=True).is_visible()
        failure.update(status='disconnected',connected=False)
        await page.reload(wait_until='networkidle')
        assert await page.get_by_text('等待连接云会话',exact=True).is_visible()
        assert errors==[], errors
        await browser.close()
        print(json.dumps({'result':'passed','live_tasks':total,'checks':['ACP filter','search empty','8 worker metric series','7-day selection','desktop screenshot','390px no overflow','failed source unknown','confirmed empty zero','disconnected state','no JS errors']},ensure_ascii=False))

asyncio.run(main())
