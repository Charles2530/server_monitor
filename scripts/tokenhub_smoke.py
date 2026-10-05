"""TokenHub UI contract with simulated data; never contacts the remote account."""
import asyncio
import functools
import re
import threading
import subprocess
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse

from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect
from v2_browser_smoke import Handler

ROOT = Path(__file__).resolve().parents[1]


async def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    now = datetime.now(timezone.utc).isoformat()
    connected = False
    can_connect = True
    broken = False
    trend_broken = False
    trend_shape = "complete"
    trend_delay = False
    trend_started, trend_release = asyncio.Event(), asyncio.Event()
    account_id = 7
    calls = []
    group_id = 2
    group_writes = []
    group_fail = False
    keys_unknown = False
    def section(data):
        return {'data': data, 'updated_at': now, 'status': 'ok', 'error': None}
    async def api(route):
        nonlocal group_id
        request = route.request
        url = urlparse(request.url)
        path = url.path
        calls.append((request.method, path, parse_qs(url.query)))
        if path == '/api/tokenhub/state':
            sections = {} if not connected else {
                'account': section({'id': account_id, 'username': 'charles', 'balance': 12.5}),
                'stats': section({'today_tokens': 123456, 'total_tokens': 999999, 'today_requests': 123, 'today_actual_cost': .75}),
                'quotas': section([{'platform': 'openai', 'windows': {
                    'daily': {'limit_usd': 10, 'used_usd': 2, 'remaining_usd': 8, 'state': 'limited', 'resets_at': now},
                    'weekly': {'state': 'unlimited', 'remaining_usd': None},
                    'monthly': {'state': 'unknown', 'remaining_usd': None}}}]),
                'account_quotas': section([{'name': 'Workspace account', 'platform': 'openai', 'available': True, 'updated_at': now, 'windows': [
                    {'key': 'five_hour', 'utilization': 0, 'resets_at': now},
                    {'key': 'seven_day', 'utilization': 34, 'resets_at': now},
                    {'key': 'daily', 'utilization': None}], 'model_quotas': [], 'credits': []}]),
                'subscriptions': section([])}
            data = {'status': 'ok' if connected else 'disconnected', 'connected': connected, 'connecting': False, 'sections': sections, 'can_connect': can_connect}
        elif path == '/api/tokenhub/trend':
            if trend_broken:
                await route.fulfill(status=503, json={'error': '趋势暂不可用'}); return
            if trend_delay:
                trend_started.set()
                await trend_release.wait()
            days = int(parse_qs(url.query).get('days', ['7'])[0])
            end = datetime.now(ZoneInfo('Asia/Shanghai')).date()
            start = end - timedelta(days=days - 1)
            data = {'days': days, 'start_date': start.isoformat(), 'end_date': end.isoformat(), 'updated_at': now,
                'trend': [{'date': (start + timedelta(days=i)).isoformat(), 'total_tokens': (i + 1) * 10000, 'actual_cost': (i + 1) / 4,
                    'input_tokens': 4000, 'output_tokens': 1000, 'cache_creation_tokens': 0, 'cache_read_tokens': 5000} for i in range(days)]}
            if trend_shape == 'missing':
                data['trend'][0]['actual_cost'] = 0
                data['trend'][2]['actual_cost'] = None
                del data['trend'][1]
            elif trend_shape == 'unknown':
                for row in data['trend']: row['actual_cost'] = None
        elif path == '/api/tokenhub/keys':
            data = {'page': 1, 'pages': 1, 'total': 1, 'updated_at': now, 'items': [{'id': 1, 'name': '<img src=x onerror=alert(1)>', 'status': 'active', 'group_name': 'OpenAI' if group_id == 2 else 'Claude', 'group_id': group_id, 'current_concurrency': 0, 'today_actual_cost': 0, 'last_30_days_actual_cost': 11.3996}]}
            if keys_unknown:
                data['items'][0].update(current_concurrency=None, today_actual_cost=None, last_30_days_actual_cost=None)
                data['usage_error'] = '用量暂未获取'
        elif path == '/api/tokenhub/key-groups':
            data = {'account_id': account_id, 'key': {'id': 1, 'name': 'work', 'group_id': group_id}, 'items': [{'id': 2, 'name': 'OpenAI', 'platform': 'openai'}, {'id': 3, 'name': 'Claude', 'platform': 'anthropic'}]}
        elif path == '/api/tokenhub/key-group':
            group_writes.append(request.post_data_json)
            assert request.headers.get('x-monitor-request') == '1'
            if group_fail:
                await route.fulfill(status=409, json={'error': '分组已变化'}); return
            group_id = request.post_data_json['group_id']
            data = {'id': 1, 'group_id': group_id}
        elif path == '/api/tokenhub/usage':
            if broken:
                await route.fulfill(status=503, json={'error': '暂不可用'}); return
            page = int(parse_qs(url.query).get('page', ['1'])[0])
            data = {'page': page, 'pages': 2, 'total': 21, 'updated_at': now, 'items': [{'id': page, 'model': 'test-model-' + str(page), 'created_at': now, 'input_tokens': 500, 'output_tokens': 100, 'actual_cost': .05, 'key_name': 'work'}]}
        elif path == '/api/state':
            data = {'status': 'ok', 'connected': True, 'updated_at': now, 'tasks': [], 'clusters': [], 'source_status': {}}
        elif path == '/api/acp/options':
            data = {'template': None, 'gpu_options': [], 'clusters': []}
        elif path == '/api/acp/pending':
            data = {'items': []}
        else:
            data = {}
        await route.fulfill(json=data)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(**browser_launch_options())
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/**', api)
            await page.goto(f'http://127.0.0.1:{server.server_port}/#overview', wait_until='networkidle')
            await expect(page.locator('#tokenhub-summary')).to_contain_text('尚未连接')
            await page.locator('.nav-item[href="#tokenhub"]').click()
            await expect(page.locator('.workspace-view:visible')).to_have_count(1)
            await expect(page.locator('#tokenhub-view')).to_be_visible()
            await expect(page.locator('#connect-button')).not_to_be_visible()
            connected = True
            await page.locator('#tokenhub-refresh').click()
            await expect(page.locator('#tokenhub-metrics')).to_contain_text('$12.50')
            await expect(page.locator('#tokenhub-metrics')).to_contain_text('今日消费')
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$7.00')
            await expect(page.locator('#tokenhub-quota-preview')).to_contain_text('34% 已用')
            await page.get_by_role('button', name='30 天', exact=True).click()
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$116.25')
            trend_broken = True
            await page.get_by_role('button', name='7 天', exact=True).click()
            await expect(page.locator('#tokenhub-trend-status')).to_contain_text('保留旧数据')
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$116.25')
            trend_broken = False
            await page.get_by_role('button', name='7 天', exact=True).click()
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$7.00')
            option = await page.locator('#tokenhub-trend-chart').evaluate('(el) => echarts.getInstanceByDom(el).getOption()')
            assert len(option['series']) == 1
            assert option['series'][0]['name'] == '实际花费'
            assert option['series'][0]['data'] == [.25, .5, .75, 1, 1.25, 1.5, 1.75]
            assert await page.locator('#tokenhub-trend-chart').evaluate('(el) => echarts.getInstanceByDom(el).getOption().tooltip[0].valueFormatter(.1234)') == '$0.1234 USD'
            trend_shape = 'missing'
            await page.get_by_role('button', name='7 天', exact=True).click()
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$5.50')
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('已返回记录花费')
            partial = await page.locator('#tokenhub-trend-chart').evaluate('(el) => echarts.getInstanceByDom(el).getOption().series[0]')
            assert partial['data'][:3] == [0, None, None] and not partial['connectNulls']
            trend_shape = 'unknown'
            await page.get_by_role('button', name='7 天', exact=True).click()
            await expect(page.locator('#tokenhub-trend-summary strong')).to_have_text('—')
            await expect(page.locator('#tokenhub-trend-chart')).to_have_attribute('aria-label', re.compile('.*合计 未知.*'))
            trend_shape = 'complete'
            await page.get_by_role('button', name='7 天', exact=True).click()
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$7.00')
            trend_delay = True
            await page.get_by_role('button', name='30 天', exact=True).click()
            await asyncio.wait_for(trend_started.wait(), 5)
            await page.get_by_role('button', name='API 密钥', exact=True).click()
            trend_release.set()
            await expect(page.locator('#tokenhub-trend-status')).to_contain_text('今日尚未结束')
            trend_delay = False
            trend_broken = True
            await page.get_by_role('button', name='仪表板', exact=True).click()
            await expect(page.locator('#tokenhub-trend-status')).to_contain_text('保留旧数据')
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$116.25')
            assert await page.locator('#tokenhub-trend-chart').evaluate('(el) => echarts.getInstanceByDom(el).getOption().xAxis[0].data.length') == 30
            trend_broken = False
            await page.get_by_role('button', name='7 天', exact=True).click()
            await expect(page.locator('#tokenhub-trend-summary')).to_contain_text('$7.00')
            await page.get_by_role('button', name='账号额度', exact=True).click()
            await expect(page.locator('#tokenhub-account-quotas')).to_contain_text('0% 已用')
            await expect(page.locator('#tokenhub-account-quotas')).to_contain_text('使用率未知')
            await page.locator('#refresh-button').click()
            await page.get_by_role('button', name='API 密钥', exact=True).click()
            await expect(page.locator('#tokenhub-keys-body')).to_contain_text('<img src=x')
            await expect(page.locator('#tokenhub-keys-body img')).to_have_count(0)
            await expect(page.locator('#tokenhub-keys-body')).to_contain_text('$0.0000')
            await expect(page.locator('#tokenhub-keys-body')).to_contain_text('$11.3996')
            await expect(page.locator('.th-concurrency')).to_have_text('0')
            await expect(page.get_by_text('密钥额度 USD', exact=True)).to_have_count(0)
            await expect(page.get_by_text('平台计费额度与订阅', exact=True)).to_have_count(0)
            await page.locator('.th-group-edit').click()
            await expect(page.locator('#tokenhub-group-select')).to_be_enabled()
            await expect(page.locator('#tokenhub-group-save')).to_be_disabled()
            await page.locator('#tokenhub-group-select').select_option('3')
            await expect(page.locator('#tokenhub-group-save')).to_be_enabled()
            await page.locator('#tokenhub-group-cancel').click()
            assert not group_writes, 'Selecting or canceling must not write'
            await page.locator('.th-group-edit').click()
            await expect(page.locator('#tokenhub-group-select')).to_be_enabled()
            await page.locator('#tokenhub-group-select').select_option('3')
            await page.locator('#tokenhub-group-save').dblclick()
            await expect(page.locator('#tokenhub-group-dialog')).not_to_be_visible()
            await expect(page.locator('#tokenhub-keys-body')).to_contain_text('Claude')
            assert group_writes == [{'key_id': 1, 'group_id': 3, 'expected_group_id': 2, 'account_id': 7}]
            group_fail = True
            await page.locator('.th-group-edit').click()
            await expect(page.locator('#tokenhub-group-select')).to_be_enabled()
            await page.locator('#tokenhub-group-select').select_option('2')
            await page.locator('#tokenhub-group-save').click()
            await expect(page.locator('#tokenhub-group-status')).to_contain_text('分组已变化')
            await expect(page.locator('#tokenhub-group-save')).to_be_disabled()
            await page.locator('#tokenhub-group-cancel').click()
            group_fail = False
            keys_unknown = True
            await page.locator('#tokenhub-refresh').click()
            await expect(page.locator('#tokenhub-keys-status')).to_contain_text('用量暂未获取')
            await expect(page.locator('.th-concurrency')).to_have_text('—')
            await expect(page.locator('.th-key-usage-line strong').first).to_have_text('—')
            keys_unknown = False
            await page.locator('#tokenhub-refresh').click()
            await expect(page.locator('#tokenhub-keys-body')).to_contain_text('$11.3996')
            for width in (1440, 1024, 760, 390, 320):
                await page.set_viewport_size({'width': width, 'height': 1000})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'keys overflow at {width}'
                await page.screenshot(path=str(ROOT / f'data/tokenhub-keys-{width}.png'), full_page=True)
            await page.locator('.th-group-edit').click()
            await expect(page.locator('#tokenhub-group-select')).to_be_enabled()
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.screenshot(path=str(ROOT / 'data/tokenhub-group-mobile.png'), full_page=True)
            await page.locator('#tokenhub-group-cancel').click()
            await page.set_viewport_size({'width': 1440, 'height': 1000})
            await page.get_by_role('button', name='仪表板', exact=True).click()
            await page.locator('#tokenhub-usage-details summary').click()
            await expect(page.locator('#tokenhub-usage-body')).to_contain_text('test-model-1')
            await page.locator('#tokenhub-usage-next').click()
            await expect(page.locator('#tokenhub-usage-body')).to_contain_text('test-model-2')
            broken = True
            await page.locator('#tokenhub-usage-query').click()
            await expect(page.locator('#tokenhub-usage-status')).to_contain_text('保留')
            await expect(page.locator('#tokenhub-usage-body')).to_contain_text('test-model-2')
            broken = False
            await page.locator('#tokenhub-usage-query').click()
            await expect(page.locator('#tokenhub-usage-body')).to_contain_text('test-model-1')
            old_css = subprocess.check_output(['git', 'show', 'HEAD:static/mobile.css'], cwd=ROOT, text=True)
            await page.add_style_tag(content=old_css)
            for width in (390, 360):
                await page.set_viewport_size({'width': width, 'height': 844})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal page overflow'
                await expect(page.locator('.nav-item[href="#tokenhub"]')).to_be_visible()
                assert await page.locator('.sidebar nav').evaluate('(el) => el.getBoundingClientRect().height < 90'), 'navigation must fit in one row'
            can_connect = False
            await page.locator('#tokenhub-refresh').click()
            await expect(page.locator('#tokenhub-disconnect')).not_to_be_visible()
            await page.get_by_role('button', name='API 密钥', exact=True).click()
            await page.get_by_role('link', name='管理 API 密钥').click()
            await expect(page.locator('#tokenhub-original-dialog')).to_be_visible()
            await expect(page.locator('#tokenhub-original-url')).to_have_value('https://tokenhub.ilab.beer/keys')
            await page.locator('#tokenhub-copy-url').click()
            await expect(page.locator('#tokenhub-copy-status')).to_contain_text('复制')
            await page.locator('#tokenhub-close-original').click()
            await page.get_by_role('button', name='仪表板', exact=True).click()
            await page.locator('#tokenhub-usage-details summary').click()
            await page.evaluate("document.activeElement?.blur(); window.scrollTo(0, 0)")
            await page.screenshot(path=str(ROOT / 'data/tokenhub-mobile-mock.png'), full_page=True)
            await page.set_viewport_size({'width': 1440, 'height': 1000})
            await expect(page.locator('#tokenhub-trend-chart canvas')).to_have_count(1)
            await page.evaluate("document.activeElement?.blur(); window.scrollTo(0, 0)")
            await page.screenshot(path=str(ROOT / 'data/tokenhub-desktop-mock.png'), full_page=True)
            await page.locator('.nav-item[href="#overview"]').click()
            await expect(page.locator('#tokenhub-summary')).to_contain_text('$12.50')
            await expect(page.locator('#tokenhub-summary')).to_contain_text('已用 34%')
            await expect(page.locator('#connect-button')).to_be_visible()
            await page.locator('.nav-item[href="#tokenhub"]').click()
            account_id = 8
            trend_broken = True
            await page.locator('#tokenhub-refresh').click()
            await expect(page.locator('#tokenhub-trend-summary')).not_to_contain_text('$7.00')
            await expect(page.locator('#tokenhub-trend-status')).to_contain_text('尚未取得趋势数据')
            assert all(method == 'GET' or path == '/api/tokenhub/key-group' for method, path, _ in calls), 'Only explicit group saves can write'
            assert len(group_writes) == 2
            assert not errors, errors
            await browser.close()
            print('TokenHub UI passed: disconnected state, summary, navigation, quota units, unknown data, safe text, pagination, error retention, usage precision, concurrency, deliberate group saves, mobile layouts.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
