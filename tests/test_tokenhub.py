import asyncio
import importlib.util
import json
import os
import time

import httpx
import pytest


def modules():
    assert importlib.util.find_spec('server.tokenhub') is not None, 'TokenHub collector is not implemented'
    from server.tokenhub import TokenHub, quota_window
    from server.tokenhub_auth import TokenHubSession
    return TokenHub, quota_window, TokenHubSession


def setup(tmp_path, handler, logged_in=True):
    Hub, _, Session = modules()
    session = Session(tmp_path / 'tokenhub' / 'session.json')
    if logged_in:
        session.save({'access_token': 'access-secret', 'refresh_token': 'refresh-secret', 'expires_at': time.time()+600})
    return Hub(session, transport=httpx.MockTransport(handler)), session


def ok(data):
    return httpx.Response(200, json={'code': 0, 'data': data})


def responses(request):
    path = request.url.path
    if path.endswith('/auth/me'):
        return ok({'id': 7, 'username': 'charles', 'balance': 12.5, 'access_token': 'DO-NOT-LEAK'})
    if path.endswith('/dashboard/stats'):
        return ok({'today_tokens': 30, 'total_tokens': 130, 'today_actual_cost': .2, 'unknown_secret': 'DO-NOT-LEAK'})
    if path.endswith('/platform-quotas'):
        return ok({'platform_quotas': [{'platform': 'openai', 'daily_limit_usd': 10, 'daily_usage_usd': 2, 'weekly_limit_usd': None}]})
    if path.endswith('/account-quotas'):
        return ok({'items': [{'name': 'account', 'platform': 'openai', 'available': True, 'windows': [{'key': 'seven_day', 'utilization': 34}], 'model_quotas': [], 'credits': []}]})
    if path.endswith('/subscriptions'):
        return ok([])
    raise AssertionError(path)


def test_quota_units_missing_and_disabled():
    _, quota, _ = modules()
    assert quota({'daily_limit_usd': 10, 'daily_usage_usd': 2}, 'daily')['remaining_usd'] == 8
    assert quota({'daily_limit_usd': 10}, 'daily')['remaining_usd'] is None
    assert quota({'daily_limit_usd': None}, 'daily')['state'] == 'unlimited'
    assert quota({}, 'daily')['state'] == 'unknown'
    assert quota({'daily_limit_usd': 0, 'daily_usage_usd': 0}, 'daily')['state'] == 'disabled'
    assert quota({'daily_limit_usd': 2, 'daily_usage_usd': 3}, 'daily')['remaining_usd'] == 0
    assert quota({'daily_limit_usd': True, 'daily_usage_usd': 0}, 'daily')['state'] == 'unknown'


@pytest.mark.asyncio
async def test_unconnected_does_not_contact_upstream(tmp_path):
    def fail(request):
        raise AssertionError('No upstream request without a session')
    hub, _ = setup(tmp_path, fail, False)
    data = await hub.snapshot()
    assert data['status'] == 'disconnected' and not data['connected']
    assert data['sections'] == {}
    await hub.close()


@pytest.mark.asyncio
async def test_snapshot_has_only_public_fields_and_per_section_freshness(tmp_path):
    hub, session = setup(tmp_path, responses)
    data = await hub.snapshot()
    assert data['status'] == 'ok'
    assert data['sections']['account']['data']['balance'] == 12.5
    assert data['sections']['quotas']['data'][0]['windows']['daily']['remaining_usd'] == 8
    assert data['sections']['stats']['updated_at']
    assert 'DO-NOT-LEAK' not in json.dumps(data) and 'access-secret' not in json.dumps(data)
    assert os.stat(session.path).st_mode & 0o777 == 0o600
    await hub.close()


