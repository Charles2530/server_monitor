"""Hourly billing distinguishes unused hours from unknown cost fields."""
import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from server import tokenhub
from server.tokenhub_auth import TokenHubAuthRequired, TokenHubError, TokenHubSession


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 6, 1, 35, tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(tz)


def hub_for(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(tokenhub, 'datetime', Clock)
    session = TokenHubSession(tmp_path / 'session.json')
    session.save({'access_token': 'test', 'refresh_token': 'test', 'expires_at': time.time() + 600})
    def handler(request):
        assert request.method == 'GET'
        assert request.url.path.endswith('/usage/dashboard/trend')
        assert dict(request.url.params) == {'start_date': '2026-10-05', 'end_date': '2026-10-06',
                                            'granularity': 'hour', 'timezone': 'Asia/Shanghai'}
        return httpx.Response(200, json={'code': 0, 'data': {'granularity': 'hour', 'trend': rows}})
    return tokenhub.TokenHub(session, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_hourly_window_filters_sorts_and_preserves_zero_unknown(tmp_path, monkeypatch):
    hub = hub_for(tmp_path, monkeypatch, [
        {'date': '2026-10-06 01:00', 'actual_cost': 1.25, 'key': 'SECRET'},
        {'date': '2026-10-05 00:00', 'actual_cost': 99},
        {'date': '2026-10-05 02:00', 'actual_cost': 0},
        {'date': '2026-10-05 03:00', 'actual_cost': True},
        {'date': '2026-10-06 02:00', 'actual_cost': 99}])
    result = await hub.hourly_trend()
    assert result['hours'] == 24 and result['timezone'] == 'Asia/Shanghai'
    assert result['start_hour'] == '2026-10-05T02:00:00+08:00'
    assert result['end_hour'] == '2026-10-06T01:00:00+08:00'
    assert len(result['trend']) == 24
    assert [row['actual_cost'] for row in result['trend'][:3]] == [0, None, 0]
    assert result['trend'][-1]['actual_cost'] == 1.25
    assert all(set(row) == {'date', 'actual_cost'} for row in result['trend'])
    assert 'SECRET' not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('rows', [
    [{'date': '2026-10-05', 'actual_cost': 1}],
    [{'date': '2026-10-05 03:30', 'actual_cost': 1}],
    [{'date': 'bad'}],
    [{'date': '2026-10-05 03:00'}, {'date': '2026-10-05 03:00'}],
])
async def test_hourly_rejects_bad_timestamps_and_duplicates(tmp_path, monkeypatch, rows):
    with pytest.raises(TokenHubError):
        await hub_for(tmp_path, monkeypatch, rows).hourly_trend()


@pytest.mark.asyncio
async def test_hourly_no_usage_is_zero_for_each_hour(tmp_path, monkeypatch):
    rows = (await hub_for(tmp_path, monkeypatch, []).hourly_trend())['trend']
    assert len(rows) == 24 and all(row['actual_cost'] == 0 for row in rows)


@pytest.mark.asyncio
async def test_hourly_disconnect_discards_inflight_result(tmp_path, monkeypatch):
    hub = hub_for(tmp_path, monkeypatch, [])
    started, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={'code': 0, 'data': {'trend': []}})
    hub.transport = httpx.MockTransport(handler)
    task = asyncio.create_task(hub.hourly_trend())
    await started.wait()
    await hub.disconnect()
    release.set()
    with pytest.raises(TokenHubAuthRequired):
        await task
