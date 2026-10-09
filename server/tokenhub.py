"""Fixed-origin TokenHub API adapter with redacted, independently dated sections."""
import asyncio
import copy
import math
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .normalize import iso
from .tokenhub_auth import API, TokenHubAuthRequired, TokenHubError, TokenHubSession, browser_login


def number(value):
    return value if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None


def integer(value, minimum=0):
    return value if type(value) is int and value >= minimum else None


def pick(data, fields):
    if not isinstance(data, dict):
        raise TokenHubError('TokenHub 数据格式已变化，请稍后重试。')
    return {key: value for key in fields if (value := data.get(key)) is None or isinstance(value, (str, int, float, bool))}


def quota_window(data, period):
    limit_key, usage_key = period + '_limit_usd', period + '_usage_usd'
    limit, used = number(data.get(limit_key)), number(data.get(usage_key))
    state = 'unknown'
    if limit_key in data and data[limit_key] is None:
        state = 'unlimited'
    elif limit == 0:
        state = 'disabled'
    elif limit is not None and limit > 0:
        state = 'limited'
    return {'limit_usd': limit, 'used_usd': used,
            'remaining_usd': max(0, limit - used) if state == 'limited' and used is not None and used >= 0 else None,
            'state': state, 'resets_at': data.get(period + '_window_resets_at') if isinstance(data.get(period + '_window_resets_at'), str) else None}


def account(data):
    result = pick(data, ('id', 'username', 'display_name', 'balance'))
    if result.get('id') is None:
        raise TokenHubError('TokenHub 账户数据不完整。')
    result['balance'] = number(data.get('balance'))
    return result


def stats(data):
    return {key: number(data.get(key)) for key in (
        'today_tokens', 'total_tokens', 'today_requests', 'total_requests', 'today_actual_cost', 'total_actual_cost',
        'today_input_tokens', 'today_output_tokens', 'today_cache_creation_tokens', 'today_cache_read_tokens')}


def quotas(data):
    rows = data.get('platform_quotas')
    if not isinstance(rows, list):
        raise TokenHubError('平台额度格式已变化。')
    return [{**pick(row, ('platform',)), 'windows': {period: quota_window(row, period) for period in ('daily', 'weekly', 'monthly')}} for row in rows]


def subscriptions(data):
    if not isinstance(data, list):
        raise TokenHubError('订阅数据格式已变化。')
    result = []
    for row in data:
        item = pick(row, ('id', 'status', 'expires_at', 'starts_at', 'daily_usage_usd', 'weekly_usage_usd', 'monthly_usage_usd'))
        item['group'] = pick(row.get('group') or {}, ('name', 'platform', 'daily_limit_usd', 'weekly_limit_usd', 'monthly_limit_usd'))
        result.append(item)
    return result


def account_quotas(data):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise TokenHubError('账号额度格式已变化。')
    result = []
    for row in data['items']:
        item = pick(row, ('name', 'platform', 'subscription_tier', 'updated_at'))
        item['available'] = row.get('available') if isinstance(row.get('available'), bool) else None
        for name, fields, numbers in (
            ('windows', ('key', 'resets_at', 'unit'), ('utilization', 'used', 'limit')),
            ('model_quotas', ('model', 'resets_at'), ('utilization',)),
            ('credits', ('type',), ('amount', 'minimum_balance')),
        ):
            rows = row.get(name, [])
            if not isinstance(rows, list):
                raise TokenHubError('账号额度格式已变化。')
            item[name] = [{**pick(entry, fields), **{key: number(entry.get(key)) for key in numbers}} for entry in rows]
        result.append(item)
    return result