@pytest.mark.asyncio
async def test_concurrent_401_rotates_refresh_once(tmp_path):
    refreshed = 0
    async def handler(request):
        nonlocal refreshed
        if request.url.path.endswith('/auth/refresh'):
            refreshed += 1
            assert json.loads(request.content)['refresh_token'] == 'refresh-secret'
            await asyncio.sleep(.01)
            return ok({'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 1200})
        if request.headers['Authorization'] != 'Bearer new-access':
            return httpx.Response(401, json={'message': 'PRIVATE upstream error'})
        return responses(request)
    hub, session = setup(tmp_path, handler)
    await asyncio.gather(hub.request('/auth/me'), hub.request('/usage/dashboard/stats'))
    assert refreshed == 1
    assert session.load()['refresh_token'] == 'new-refresh'
    await hub.close()


@pytest.mark.asyncio
async def test_partial_failure_preserves_old_value_and_timestamp(tmp_path):
    broken = False
    def handler(request):
        if broken and request.url.path.endswith('/platform-quotas'):
            return httpx.Response(503, text='SECRET')
        return responses(request)
    hub, _ = setup(tmp_path, handler)
    before = await hub.snapshot()
    broken = True
    after = await hub.snapshot(force=True)
    assert after['status'] == 'partial'
    assert after['sections']['quotas']['data'] == before['sections']['quotas']['data']
    assert after['sections']['quotas']['updated_at'] == before['sections']['quotas']['updated_at']
    assert after['sections']['quotas']['status'] == 'stale'
    assert 'SECRET' not in json.dumps(after)
    await hub.close()


@pytest.mark.asyncio
async def test_auth_failure_is_not_reported_as_empty_account(tmp_path):
    broken = False
    def handler(request):
        if broken:
            return httpx.Response(401, text='secret-refresh-token')
        return responses(request)
    hub, _ = setup(tmp_path, handler)
    before = await hub.snapshot()
    broken = True
    after = await hub.snapshot(force=True)
    assert after['status'] == 'auth_required' and not after['connected']
    assert after['sections']['account']['data'] == before['sections']['account']['data']
    assert after['sections']['account']['status'] == 'stale'
    assert 'secret-refresh-token' not in json.dumps(after)
    await hub.close()


@pytest.mark.asyncio
async def test_keys_and_usage_never_return_keys_or_prompts(tmp_path):
    def handler(request):
        if request.url.path.endswith('/api-keys-usage'):
            return ok({'stats': {}})
        assert request.url.params['page_size'] == '20'
        if request.url.path.endswith('/keys'):
            return ok({'items': [{'id': 1, 'name': 'work', 'key': 'sk-SECRET', 'status': 'active', 'group': {'name': 'OpenAI', 'secret': 'SECRET'}, 'quota': 10, 'quota_used': 2}], 'total': 1, 'pages': 1})
        assert request.url.params['timezone'] == 'Asia/Shanghai'
        return ok({'items': [{'id': 3, 'model': 'test', 'input_tokens': 10, 'api_key': {'name': 'work', 'key': 'SECRET'}, 'request_body': 'SECRET', 'actual_cost': .1}], 'total': 1, 'pages': 1})
    hub, _ = setup(tmp_path, handler)
    keys = await hub.keys(page=1)
    usage = await hub.usage(start_date='2026-10-01', end_date='2026-10-06', page=1)
    assert keys['items'][0]['name'] == 'work'
    assert usage['items'][0]['input_tokens'] == 10
    assert 'SECRET' not in json.dumps([keys, usage])
    await hub.close()


@pytest.mark.asyncio
async def test_disconnect_does_not_logout_github_or_remote_sessions(tmp_path):
    def handler(request):
        raise AssertionError('Local disconnect must not revoke remote sessions')
    hub, session = setup(tmp_path, handler)
    await hub.disconnect()
    assert not session.path.exists()
    assert (await hub.snapshot())['sections'] == {}
    await hub.close()


def test_account_quotas_preserve_unknown_units_and_redact():
    from server.tokenhub import account_quotas
    rows = account_quotas({'items': [{'name': 'work', 'available': True, 'secret': 'SECRET',
        'windows': [{'key': 'five_hour', 'utilization': 0, 'used': 10, 'limit': 100, 'unit': 'tokens', 'secret': 'SECRET'},
                    {'key': 'seven_day', 'utilization': None}, {'key': 'daily', 'utilization': True}],
        'model_quotas': [{'model': 'm', 'utilization': 90}], 'credits': [{'type': 'USD', 'amount': 4}]}]})
    assert rows[0]['windows'][0]['utilization'] == 0
    assert rows[0]['windows'][0]['unit'] == 'tokens'
    assert rows[0]['windows'][1]['utilization'] is None
    assert rows[0]['windows'][2]['utilization'] is None
    assert 'SECRET' not in json.dumps(rows)
    with pytest.raises(Exception):
        account_quotas({'items': [{'windows': 'wrong'}]})


@pytest.mark.asyncio
async def test_account_quota_error_keeps_snapshot(tmp_path):
    broken = False
    def handler(request):
        if broken and request.url.path.endswith('/account-quotas'):
            return httpx.Response(503)
        return responses(request)
    hub, _ = setup(tmp_path, handler)
    before = (await hub.snapshot())['sections']['account_quotas']
    broken = True
    after = (await hub.snapshot(force=True))['sections']['account_quotas']
    assert after['data'] == before['data'] and after['updated_at'] == before['updated_at']
    assert after['status'] == 'stale'


@pytest.mark.asyncio
async def test_trend_days_timezone_unknown_and_redaction(tmp_path):
    from datetime import date
    seen = []
    def handler(request):
        assert request.url.path.endswith('/usage/dashboard/trend')
        params = request.url.params
        assert params['timezone'] == 'Asia/Shanghai' and params['granularity'] == 'day'
        seen.append((date.fromisoformat(params['end_date']) - date.fromisoformat(params['start_date'])).days + 1)
        return ok({'trend': [{'date': params['start_date'], 'total_tokens': 123, 'input_tokens': None, 'output_tokens': True, 'secret': 'SECRET'}]})
    hub, _ = setup(tmp_path, handler)
    for days in (7, 30):
        data = await hub.trend(days)
        assert data['days'] == days and data['updated_at']
        assert data['trend'][0]['total_tokens'] == 123
        assert data['trend'][0]['input_tokens'] is None
        assert data['trend'][0]['output_tokens'] is None
        assert 'SECRET' not in json.dumps(data)
    assert seen == [7, 30]
    with pytest.raises(Exception):
        await hub.trend(365)
    assert seen == [7, 30]


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{'trend': 'wrong'}, {'trend': [{'date': 'bad'}]}, {'trend': [{'date': '2000-01-01'}]}])
async def test_trend_rejects_malformed_or_out_of_range_data(tmp_path, payload):
    from server.tokenhub_auth import TokenHubError
    hub, _ = setup(tmp_path, lambda request: ok(payload))
    with pytest.raises(TokenHubError):
        await hub.trend(7)


