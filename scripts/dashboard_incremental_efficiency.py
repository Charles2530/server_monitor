"""Compare dashboard DOM work against the saved dirty baseline; every API is mocked."""
import asyncio
import functools
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options
from v2_browser_smoke import Handler

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'data/efficiency-baseline-1010-002608'


async def audit(browser, url, baseline):
    page = await browser.new_page(viewport={'width': 1440, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    revision = 0
    calls = []
    stamp = datetime.now(timezone.utc)
    release = asyncio.Event()

    async def api(route):
        path = urlsplit(route.request.url).path
        calls.append(path)
        assert route.request.method == 'GET'
        if path == '/api/state':
            await release.wait()
        updated = (stamp + timedelta(seconds=revision)).isoformat()
        tasks = [{'id': f'ACP:{i}', 'name': f'charles {i}', 'kind': 'ACP', 'state': 'running',
                  'can_stop': True, 'gpu_count': 1, 'replicas': 1, 'cluster': 'fixture',
                  'updated_at': updated, 'metrics': {'gpu': revision + i}, 'url': ''} for i in range(20)]
        payload = {'site': 'maoshanwang', 'status': 'ok', 'connected': True, 'updated_at': updated,
                   'tasks': tasks, 'waiting_tasks': [], 'clusters': [{'id': 'fixture', 'name': 'fixture', 'available_gpus': revision}],
                   'source_status': {key: {'status': 'ok', 'updated_at': updated} for key in ['ACP', 'CCI', 'ACP_queue', 'CCI_queue']},
                   'volumes': [], 'mount_status': {}, 'warnings': [], 'sections': {}}
        if path == '/api/history':
            payload = {'series': [{'metric': 'gpu', 'label': 'GPU', 'points': [[1791586800, 50], [1791586860, 60]]}]}
        await route.fulfill(json=payload)

    await page.route('**/api/**', api)
    if baseline:
        async def script(route):
            await route.fulfill(path=BASELINE / urlsplit(route.request.url).path.lstrip('/'), content_type='application/javascript')
        for filename in ['app.js', 'acp.js']:
            await page.route(f'**/static/{filename}', script)
    await page.goto(url + '/?task_id=ACP:7#storage', wait_until='domcontentloaded')
    await page.evaluate("location.hash='tasks'")
    await expect(page.locator('#tasks-view')).to_be_visible()
    await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    release.set()
    await expect(page.locator('#detail-title')).to_have_text('charles 7')
    await page.locator('.task-card').nth(7).click()
    await expect(page.locator('#detail-title')).to_have_text('charles 7')
    await page.evaluate("location.hash='storage'")
    await expect(page.locator('#storage-section')).to_be_visible()
    await page.evaluate('''() => {
      window.__mutations = {tasks: 0, overview: 0, clusters: 0};
      for (const [key, id] of [['tasks','task-list'], ['overview','overview-task-list'], ['clusters','cluster-body']]) {
        new MutationObserver(records => __mutations[key] += records.length)
          .observe(document.getElementById(id), {subtree:true, childList:true, attributes:true, characterData:true});
      }
    }''')
    history_calls = calls.count('/api/history')
    for revision in range(1, 7):
        previous = calls.count('/api/state')
        await page.evaluate("document.dispatchEvent(new CustomEvent('monitor-refresh'))")
        for _ in range(100):
            if calls.count('/api/state') > previous:
                break
            await asyncio.sleep(.01)
        await page.wait_for_timeout(50)
    mutations = await page.evaluate('({...__mutations})')
    assert calls.count('/api/history') == history_calls, 'Hidden chart fetched history'
    await page.evaluate("location.hash='tasks'")
    await expect(page.locator('#detail-title')).to_have_text('charles 7')
    await expect(page.locator('.task-card')).to_have_count(20)
    await expect(page.locator('.task-card').nth(7)).to_contain_text('13')
    await page.evaluate("location.hash='overview'")
    await expect(page.locator('#cluster-body')).to_contain_text('6')
    assert not errors, errors
    await page.close()
    print(json.dumps({'variant': 'before' if baseline else 'after', 'six_off_page_snapshots': mutations,
                      'selection_retained': True, 'latest_data_on_return': True, 'js_errors': len(errors)}), flush=True)
    return mutations


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            url = f'http://127.0.0.1:{server.server_port}'
            await audit(browser, url, True)
            after = await audit(browser, url, False)
            assert after == {'tasks': 0, 'overview': 0, 'clusters': 0}, after
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()


asyncio.run(main())