class TokenHub:
    def __init__(self, session=None, transport=None, login=None):
        self.session = session or TokenHubSession()
        self.transport = transport
        self.login = login or browser_login
        self.refresh_lock = asyncio.Lock()
        self.snapshot_lock = asyncio.Lock()
        self.sections = {}
        self.checked_at = 0
        self.connected = False
        self.status = 'disconnected'
        self.login_error = None
        self.connect_task = None
        self.loop_task = None
        self.generation = 0
        self.closed = False
        self.snapshot_task = None
        self.snapshot_generation = None
        self.snapshot_tasks = set()

    async def raw(self, method, path, token=None, *, client=None, **kwargs):
        if self.closed:
            raise TokenHubError()
        try:
            if client is None:
                async with httpx.AsyncClient(base_url=API + '/', timeout=20, follow_redirects=False, transport=self.transport) as client:
                    return await self.raw(method, path, token, client=client, **kwargs)
            request = client.build_request(method, path.lstrip('/'), headers={'Authorization': 'Bearer ' + token} if token else {}, **kwargs)
            # A snapshot shares connections, never upstream session cookies.
            request.headers.pop('cookie', None)
            response = await client.send(request)
            if response.status_code == 401:
                raise TokenHubAuthRequired()
            if response.status_code >= 400:
                raise TokenHubError()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get('code') != 0 or 'data' not in payload:
                raise TokenHubError('TokenHub 返回了无法识别的数据。')
            return payload['data']
        except (httpx.HTTPError, ValueError):
            raise TokenHubError() from None

    async def renew(self, failed_token):
        async with self.refresh_lock:
            current = self.session.load()
            if current['access_token'] != failed_token:
                return current
            generation = self.generation
            data = await self.raw('POST', '/auth/refresh', json={'refresh_token': current['refresh_token']})
            if not isinstance(data, dict) or number(data.get('expires_in')) is None or data['expires_in'] <= 0:
                raise TokenHubAuthRequired()
            value = {'access_token': data.get('access_token'), 'refresh_token': data.get('refresh_token'), 'expires_at': time.time() + data['expires_in']}
            if generation != self.generation:
                raise TokenHubAuthRequired()
            self.session.save(value)
            return value

    async def request(self, path, params=None, *, method='GET', body=None, client=None):
        generation = self.generation
        current = self.session.load()
        if current['expires_at'] <= time.time() + 30:
            current = await self.renew(current['access_token'])
        try:
            result = await self.raw(method, path, current['access_token'], client=client, params=params, **({'json': body} if body is not None else {}))
        except TokenHubAuthRequired:
            if method == 'PUT':
                raise
            current = await self.renew(current['access_token'])
            result = await self.raw(method, path, current['access_token'], client=client, params=params, **({'json': body} if body is not None else {}))
        if generation != self.generation:
            raise TokenHubAuthRequired()
        return result

    def stale(self, name, message):
        previous = self.sections.get(name, {})
        self.sections[name] = {**previous, 'data': previous.get('data'), 'updated_at': previous.get('updated_at'),
                               'status': 'stale' if previous.get('updated_at') else 'error', 'error': message}

    async def read_section(self, name, path, normalize, *, client=None):
        try:
            data = normalize(await self.request(path, client=client))
            self.sections[name] = {'data': data, 'updated_at': iso(time.time()), 'status': 'ok', 'error': None}
        except TokenHubAuthRequired:
            self.stale(name, '登录已失效，数据未更新。')
            raise
        except Exception:
            self.stale(name, '读取失败，数据未更新。')

    def view(self):
        return {'status': self.status, 'connected': self.connected,
                'connecting': bool(self.connect_task and not self.connect_task.done()), 'login_error': self.login_error,
                'sections': copy.deepcopy(self.sections), 'interval_seconds': 60}

    async def snapshot(self, force=False):
        if self.closed:
            raise TokenHubError()
        refreshing = self.snapshot_task is not None and not self.snapshot_task.done() and self.snapshot_generation == self.generation
        if not refreshing and not force and self.checked_at and time.monotonic() - self.checked_at < 60:
            return self.view()
        if not refreshing:
            self.snapshot_generation = self.generation
            self.snapshot_task = asyncio.create_task(self.refresh_snapshot(force, self.generation))
            self.snapshot_tasks.add(self.snapshot_task)
            self.snapshot_task.add_done_callback(self.snapshot_tasks.discard)
            self.snapshot_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        # An HTTP caller leaving must not cancel a refresh shared with other callers.
        await asyncio.shield(self.snapshot_task)
        return self.view()

    async def refresh_snapshot(self, force, generation):
        async with self.snapshot_lock:
            if generation != self.generation:
                return
            if not force and self.checked_at and time.monotonic() - self.checked_at < 60:
                return
            try:
                self.session.load()
            except TokenHubAuthRequired:
                self.status, self.connected = 'disconnected', False
                return
            try:
                async with httpx.AsyncClient(base_url=API + '/', timeout=20, follow_redirects=False, transport=self.transport) as client:
                    old_id = (self.sections.get('account', {}).get('data') or {}).get('id')
                    await self.read_section('account', '/auth/me', account, client=client)
                    if self.sections['account']['status'] != 'ok':
                        for name in self.sections:
                            self.stale(name, '账户验证失败，数据未更新。')
                        self.status = 'error'
                        return
                    new_id = self.sections['account']['data']['id']
                    if old_id is not None and new_id != old_id:
                        self.sections = {'account': self.sections['account']}
                    result = await asyncio.gather(
                        self.read_section('stats', '/usage/dashboard/stats', stats, client=client),
                        self.read_section('account_quotas', '/account-quotas', account_quotas, client=client),
                        self.read_section('quotas', '/user/platform-quotas', quotas, client=client),
                        self.read_section('subscriptions', '/subscriptions', subscriptions, client=client), return_exceptions=True)
                if any(isinstance(item, TokenHubAuthRequired) for item in result):
                    raise TokenHubAuthRequired()
                self.connected = True
                self.status = 'ok' if all(item['status'] == 'ok' for item in self.sections.values()) else 'partial'
            except TokenHubAuthRequired:
                self.status, self.connected = 'auth_required', False
                for name in self.sections:
                    self.stale(name, '登录已失效，数据未更新。')
            finally:
                self.checked_at = time.monotonic()

    async def trend(self, days=7):
        if isinstance(days, bool) or days not in (7, 30):
            raise TokenHubError('请选择 7 天或 30 天。', 'invalid_days', 400)
        end = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        start = end - timedelta(days=days - 1)
        data = await self.request('/usage/dashboard/trend', {
            'start_date': start.isoformat(), 'end_date': end.isoformat(),
            'granularity': 'day', 'timezone': 'Asia/Shanghai'})
        if not isinstance(data, dict) or not isinstance(data.get('trend'), list):
            raise TokenHubError('趋势数据格式已变化。')
        rows, seen = [], set()
        for row in data['trend']:
            item = pick(row, ('date',))
            try:
                day = date.fromisoformat(item['date'])
                if not start <= day <= end or day in seen:
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise TokenHubError('趋势日期格式已变化。') from None
            seen.add(day)
            item.update({key: number(row.get(key)) for key in (
                'requests', 'input_tokens', 'output_tokens', 'cache_creation_tokens',
                'cache_read_tokens', 'total_tokens', 'actual_cost')})
            rows.append(item)
        by_day = {row['date']: row for row in rows}
        # Successful aggregates omit unused days; explicit unknown costs stay unknown.
        timeline = [(start + timedelta(days=i)).isoformat() for i in range(days)]
        return {'days': days, 'start_date': start.isoformat(), 'end_date': end.isoformat(),
                'trend': [by_day.get(day, {'date': day, 'actual_cost': 0}) for day in timeline],
                'updated_at': iso(time.time())}

    async def hourly_trend(self):
        zone = ZoneInfo('Asia/Shanghai')
        end = datetime.now(zone).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=23)
        data = await self.request('/usage/dashboard/trend', {
            'start_date': start.date().isoformat(), 'end_date': end.date().isoformat(),
            'granularity': 'hour', 'timezone': 'Asia/Shanghai'})
        if not isinstance(data, dict) or not isinstance(data.get('trend'), list):
            raise TokenHubError('小时费用数据格式已变化。')
        rows, seen = [], set()
        for row in data['trend']:
            value = pick(row, ('date',)).get('date')
            try:
                hour = datetime.strptime(value, '%Y-%m-%d %H:%M').replace(tzinfo=zone)
                if hour.minute or hour.strftime('%Y-%m-%d %H:%M') != value or hour in seen:
                    raise ValueError()
            except (ValueError, TypeError):
                raise TokenHubError('小时费用时间格式已变化。') from None
            seen.add(hour)
            # The upstream accepts whole calendar dates; keep only the 24 hourly buckets.
            if start <= hour <= end:
                rows.append({'date': hour.isoformat(), 'actual_cost': number(row.get('actual_cost'))})
        by_hour = {row['date']: row for row in rows}
        # A successful aggregate omits hours with no usage. An explicit unknown cost stays unknown.
        timeline = [(start + timedelta(hours=i)).isoformat() for i in range(24)]
        return {'hours': 24, 'timezone': 'Asia/Shanghai', 'start_hour': start.isoformat(),
                'end_hour': end.isoformat(), 'trend': [by_hour.get(hour, {'date': hour, 'actual_cost': 0}) for hour in timeline],
                'updated_at': iso(time.time())}

    async def keys(self, page=1):
        generation = self.generation
        data = await self.request('/keys', {'page': page, 'page_size': 20})
        result = self.page(data, page)
        for row in data['items']:
            item = pick(row, ('id', 'name', 'status', 'quota', 'quota_used', 'expires_at', 'last_used_at'))
            item['group_name'] = pick(row.get('group') or {}, ('name',)).get('name')
            item['group_id'] = integer(row.get('group_id'), 1)
            item['current_concurrency'] = integer(row.get('current_concurrency'))
            item['today_actual_cost'] = item['last_30_days_actual_cost'] = None
            result['items'].append(item)
        ids = [item['id'] for item in result['items'] if integer(item.get('id'), 1) is not None]
        if ids:
            try:
                usage = await self.request('/usage/dashboard/api-keys-usage', method='POST', body={'api_key_ids': ids})
                if not isinstance(usage, dict) or not isinstance(usage.get('stats'), dict):
                    raise TokenHubError()
                for item in result['items']:
                    entry = usage['stats'].get(str(item['id']), {})
                    if not isinstance(entry, dict):
                        continue
                    item['today_actual_cost'] = number(entry.get('today_actual_cost'))
                    item['last_30_days_actual_cost'] = number(entry.get('total_actual_cost'))
            except TokenHubAuthRequired:
                raise
            except TokenHubError:
                result['usage_error'] = '用量暂未获取，请刷新重试。'
        if generation != self.generation:
            raise TokenHubAuthRequired()
        return result

    async def copy_key(self, key_id, account_id):
        if integer(key_id, 1) is None or integer(account_id, 1) is None:
            raise TokenHubError('密钥或账户编号无效。', 'invalid_key', 400)
        generation = self.generation
        owner, key = await asyncio.gather(self.request('/auth/me'), self.request(f'/keys/{key_id}'))
        if generation != self.generation:
            raise TokenHubAuthRequired()
        if (not isinstance(owner, dict) or integer(owner.get('id'), 1) != account_id
                or not isinstance(key, dict) or integer(key.get('id'), 1) != key_id
                or integer(key.get('user_id'), 1) != account_id):
            raise TokenHubError('账户或密钥已变化，请刷新后重试。', 'account_changed', 409)
        value = key.get('key')
        if (not isinstance(value, str) or not 1 <= len(value) <= 4096
                or any(char.isspace() for char in value) or any(mask in value for mask in ('*', '…', '•', '...'))):
            raise TokenHubError('原站未返回完整密钥，请前往原站复制。', 'key_unavailable', 503)
        # Only this explicit action returns a secret; never add it to snapshots or lists.
        return {'key': value}

    async def key_groups(self, key_id):
        if integer(key_id, 1) is None:
            raise TokenHubError('密钥编号无效。', 'invalid_key', 400)
        generation = self.generation
        owner, key, groups = await asyncio.gather(
            self.request('/auth/me'), self.request(f'/keys/{key_id}'), self.request('/groups/available'))
        if generation != self.generation:
            raise TokenHubAuthRequired()
        account_id = integer(owner.get('id'), 1) if isinstance(owner, dict) else None
        if account_id is None or not isinstance(key, dict) or key.get('id') != key_id or key.get('user_id') != account_id:
            raise TokenHubError('账户或密钥已变化，请刷新后重试。', 'account_changed', 409)
        if not isinstance(groups, list):
            raise TokenHubError('可用分组格式已变化。')
        return {'account_id': account_id, 'key': {**pick(key, ('id', 'name')), 'group_id': integer(key.get('group_id'), 1)},
                'items': [pick(group, ('id', 'name', 'platform')) for group in groups
                          if isinstance(group, dict) and integer(group.get('id'), 1) is not None and group.get('status') == 'active']}

    async def update_key_group(self, key_id, group_id, *, expected_group_id, account_id):
        if integer(group_id, 1) is None or integer(account_id, 1) is None or (expected_group_id is not None and integer(expected_group_id, 1) is None):
            raise TokenHubError('分组参数无效。', 'invalid_group', 400)
        # Account changes and concurrent local edits cannot interleave with validation/write.
        async with self.snapshot_lock:
            options = await self.key_groups(key_id)
            if options['account_id'] != account_id or options['key']['group_id'] != expected_group_id:
                raise TokenHubError('账户或分组已变化，请关闭窗口并重新选择。', 'group_changed', 409)
            if group_id not in {group['id'] for group in options['items']}:
                raise TokenHubError('该分组已不可用，请重新选择。', 'invalid_group', 409)
            if group_id == expected_group_id:
                return {'id': key_id, 'group_id': group_id}
            try:
                result = await self.request(f'/keys/{key_id}', method='PUT', body={'group_id': group_id})
            except TokenHubError:
                raise TokenHubError('未能确认保存结果，请关闭窗口并刷新密钥列表后再操作。', 'save_unconfirmed', 503) from None
            if not isinstance(result, dict) or result.get('id') != key_id or result.get('group_id') != group_id:
                raise TokenHubError('未能确认保存结果，请刷新密钥列表。', 'save_unconfirmed', 503)
            return {'id': key_id, 'group_id': group_id}

    async def usage(self, start_date, end_date, page=1):
        try:
            start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
            if start > end or (end - start).days > 366:
                raise ValueError()
        except (ValueError, TypeError):
            raise TokenHubError('请选择有效日期范围，最长 366 天。', 'invalid_dates', 400) from None
        data = await self.request('/usage', {'start_date': start_date, 'end_date': end_date, 'page': page, 'page_size': 20, 'timezone': 'Asia/Shanghai'})
        result = self.page(data, page)
        for row in data['items']:
            item = pick(row, ('id', 'created_at', 'model', 'input_tokens', 'output_tokens', 'cache_creation_tokens', 'cache_read_tokens', 'actual_cost', 'total_cost', 'duration_ms'))
            item['key_name'] = pick(row.get('api_key') or {}, ('name',)).get('name')
            result['items'].append(item)
        return result

    @staticmethod
    def page(data, page):
        if not isinstance(data, dict) or not isinstance(data.get('items'), list) or number(data.get('total')) is None:
            raise TokenHubError('列表数据格式已变化。')
        return {'items': [], 'total': data['total'], 'pages': max(1, math.ceil(data['total'] / 20)), 'page': page, 'updated_at': iso(time.time())}

    def connect(self):
        if not self.connect_task or self.connect_task.done():
            self.login_error = None
            self.connect_task = asyncio.create_task(self.finish_login())
        return {'connecting': True, 'message': '请在本机弹出的浏览器中完成 GitHub 登录；完成后窗口会自动关闭。'}

    async def finish_login(self):
        try:
            value = await self.login()
            async with self.snapshot_lock, self.refresh_lock:
                self.generation += 1
                self.session.save(value)
                self.sections = {}
                self.checked_at = 0
            await self.snapshot(force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.login_error = '登录未完成。请重新连接，并在 5 分钟内完成 GitHub 登录。'

    async def disconnect(self):
        if self.connect_task and not self.connect_task.done():
            self.connect_task.cancel()
            await asyncio.gather(self.connect_task, return_exceptions=True)
        async with self.snapshot_lock, self.refresh_lock:
            self.generation += 1
            self.session.clear()
            self.sections = {}
            self.status, self.connected, self.checked_at, self.login_error = 'disconnected', False, 0, None
        return self.view()

    async def loop(self):
        while True:
            try:
                await self.snapshot()
            except Exception:
                self.status = 'error'
            await asyncio.sleep(60)

    async def close(self):
        self.closed = True
        tasks = [task for task in (self.connect_task, self.loop_task, *self.snapshot_tasks) if task and not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