@pytest.mark.asyncio
async def test_inflight_trend_cannot_return_after_disconnect(tmp_path):
    from server.tokenhub_auth import TokenHubAuthRequired
    started, resume = asyncio.Event(), asyncio.Event()
    async def handler(request):
        started.set()
        await resume.wait()
        return ok({'trend': [{'date': request.url.params['start_date'], 'total_tokens': 100}]})
    hub, _ = setup(tmp_path, handler)
    task = asyncio.create_task(hub.trend())
    await started.wait()
    await hub.disconnect()
    resume.set()
    with pytest.raises(TokenHubAuthRequired):
        await task
    assert hub.sections == {}


@pytest.mark.asyncio
async def test_key_usage_concurrency_and_group_are_redacted(tmp_path):
    def handler(request):
        if request.url.path.endswith('/keys'):
            return ok({'total': 3, 'items': [
                {'id': 1, 'name': 'work', 'group_id': 2, 'current_concurrency': 0, 'key': 'SECRET'},
                {'id': 2, 'name': 'busy', 'current_concurrency': 3},
                {'id': 3, 'current_concurrency': True}]})
        assert request.method == 'POST'
        assert request.url.path.endswith('/usage/dashboard/api-keys-usage')
        assert json.loads(request.content) == {'api_key_ids': [1, 2, 3]}
        return ok({'stats': {'1': {'today_actual_cost': 0, 'total_actual_cost': 11.3996, 'secret': 'SECRET'},
                             '2': {'today_actual_cost': 36.3408, 'total_actual_cost': 972.3599},
                             '3': {'today_actual_cost': True}}})
    hub, _ = setup(tmp_path, handler)
    result = await hub.keys()
    one, two, three = result['items']
    assert one['current_concurrency'] == 0 and one['group_id'] == 2
    assert one['today_actual_cost'] == 0 and one['last_30_days_actual_cost'] == 11.3996
    assert two['current_concurrency'] == 3 and two['last_30_days_actual_cost'] == 972.3599
    assert three['current_concurrency'] is None and three['today_actual_cost'] is None
    assert three['last_30_days_actual_cost'] is None
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.asyncio
async def test_key_usage_failure_keeps_keys_and_marks_unknown(tmp_path):
    def handler(request):
        if request.url.path.endswith('/keys'):
            return ok({'total': 1, 'items': [{'id': 1, 'current_concurrency': -1}]})
        return httpx.Response(503, text='SECRET')
    hub, _ = setup(tmp_path, handler)
    result = await hub.keys()
    assert result['items'][0]['today_actual_cost'] is None
    assert result['items'][0]['current_concurrency'] is None
    assert result['usage_error'] and 'SECRET' not in json.dumps(result)


