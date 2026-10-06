"""Dispatch and lifecycle checks using local HTTP responses and temporary sessions."""
import asyncio
import base64
import json
import threading
import time

import httpx
import pytest

from server.acp_actions import ActionError
from server.task_logs import TaskLogs
from server.tokenhub import TokenHub
from server.tokenhub_auth import TokenHubSession


def ok(data, **kwargs):
    return httpx.Response(200, json={'code': 0, 'data': data}, **kwargs)


def dashboard(request):
    path = request.url.path
    if path.endswith('/auth/me'):
        return ok({'id': 7})
    if path.endswith('/dashboard/stats'):
        return ok({'today_tokens': 10})
    if path.endswith('/platform-quotas'):
        return ok({'platform_quotas': []})
    if path.endswith('/account-quotas'):
        return ok({'items': []})
    if path.endswith('/subscriptions'):
        return ok([])
    raise AssertionError(path)


def hub_for(tmp_path, handler):
    session = TokenHubSession(tmp_path / 'session.json')
    session.save({'access_token': 'first-token', 'refresh_token': 'first-refresh', 'expires_at': time.time() + 600})
    return TokenHub(session, transport=httpx.MockTransport(handler)), session


@pytest.mark.asyncio
async def test_overlapping_forced_snapshots_share_one_refresh_but_later_force_runs(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/auth/me'):
            started.set()
            await release.wait()
        return dashboard(request)
    hub, _ = hub_for(tmp_path, handler)
    first = asyncio.create_task(hub.snapshot(force=True))
    others = []
    try:
        await asyncio.wait_for(started.wait(), 1)
        others = [asyncio.create_task(hub.snapshot(force=True)) for _ in range(2)]
        await asyncio.sleep(0)
        release.set()
        result = await asyncio.gather(first, *others)
        assert len(calls) == 5
        assert all(item['status'] == 'ok' for item in result)
        result[0]['sections'].clear()
        assert result[1]['sections'] and hub.sections
        await hub.snapshot(force=True)
        assert len(calls) == 10
    finally:
        release.set()
        await asyncio.gather(first, *others, return_exceptions=True)
        await hub.close()


@pytest.mark.asyncio
async def test_cancelling_first_snapshot_caller_keeps_shared_refresh(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    calls, cancelled = [], []
    async def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/auth/me'):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return dashboard(request)
    hub, _ = hub_for(tmp_path, handler)
    first = asyncio.create_task(hub.snapshot(force=True))
    second = None
    try:
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(hub.snapshot(force=True))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert cancelled == []
        release.set()
        assert (await second)['status'] == 'ok'
        assert len(calls) == 5
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await hub.close()


@pytest.mark.asyncio
async def test_fresh_cached_read_does_not_swallow_a_concurrent_force(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return dashboard(request)
    hub, _ = hub_for(tmp_path, handler)
    try:
        await hub.snapshot()
        await asyncio.gather(hub.snapshot(), hub.snapshot(force=True))
        assert len(calls) == 10
    finally:
        await hub.close()


@pytest.mark.asyncio
async def test_close_cancels_abandoned_shared_refresh_before_client_closes(tmp_path, monkeypatch):
    started = asyncio.Event()
    cleaned, clients = [], []
    original = httpx.AsyncClient
    def create_client(*args, **kwargs):
        client = original(*args, **kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr('server.tokenhub.httpx.AsyncClient', create_client)
    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.append(not clients[0].is_closed)
    hub, _ = hub_for(tmp_path, handler)
    caller = asyncio.create_task(hub.snapshot(force=True))
    try:
        await asyncio.wait_for(started.wait(), 1)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await hub.close()
        assert cleaned == [True]
        assert clients[0].is_closed and not hub.snapshot_tasks
    finally:
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        await hub.close()


@pytest.mark.asyncio
async def test_snapshot_queued_before_reconnect_cannot_refresh_old_generation(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.headers.get('authorization'))
        if request.url.path.endswith('/auth/me'):
            return ok({'id': 8})
        return dashboard(request)
    hub, _ = hub_for(tmp_path, handler)
    async def login():
        return {'access_token': 'new-account-token', 'refresh_token': 'new-refresh', 'expires_at': time.time() + 600}
    hub.login = login
    await hub.snapshot_lock.acquire()
    reconnect = asyncio.create_task(hub.finish_login())
    await asyncio.sleep(0)
    stale = asyncio.create_task(hub.snapshot(force=True))
    await asyncio.sleep(0)
    try:
        hub.snapshot_lock.release()
        await asyncio.gather(reconnect, stale)
        assert len(calls) == 5 and set(calls) == {'Bearer new-account-token'}
        assert hub.view()['sections']['account']['data']['id'] == 8
        await hub.disconnect()
        assert (await hub.snapshot())['sections'] == {}
    finally:
        await hub.close()


@pytest.mark.asyncio
async def test_requests_release_clients_and_do_not_share_authorization_or_cookies(tmp_path, monkeypatch):
    clients, headers = [], []
    original = httpx.AsyncClient
    def create_client(*args, **kwargs):
        client = original(*args, **kwargs)
        clients.append(client)
        return client
    def handler(request):
        headers.append(dict(request.headers))
        return ok({'id': 7}, headers={'set-cookie': 'session=private-cookie; Path=/; Secure; HttpOnly'})
    hub, session = hub_for(tmp_path, handler)
    monkeypatch.setattr('server.tokenhub.httpx.AsyncClient', create_client)
    try:
        await hub.request('/auth/me')
        assert clients and all(client.is_closed for client in clients)
        session.save({'access_token': 'second-token', 'refresh_token': 'second-refresh', 'expires_at': time.time() + 600})
        await hub.request('/auth/me')
        await hub.raw('GET', '/auth/me')
        assert [item.get('authorization') for item in headers] == ['Bearer first-token', 'Bearer second-token', None]
        assert not any('cookie' in item for item in headers)
        assert all('authorization' not in client.headers for client in clients)
        assert all(client.is_closed for client in clients)
    finally:
        await hub.close()
    assert clients and all(client.is_closed for client in clients)


def logs_for(handler):
    logs = TaskLogs(transport=httpx.MockTransport(handler), session_loader=lambda: {'headers': {}})
    logs.reader.workspace_uid = 'workspace-id'
    logs.reader.station = '/fixture-station'
    return logs


def log_response(request):
    path = request.url.path
    if path.endswith('/trainingJobsOwn'):
        return httpx.Response(200, json={'training_jobs': [{'name': 'job', 'uid': 'uid', 'display_name': 'charles_job'}]})
    if path.endswith('/workers'):
        return httpx.Response(200, json={'workers': [{'name': 'worker', 'containers': [{'name': 'main'}]}]})
    if path.endswith('/logLivestream/token'):
        return httpx.Response(200, json={'endpoint': 'monitor-data.ms-sc-01.maoshanwangtechapi.com', 'token': 'fixture-token'})
    if '/v1/polling/' in path:
        return httpx.Response(200, json={'entries': [{'type': 'LOG', 'content': base64.b64encode(b'hello\n').decode()}]})
    raise AssertionError(path)


@pytest.mark.asyncio
async def test_log_json_and_text_processing_both_run_off_event_loop(monkeypatch):
    from server import task_logs
    loop_thread = threading.get_ident()
    calls = []
    original_json, original_text = json.loads, task_logs.log_text
    def parse(value, *args, **kwargs):
        if isinstance(value, bytearray):
            calls.append(('json', threading.get_ident()))
        return original_json(value, *args, **kwargs)
    def text(*args):
        calls.append(('text', threading.get_ident()))
        return original_text(*args)
    monkeypatch.setattr(task_logs.json, 'loads', parse)
    monkeypatch.setattr(task_logs, 'log_text', text)
    result = await logs_for(log_response).read('ACP:job', 'worker', 'main')
    assert result['text'] == 'hello\n'
    assert [name for name, _ in calls] == ['json', 'text']
    assert all(thread != loop_thread for _, thread in calls)


@pytest.mark.asyncio
async def test_log_response_limit_is_enforced_before_dispatch(monkeypatch):
    from server import task_logs
    monkeypatch.setattr(task_logs, 'MAX_RESPONSE', 3)
    def unexpected(*args):
        raise AssertionError('Oversized log responses must not enter the worker pool')
    monkeypatch.setattr(task_logs, 'parse_logs', unexpected)
    with pytest.raises(ActionError) as error:
        await logs_for(log_response).read('ACP:job', 'worker', 'main')
    assert error.value.code == 'logs_unavailable' and error.value.status == 502


@pytest.mark.asyncio
async def test_cancelling_log_decode_does_not_wait_for_or_publish_worker_result(monkeypatch):
    from server import task_logs
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = task_logs.parse_logs
    def parse(*args):
        entered.set()
        try:
            if not release.wait(2):
                raise AssertionError('Worker was not released')
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(task_logs, 'parse_logs', parse)
    task = asyncio.create_task(logs_for(log_response).read('ACP:job', 'worker', 'main'))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert not finished.is_set()
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        await asyncio.gather(task, return_exceptions=True)
