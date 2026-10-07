"""Verify inventory rendering, source isolation and responsive layout against localhost."""
import asyncio
import json
from pathlib import Path
import sys
from browser_support import browser_launch_options
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parent.parent
BASE='http://127.0.0.1:8766'

async def main():
    preview='--preview' in sys.argv
    async with async_playwright() as p:
        browser=await p.chromium.launch(**browser_launch_options())
        page=await browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        # Creation catalog is unrelated to this read-only inventory check.
        await page.route(BASE+'/api/acp/options',lambda route:route.fulfill(status=503,json={'code':'source_unavailable'}))
        if preview:
            await page.route(BASE+'/',lambda route:route.fulfill(path=str(ROOT/'static/index.html'),content_type='text/html'))
            for name,content_type in [('app.js','text/javascript'),('navigation.js','text/javascript'),('style.css','text/css')]:
                async def serve(route,request,name=name,content_type=content_type):
                    await route.fulfill(path=str(ROOT/'static'/name),content_type=content_type)
                await page.route(BASE+'/static/'+name,serve)
            state=json.loads((ROOT/'data/inventory-preview.json').read_text())
        else:
            response=await page.request.get(BASE+'/api/state')
            state=await response.json()
        tracked=[c for c in state['clusters'] if c.get('inventory')]
        assert len(tracked)==2,'Wait for the first completed inventory collection'
        await page.route(BASE+'/api/state',lambda route:route.fulfill(json=state))
        await page.goto(BASE+'/#clusters',wait_until='networkidle')
        assert await page.get_by_role('columnheader',name='总 GPU 张',exact=True).is_visible()
        for c in tracked:
            # Exact cell text avoids matching modeltoolchain_test for modeltoolchain.
            row=page.locator('#cluster-body tr').filter(has=page.get_by_text(c['name'],exact=True))
            assert await row.count()==1
            assert await row.locator('td').nth(1).inner_text()==str(c['inventory']['total_gpus'])+(('\n含异常节点 '+str(c['inventory']['abnormal_node_gpus'])+' 张') if c['inventory']['abnormal_node_gpus'] else '')
            assert await row.locator('td').nth(2).locator('.resource-value').inner_text()==str(int(c['available_gpus']))
            assert str(c['inventory']['node_count'])+' 节点' in await row.inner_text()
        assert await page.locator('#inventory-notice').is_visible()
        await page.locator('#clusters-section').scroll_into_view_if_needed()
        await page.screenshot(path=str(ROOT/'data/cluster-inventory-desktop.png'))
        await page.set_viewport_size({'width':390,'height':844})
        await page.locator('#clusters-section').scroll_into_view_if_needed()
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        await page.screenshot(path=str(ROOT/'data/cluster-inventory-mobile.png'))
        stale=tracked[0]
        stale['inventory_error']='总量或节点状态采集失败；保留旧值'
        stale['inventory']['updated_at']='2026-09-28T01:02:03+00:00'
        await page.reload(wait_until='networkidle')
        row=page.locator('#cluster-body tr').filter(has=page.get_by_text(stale['name'],exact=True))
        assert '旧总量' in await row.inner_text() and '旧状态' in await row.inner_text()
        assert await row.locator('td').count()==6, 'Collection timestamps should not occupy a separate column'
        stale['inventory']=None
        await page.reload(wait_until='networkidle')
        assert '—' in await page.locator('#cluster-body tr').filter(has=page.get_by_text(stale['name'],exact=True)).locator('td').nth(1).inner_text()
        assert not errors, errors
        await browser.close()
        print('Inventory browser checks passed: total/remaining separation, node health, stale warnings, missing inventory, 390px no overflow, no JS errors.')

asyncio.run(main())