@pytest.mark.asyncio
async def test_key_group_options_and_update_only_group_id(tmp_path):
    writes = []
    def handler(request):
        path = request.url.path
        if path.endswith('/auth/me'): return ok({'id': 7, 'token': 'SECRET'})
        if path.endswith('/groups/available'): return ok([{'id': 2, 'name': 'Old', 'platform': 'openai', 'status': 'active'}, {'id': 3, 'name': 'New', 'platform': 'anthropic', 'rate_multiplier': 2, 'status': 'active', 'secret': 'SECRET'}])
        assert path.endswith('/keys/1')
        if request.method == 'GET': return ok({'id': 1, 'name': 'work', 'user_id': 7, 'group_id': 2, 'key': 'SECRET'})
        assert request.method == 'PUT'
        writes.append(json.loads(request.content))
        return ok({'id': 1, 'name': 'work', 'group_id': 3, 'key': 'SECRET'})
    hub, _ = setup(tmp_path, handler)
    options = await hub.key_groups(1)
    assert options['account_id'] == 7 and options['key']['group_id'] == 2
    assert options['items'][1]['name'] == 'New' and not writes
    result = await hub.update_key_group(1, 3, expected_group_id=2, account_id=7)
    assert result['group_id'] == 3
    assert writes == [{'group_id': 3}]
    assert 'SECRET' not in json.dumps([options, result])


@pytest.mark.asyncio
@pytest.mark.parametrize('account_id,previous,group_id', [(8, 2, 3), (7, 9, 3), (7, 2, 99)])
async def test_key_group_rejects_stale_identity_or_unavailable_group(tmp_path, account_id, previous, group_id):
    def handler(request):
        assert request.method == 'GET', 'Invalid selection must never mutate a key'
        if request.url.path.endswith('/auth/me'): return ok({'id': 7})
        if request.url.path.endswith('/groups/available'): return ok([{'id': 3, 'name': 'New', 'status': 'active'}])
        return ok({'id': 1, 'user_id': 7, 'name': 'work', 'group_id': 2})
    hub, _ = setup(tmp_path, handler)
    from server.tokenhub_auth import TokenHubError
    with pytest.raises(TokenHubError):
        await hub.update_key_group(1, group_id, expected_group_id=previous, account_id=account_id)


@pytest.mark.asyncio
async def test_group_update_timeout_does_not_retry_write(tmp_path):
    writes = []
    def handler(request):
        if request.url.path.endswith('/auth/me'): return ok({'id': 7})
        if request.url.path.endswith('/groups/available'): return ok([{'id': 3, 'name': 'New', 'status': 'active'}])
        if request.method == 'GET': return ok({'id': 1, 'user_id': 7, 'group_id': 2})
        writes.append(request)
        raise httpx.ReadTimeout('SECRET', request=request)
    hub, _ = setup(tmp_path, handler)
    from server.tokenhub_auth import TokenHubError
    with pytest.raises(TokenHubError) as error:
        await hub.update_key_group(1, 3, expected_group_id=2, account_id=7)
    assert error.value.code == 'save_unconfirmed'
    assert 'SECRET' not in str(error.value)
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_key_group_rejects_account_change_during_read(tmp_path):
    def handler(request):
        assert request.method == 'GET'
        if request.url.path.endswith('/auth/me'): return ok({'id': 7})
        if request.url.path.endswith('/groups/available'):
            hub.generation += 1
            return ok([{'id': 3, 'name': 'New', 'status': 'active'}])
        return ok({'id': 1, 'user_id': 7, 'group_id': 2})
    hub, _ = setup(tmp_path, handler)
    from server.tokenhub_auth import TokenHubAuthRequired
    with pytest.raises(TokenHubAuthRequired):
        await hub.update_key_group(1, 3, expected_group_id=2, account_id=7)
