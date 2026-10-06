"""Read-only UI fixtures for hourly billing, refresh failures and responsive charts."""
import asyncio
import functools
import threading
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options
from v2_browser_smoke import Handler

ROOT = Path(__file__).resolve().parents[1]


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    end = datetime(2026, 10, 6, 2, tzinfo=ZoneInfo('Asia/Shanghai'))
    start = end - timedelta(hours=23)
    values = [0, 1.25, 0, 4.75] + [0] * 20
    mode, account_id = 'ok', 7
    started, release = asyncio.Event(), asyncio.Event()
    errors, calls = [], []
    def section(data):
        return {'data': data, 'status': 'ok', 'updated_at': end.isoformat()}
    async def api(route):
        assert route.request.method == 'GET', 'No account mutations during chart QA'
        url = urlparse(route.request.url)
        params = parse_qs(url.query)
        if url.path == '/api/tokenhub/state':
            data = {'connected': True, 'status': 'ok', 'sections': {
                'account': section({'id': account_id, 'username': 'chart-test', 'balance': 100}),
                'stats': section({'today_actual_cost': 6}),
                'account_quotas': section([{'name': f'iLab{i}', 'platform': 'openai', 'available': True,
                    'updated_at': end.isoformat(), 'windows': [{'key': 'five_hour', 'utilization': 0},
                    {'key': 'seven_day', 'utilization': 58}]} for i in (1, 2)]),
                'quotas': section([]), 'subscriptions': section([])}}
        elif url.path == '/api/tokenhub/trend' and params.get('granularity') == ['hour']:
            calls.append('hour')
            if mode == 'fail':
                await route.fulfill(status=503, json={'error': '小时费用暂不可用'}); return
            if mode == 'delay':
                started.set()
                await release.wait()
            costs = [0] * 24 if mode == 'zero' else values
            data = {'hours': 24, 'timezone': 'Asia/Shanghai', 'start_hour': start.isoformat(),
                    'end_hour': end.isoformat(), 'updated_at': end.isoformat(),
                    'trend': [{'date': (start + timedelta(hours=i)).isoformat(), 'actual_cost': v}
                              for i, v in enumerate(costs)]}
        elif url.path == '/api/tokenhub/trend':
            calls.append('day')
            days = int(params.get('days', ['7'])[0])
            day = end.date() - timedelta(days=days - 1)
            data = {'days': days, 'start_date': day.isoformat(), 'end_date': end.date().isoformat(),
                    'updated_at': end.isoformat(), 'trend': [
                        {'date': (day + timedelta(days=i)).isoformat(), 'actual_cost': 6} for i in range(days)]}
        elif url.path == '/api/tokenhub/keys':
            data = {'items': [], 'total': 0, 'pages': 1, 'page': 1}
        else:
            data = {'status': 'ok', 'tasks': [], 'clusters': [], 'items': []}
        await route.fulfill(json=data)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            await page.goto(f'http://127.0.0.1:{server.server_port}/#tokenhub', wait_until='networkidle')
            await expect(page.locator('#tokenhub-hourly-summary')).to_contain_text('$6.00')
            await expect(page.locator('#tokenhub-hourly-summary')).to_contain_text('$4.75')
            await expect(page.locator('#tokenhub-hourly-status')).to_contain_text('无使用显示 $0')
            chart_option = "el => echarts.getInstanceByDom(el).getOption()"
            chart = page.locator('#tokenhub-hourly-chart')
            option = await chart.evaluate(chart_option)
            assert option['series'][0]['data'] == values
            assert option['series'][0]['connectNulls'] is True
            assert option['xAxis'][0]['data'][0] == '2026-10-05 03:00'
            assert option['xAxis'][0]['data'][-1] == '2026-10-06 02:00'
            hour_calls = calls.count('hour')
            await page.get_by_role('button', name='30 天', exact=True).click()
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$180.00')
            assert calls.count('hour') == hour_calls, 'Day range must not refetch the independent hourly series'
            out = ROOT / 'data/hourly-smoke'; out.mkdir(exist_ok=True)
            for width in (1440, 1024, 760, 390, 320):
                await page.set_viewport_size({'width': width, 'height': 1000})
                for theme in ('light', 'dark'):
                    if await page.locator('html').get_attribute('data-theme') != theme:
                        await page.locator('#theme-toggle').click()
                    for id in ('tokenhub-trend-chart', 'tokenhub-hourly-chart'):
                        await page.wait_for_function('''id => {
                            const el = document.getElementById(id), chart = echarts.getInstanceByDom(el);
                            return chart && Math.abs(chart.getWidth() - el.clientWidth) < 2 && Math.abs(chart.getHeight() - el.clientHeight) < 2;
                        }''', arg=id)
                        box = await page.locator('#' + id).bounding_box()
                        assert 235 <= box['height'] <= 340, (id, width, box)
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                    option = await chart.evaluate(chart_option)
                    assert option['tooltip'][0]['renderMode'] == 'richText'
                    expected = await page.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--ink").trim()')
                    assert option['tooltip'][0]['textStyle']['color'] == expected
                    if width in (1440, 390):
                        await page.locator('.th-dashboard-grid').screenshot(path=str(out / f'{theme}-{width}.png'), animations='disabled')
            await page.set_viewport_size({'width': 1440, 'height': 1000})
            mode = 'fail'
            await page.locator('#tokenhub-refresh').click()
            await expect(page.locator('#tokenhub-hourly-status')).to_contain_text('保留旧数据')
            assert (await chart.evaluate(chart_option))['series'][0]['data'] == values
            mode = 'zero'
            await page.locator('#tokenhub-refresh').click()
            await expect(page.locator('#tokenhub-hourly-summary')).to_contain_text('$0.00')
            assert (await chart.evaluate(chart_option))['series'][0]['data'] == [0] * 24
            mode = 'delay'
            await page.locator('#tokenhub-refresh').click()
            await asyncio.wait_for(started.wait(), 5)
            await page.get_by_role('button', name='API 密钥', exact=True).click()
            release.set()
            mode = 'ok'
            await page.get_by_role('button', name='仪表板', exact=True).click()
            await expect(page.locator('#tokenhub-hourly-summary')).to_contain_text('$6.00')
            account_id = 8; mode = 'fail'
            await expect(page.locator('#tokenhub-refresh')).to_be_enabled()
            await page.locator('#tokenhub-refresh').click()
            await expect(page.locator('#tokenhub-hourly-status')).to_contain_text('尚未取得小时费用')
            await expect(page.locator('#tokenhub-hourly-summary')).not_to_contain_text('$6.00')
            assert not errors, errors
            await browser.close()
            print('Hourly charts passed: 24 Shanghai buckets, zero use, continuous line, totals/peak, independent ranges, stale recovery, tab cancellation, account reset, bounded heights, five widths and both themes; no remote writes.')
    finally:
        release.set()
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
