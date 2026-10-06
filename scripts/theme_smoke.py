"""Theme interaction checks against local static files and mocked read-only APIs."""
import asyncio
import functools
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect
from v2_browser_smoke import Handler

ROOT = Path(__file__).resolve().parent.parent


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    now = datetime.now(timezone.utc)
    errors = []
    quota_count = 1
    screenshots = ROOT / 'data' / 'theme-smoke'
    screenshots.mkdir(parents=True, exist_ok=True)

    async def api(route):
        assert route.request.method == 'GET', 'Theme switching must not mutate cloud data'
        path = route.request.url.split('/api/')[1].split('?')[0]
        if path == 'state':
            data = {'status': 'ok', 'connected': True, 'updated_at': now.isoformat(),
                    'interval_seconds': 60, 'clusters': [], 'waiting_tasks': [], 'source_status': {},
                    'tasks': [{'id': 'ACP:test', 'kind': 'ACP', 'name': 'charles theme test',
                               'state': 'running', 'gpu_count': 1, 'replicas': 1, 'metrics': {}}]}
        elif path == 'history':
            data = {'series': [{'metric': 'gpu', 'label': 'GPU', 'points': [[now.timestamp(), 50]]}]}
        elif path == 'tokenhub/state':
            def section(value):
                return {'data': value, 'updated_at': now.isoformat(), 'status': 'ok'}
            data = {'status': 'ok', 'connected': True, 'sections': {
                'account': section({'id': 7, 'username': 'charles', 'balance': 12.5}),
                'stats': section({'today_tokens': 123456, 'today_requests': 123, 'today_actual_cost': .75}),
                'account_quotas': section([{'name': 'iLab', 'platform': 'openai', 'available': True,
                    'updated_at': now.isoformat(), 'windows': [
                        {'key': 'five_hour', 'utilization': 0, 'resets_at': now.isoformat()},
                        {'key': 'seven_day', 'utilization': 42, 'resets_at': now.isoformat()}]}]),
                'quotas': section([]), 'subscriptions': section([])}}
            data['sections']['account_quotas']['data'] *= quota_count
        elif path == 'tokenhub/keys':
            data = {'page': 1, 'pages': 1, 'total': 1, 'items': [{'id': 1, 'name': 'Workspace key',
                    'group_id': 2, 'group_name': 'OpenAI', 'status': 'active', 'current_concurrency': 2,
                    'today_actual_cost': .75, 'last_30_days_actual_cost': 12.5}]}
        elif path == 'tokenhub/key-groups':
            data = {'account_id': 7, 'key': {'id': 1, 'name': 'Workspace key', 'group_id': 2},
                    'items': [{'id': 2, 'name': 'OpenAI', 'platform': 'openai'}]}
        elif path == 'tokenhub/usage':
            data = {'page': 1, 'pages': 1, 'total': 1, 'items': [{'model': 'test-model',
                    'created_at': now.isoformat(), 'input_tokens': 500, 'output_tokens': 100,
                    'actual_cost': .05, 'key_name': 'Workspace key'}]}
        elif path == 'tokenhub/trend' and parse_qs(urlparse(route.request.url).query).get('granularity') == ['hour']:
            end = now.replace(minute=0, second=0, microsecond=0)
            start = end - timedelta(hours=23)
            data = {'hours': 24, 'start_hour': start.isoformat(), 'end_hour': end.isoformat(), 'updated_at': now.isoformat(),
                    'trend': [{'date': (start + timedelta(hours=i)).isoformat(), 'actual_cost': i / 10} for i in range(24)]}
        elif path == 'tokenhub/trend':
            start = now.date() - timedelta(days=6)
            data = {'days': 7, 'start_date': start.isoformat(), 'end_date': now.date().isoformat(),
                    'updated_at': now.isoformat(), 'trend': [
                        {'date': (start + timedelta(days=i)).isoformat(), 'actual_cost': (i + 1) / 4}
                        for i in range(7)]}
        elif path == 'acp/options':
            data = {'template': {'id': 'ACP:test', 'name': 'charles test'}, 'gpu_options': [1, 2, 4, 8]}
        elif path == 'storage':
            data = {'volumes': [], 'warnings': [], 'mount_status': {}, 'status': 'ok'}
        elif path == 'task-history':
            data = {'tasks': [], 'loading': False, 'source_status': {}, 'filtered_stopped': {}, 'warnings': []}
        elif path == 'utilization-queue':
            data = {'tasks': [], 'loading': False, 'status': 'ok', 'warnings': []}
        else:
            raise AssertionError(path)
        await route.fulfill(json=data)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            url = f'http://127.0.0.1:{server.server_port}/'
            await page.goto(url, wait_until='networkidle')
            toggle = page.get_by_role('button', name='深色模式', exact=True)
            await expect(toggle).to_be_visible(timeout=3000)
            await expect(page.locator('.sidebar')).not_to_contain_text('本地采集器')
            await expect(page.locator('.sidebar')).not_to_contain_text('CCI · ACP · 标准集群')
            await expect(page.locator('.profile')).to_contain_text('charles')
            await page.screenshot(path=str(screenshots / 'overview-light.png'), full_page=True, animations='disabled')
            light_background = await page.locator('body').evaluate('e => getComputedStyle(e).backgroundColor')
            await toggle.click()
            await expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            await expect(page.get_by_role('button', name='浅色模式', exact=True)).to_be_visible()
            assert await page.locator('body').evaluate('e => getComputedStyle(e).backgroundColor') != light_background
            for selector in ('.site-switcher', '#cluster-body tr'):
                channels = await page.locator(selector).first.evaluate('e => getComputedStyle(e).backgroundColor.match(/\d+/g).slice(0, 3).map(Number)')
                assert max(channels) < 160, (selector, channels)
            await page.reload(wait_until='networkidle')
            await expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            for name in ('tasks', 'acp', 'waiting', 'storage', 'history', 'tokenhub', 'overview'):
                await page.locator(f'.nav-item[href="#{name}"]').click()
                await expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.screenshot(path=str(screenshots / f'{name}-dark.png'), full_page=True, animations='disabled')
            await page.locator('.nav-item[href="#tokenhub"]').click()
            await expect(page.locator('#tokenhub-trend-chart canvas')).to_be_visible()
            # Quota content must never stretch the plot into a tall, distorted canvas.
            trend_box = await page.locator('.th-trend-panel').bounding_box()
            quota_box = await page.locator('.th-quota-preview-panel').bounding_box()
            assert (await page.locator('#tokenhub-trend-chart').bounding_box())['height'] <= 340, 'Quota cards stretched the daily plot'

            async def readable(selector, dark=True):
                result = await page.locator(selector).first.evaluate("""el => {
                  const rgb = value => {
                    const numbers = value.match(/[\d.]+/g);
                    if (!numbers) throw new Error(`Invalid color ${JSON.stringify(value)} for ${el.className}; connected=${el.isConnected}`);
                    return numbers.map(Number);
                  };
                  let parent = el, background;
                  do { background = rgb(getComputedStyle(parent).backgroundColor); parent = parent.parentElement; }
                  while (background[3] === 0 && parent);
                  const foreground = rgb(getComputedStyle(el).color);
                  const luminance = values => values.slice(0, 3).map(v => {
                    const c = v / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
                  }).reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
                  const a = luminance(foreground), b = luminance(background);
                  return {contrast: (Math.max(a, b) + .05) / (Math.min(a, b) + .05), background};
                }""")
                assert result['contrast'] >= 4.5, (selector, result)
                if dark:
                    assert max(result['background'][:3]) < 160, (selector, result)

            for selector in ('.th-quota-preview-panel .th-quota-line strong', '.th-quota-preview-panel .th-quota-line span', '.th-badge'):
                await readable(selector)
            await page.locator('#tokenhub-usage-details summary').click()
            await expect(page.locator('#tokenhub-usage-body')).to_contain_text('test-model')
            await page.locator('#tokenhub-usage-details summary').hover()
            await readable('.th-disclosure-label')
            await readable('.th-token-pair span')
            await page.get_by_role('button', name='API 密钥', exact=True).click()
            await expect(page.locator('#tokenhub-keys-status')).to_contain_text('已更新')
            await expect(page.locator('.th-key-copy')).to_be_visible()
            for selector in ('.th-key-group', '.th-key-usage-line span', '.th-key-copy'):
                await readable(selector)
            await page.locator('.th-key-copy').hover()
            await readable('.th-key-copy')
            await page.screenshot(path=str(screenshots / 'tokenhub-keys-dark.png'), full_page=True, animations='disabled')
            await page.locator('.th-group-edit').click()
            await expect(page.locator('#tokenhub-group-select')).to_be_enabled()
            await readable('.th-group-dialog label')
            await page.screenshot(path=str(screenshots / 'tokenhub-group-dark.png'), full_page=True, animations='disabled')
            await page.locator('#tokenhub-group-cancel').click()
            await page.get_by_role('button', name='账号额度', exact=True).click()
            await readable('#tokenhub-account-quotas .th-quota-line strong')
            await page.screenshot(path=str(screenshots / 'tokenhub-quotas-dark.png'), full_page=True, animations='disabled')
            await page.get_by_role('button', name='仪表板', exact=True).click()
            trend_color = "echarts.getInstanceByDom(document.getElementById('tokenhub-trend-chart')).getOption().xAxis[0].axisLabel.color"
            dark_trend_axis = await page.evaluate(trend_color)
            series_color = "echarts.getInstanceByDom(document.getElementById('tokenhub-trend-chart')).getOption().series[0].itemStyle.color"
            dark_series = await page.evaluate(series_color)
            await page.get_by_role('button', name='浅色模式', exact=True).click()
            assert await page.evaluate(trend_color) != dark_trend_axis
            assert await page.evaluate(series_color) != dark_series
            for selector in ('.th-metric-heading', '.th-detail-metrics .th-metric:first-child small', '.th-spend-label', '.th-spend-period small', '.th-quota-line span', '.th-disclosure-label', '.th-token-pair span', '.th-table th'):
                await readable(selector, dark=False)
            for tab in ('keys', 'quotas'):
                await page.locator(f'[data-tokenhub-tab="{tab}"]').click()
                if tab == 'keys':
                    await expect(page.locator('#tokenhub-keys-status')).to_contain_text('已更新')
                    for selector in ('.th-key-group', '.th-key-usage-line span', '.th-key-copy', '.th-table th', '.th-key-status', '.th-concurrency'):
                        await readable(selector, dark=False)
                    previous = page.locator('#tokenhub-keys-prev')
                    await expect(previous).to_be_disabled()
                    color_before = await previous.evaluate('e => getComputedStyle(e).backgroundColor')
                    await previous.hover()
                    assert await previous.evaluate('e => getComputedStyle(e).backgroundColor') == color_before
                    await page.locator('.th-group-edit').focus()
                    await page.keyboard.press('Tab')
                    await expect(page.locator('.th-key-copy')).to_be_focused()
                    assert await page.locator('.th-key-copy').evaluate('e => getComputedStyle(e).outlineStyle') == 'solid'
                    await page.locator('.th-group-edit').click()
                    await expect(page.locator('#tokenhub-group-select')).to_be_enabled()
                    await readable('.th-group-dialog label', dark=False)
                    await readable('.th-group-note', dark=False)
                    await page.screenshot(path=str(screenshots / 'tokenhub-group-light.png'), full_page=True, animations='disabled')
                    await page.locator('#tokenhub-group-cancel').click()
                await page.screenshot(path=str(screenshots / f'tokenhub-{tab}-light.png'), full_page=True, animations='disabled')
            await page.get_by_role('button', name='仪表板', exact=True).click()
            await page.screenshot(path=str(screenshots / 'tokenhub-light.png'), full_page=True, animations='disabled')
            for quota_count in (0, 2, 1):
                await page.locator('#tokenhub-refresh').click()
                await expect(page.locator('#tokenhub-quota-preview .th-quota')).to_have_count(quota_count)
                trend_box = await page.locator('.th-trend-panel').bounding_box()
                quota_box = await page.locator('.th-quota-preview-panel').bounding_box()
                assert (await page.locator('#tokenhub-trend-chart').bounding_box())['height'] <= 340, 'Quota cards stretched the daily plot'
                await page.wait_for_function("""() => {
                    const el = document.getElementById('tokenhub-trend-chart');
                    return Math.abs(echarts.getInstanceByDom(el).getHeight() - el.clientHeight) < 2;
                }""")
            for width in (1280, 1024, 760, 390, 320):
                await page.set_viewport_size({'width': width, 'height': 844})
                for theme in ('dark', 'light'):
                    await page.locator('#theme-toggle').click()
                    await expect(page.locator('html')).to_have_attribute('data-theme', theme)
                    for tab in ('dashboard', 'keys', 'quotas'):
                        await page.locator(f'[data-tokenhub-tab="{tab}"]').click()
                        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, theme, tab)
                        if width == 390:
                            await page.screenshot(path=str(screenshots / f'tokenhub-{tab}-{theme}-mobile.png'), full_page=True, animations='disabled')
            await page.set_viewport_size({'width': 1440, 'height': 1000})
            await page.get_by_role('button', name='仪表板', exact=True).click()
            await page.get_by_role('button', name='深色模式', exact=True).click()
            await page.locator('.nav-item[href="#tasks"]').click()
            await expect(page.locator('#history-chart canvas')).to_be_visible()
            chart_color = "echarts.getInstanceByDom(document.getElementById('history-chart')).getOption().xAxis[0].axisLabel.color"
            dark_axis = await page.evaluate(chart_color)
            await page.get_by_role('button', name='浅色模式', exact=True).focus()
            await page.keyboard.press('Enter')
            await expect(page.locator('html')).to_have_attribute('data-theme', 'light')
            assert await page.evaluate(chart_color) != dark_axis
            await page.reload(wait_until='networkidle')
            await expect(page.locator('html')).to_have_attribute('data-theme', 'light')
            for width in (1024, 760, 390, 320):
                await page.set_viewport_size({'width': width, 'height': 844})
                await page.get_by_role('button', name='深色模式', exact=True).click()
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                if width == 390:
                    await page.screenshot(path=str(screenshots / 'mobile-dark.png'), full_page=True, animations='disabled')
                await page.get_by_role('button', name='浅色模式', exact=True).click()
            await page.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('blocked');}})")
            await page.reload(wait_until='networkidle')
            await page.get_by_role('button', name='深色模式', exact=True).click()
            await expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            assert not errors, errors
            await browser.close()
            print('Theme passed: toggle, persistence, keyboard, chart colors, seven pages, TokenHub light/dark contrast, light dialog and disabled/focus states, bounded plot heights (0/1/2 accounts), all TokenHub tabs at five widths in both themes, blocked storage; no cloud writes.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
