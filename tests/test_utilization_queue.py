"""All-user ACP utilization is read-only, site-scoped and freshness-aware."""
import asyncio
import json
import time

import httpx
import pytest

from server.sites import MAOSHANWANG, SENSECORE
from server.normalize import iso


def job(site=MAOSHANWANG, name='low', state='RUNNING', gpus=8):
    return {'id': site.workspace + '/trainingJobs/' + name, 'name': name,
            'uid': 'uid-' + name, 'display_name': 'alice ' + name, 'state': state,
            'ownership': {'user_name': 'alice'}, 'resource_pool': {'display_name': 'cluster'},
            'roles': [{'total_replicas': 1, 'resource_spec': [{'limits': {'device': {'number': gpus}}}]}],
            'env': {'SECRET': 'private'}, 'command': 'private-command'}


def manager(site=MAOSHANWANG, rows=None, values=None, *, fail=False):
    from server.utilization_queue import UtilizationQueue
    calls = []
    now = int(time.time())
    rows = rows if rows is not None else [job(site)]
    values = values if values is not None else {'low': [('worker-0', 0, now)]}

    async def handler(request):
        calls.append(request)
        assert request.headers['authorization'] == site.id
        if request.url.path.endswith('/trainingJobs'):
            assert request.method == 'GET'
            assert request.url.params['filter'] == "state='RUNNING'"
            assert request.url.params['page_size'] == '100'
            assert site.workspace in request.url.path
            if fail: return httpx.Response(403, json={'secret': 'private'})
            token = request.url.params['page_token']
            return httpx.Response(200, json={'training_jobs': rows[:2] if token == '1' else rows[2:],
                                            'next_page_token': '2' if token == '1' and len(rows) > 2 else ''})
        if request.url.path.endswith('/workers'):
            assert request.method == 'GET'
            name = request.url.path.split('/trainingJobs/')[1].split('/')[0]
            workers = ['worker-0', 'worker-1'] if name == 'partial' else ['worker-0']
            return httpx.Response(200, json={'workers': [{'name': w} for w in workers]})
        assert request.method == 'POST' and request.url.path.endswith('/WorkerGpuUtilTotal/metrics')
        body = json.loads(request.content)
        assert body['end'] - body['start'] <= 360
        assert body['query_filter']['resource_id'] == ['workspace-uid']
        name = body['query_filter']['filters']['label_acp_lepton_sensetime_com_job_name'][4:]
        return httpx.Response(200, json={'metrics_result': [
            {'metrics_attrs': {'exported_pod': worker},
             'values': [{'metric_time': iso(stamp), 'values': value}]}
            for worker, value, stamp in values.get(name, [])]})

    obj = UtilizationQueue(site, transport=httpx.MockTransport(handler),
                           session_loader=lambda: {'headers': {'authorization': site.id}})
    async def metadata(client):
        obj.reader.workspace_uid = 'workspace-uid'
        obj.reader.station = '/station'
    obj.reader.metadata = metadata
    return obj, calls


async def completed(obj):
    await obj.snapshot()
    await obj.refresh_task
    return await obj.snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('site', [MAOSHANWANG, SENSECORE])
async def test_all_users_running_acp_paginate_sort_and_allowlist(site):
    now = int(time.time())
    rows = [job(site, 'high'), job(site), job(site, 'missing'), job(site, 'queued', 'QUEUEING'),
            job(site, 'done', 'SUCCEEDED'), job(SENSECORE if site == MAOSHANWANG else MAOSHANWANG, 'foreign'),
            {**job(site, 'cci'), 'id': site.workspace + '/apps/cci'}, job(site, 'cpu', gpus=0)]
    obj, calls = manager(site, rows, {'low': [('worker-0', 0, now)], 'high': [('worker-0', .8, now)]})
    try:
        data = await completed(obj)
        assert data['site'] == site.id
        assert [t['id'] for t in data['tasks'][:2]] == ['ACP:low', 'ACP:high']
        assert {t['id'] for t in data['tasks']} == {'ACP:low', 'ACP:high', 'ACP:missing', 'ACP:cpu'}
        assert [t['gpu_utilization'] for t in data['tasks']] == [0, 80, None, None]
        assert all(t['owner'] == 'alice' and t['kind'] == 'ACP' for t in data['tasks'])
        assert all(t['url'].startswith(site.console + '/acp/list/detail?') for t in data['tasks'])
        assert not any(k in t for t in data['tasks'] for k in ('uid', 'source_id', 'env', 'command', 'series', 'can_stop'))
        assert 'private' not in json.dumps(data)
        assert not any('/cpu/workers' in str(r.url) for r in calls)
        assert not any('Own' in str(r.url) or '/apps' in str(r.url) for r in calls)
        assert data['status'] == 'partial' and not data['loading']
        count = len(calls)
        assert await obj.snapshot() == data
        assert len(calls) == count
    finally: await obj.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('site', [MAOSHANWANG, SENSECORE])
async def test_all_creation_dates_include_old_jobs_and_rank_latest_samples(site):
    now = int(time.time())
    rows = [job(site, 'recent'), job(site, 'ended', 'SUCCEEDED'),
            {**job(site, 'old'), 'create_time': iso(now - 120 * 86400),
             'start_time': iso(now - 119 * 86400)}]
    obj, calls = manager(site, rows, {
        'old': [('worker-0', .99, now - 120), ('worker-0', 0, now)],
        'recent': [('worker-0', 0, now - 120), ('worker-0', .2, now)]})
    try:
        data = await completed(obj)
        assert [(row['id'], row['gpu_utilization']) for row in data['tasks']] == [
            ('ACP:old', 0), ('ACP:recent', 20)]
        listing = [request for request in calls if request.url.path.endswith('/trainingJobs')]
        assert [request.url.params['page_token'] for request in listing] == ['1', '2']
        for request in listing:
            assert dict(request.url.params) == {
                'page_size': '100', 'page_token': request.url.params['page_token'],
                'filter': "state='RUNNING'"}
    finally: await obj.close()


@pytest.mark.asyncio
async def test_creation_runtime_and_personal_filter_use_platform_start_and_name(monkeypatch):
    now = int(time.time())
    monkeypatch.setattr('server.utilization_queue.time.time', lambda: now)
    rows = [{**job(name='mine'), 'display_name': 'train_CHARLES_model',
             'create_time': iso(now - 86400), 'start_time': iso(now - 3600)},
            {**job(name='other'), 'ownership': {'user_name': 'charles'},
             'create_time': iso(now - 600), 'start_time': iso(now - 120)}]
    obj, calls = manager(rows=rows, values={'mine': [('worker-0', .4, now)],
                                          'other': [('worker-0', .2, now)]})
    try:
        data = await completed(obj)
        mine = next(row for row in data['tasks'] if row['id'] == 'ACP:mine')
        assert mine['created_at'] == iso(now - 86400)
        assert mine['started_at'] == iso(now - 3600)
        assert mine['duration_seconds'] == 3600
        assert mine['is_mine'] is True
        assert next(row for row in data['tasks'] if row['id'] == 'ACP:other')['is_mine'] is False
        request_count = len(calls)
        now += 120
        newer = await obj.snapshot()
        assert next(row for row in newer['tasks'] if row['is_mine'])['duration_seconds'] == 3720
        assert len(calls) == request_count
        obj.status = 'error'
        stale = await obj.snapshot()
        assert next(row for row in stale['tasks'] if row['is_mine'])['duration_seconds'] == 3600
    finally: await obj.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('offset', [None, 'invalid', 60, -90000])
async def test_unknown_or_invalid_start_never_uses_creation_as_runtime(offset):
    now = int(time.time())
    started = iso(now + offset) if isinstance(offset, int) else offset
    obj, _ = manager(rows=[{**job(), 'create_time': iso(now - 86400), 'start_time': started}])
    try:
        row = (await completed(obj))['tasks'][0]
        assert row['created_at'] == iso(now - 86400)
        assert row['started_at'] is None
        assert row['duration_seconds'] is None
    finally: await obj.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('value,age', [(None, 0), (.1, 301), (.1, -60), (1.1, 0), (-.1, 0)])
async def test_invalid_or_expired_sample_is_unknown_not_zero(value, age):
    obj, _ = manager(values={'low': [('worker-0', value, int(time.time()) - age)]})
    try:
        data = await completed(obj)
        assert data['tasks'][0]['gpu_utilization'] is None
        assert data['tasks'][0]['metric_status'] != 'ok'
    finally: await obj.close()


@pytest.mark.asyncio
async def test_missing_worker_does_not_rank_partial_mean_as_idle():
    obj, _ = manager(rows=[job(name='partial')], values={'partial': [('worker-0', 0, int(time.time()))]})
    try:
        data = await completed(obj)
        assert data['tasks'][0]['gpu_utilization'] is None
        assert data['tasks'][0]['metric_status'] == 'unknown'
    finally: await obj.close()


@pytest.mark.asyncio
async def test_fresh_workers_average_without_summing_utilization():
    now = int(time.time())
    obj, _ = manager(rows=[job(name='partial')], values={'partial': [('worker-0', .1, now), ('worker-1', .9, now)]})
    try:
        data = await completed(obj)
        assert data['tasks'][0]['gpu_utilization'] == 50
        assert data['tasks'][0]['metric_updated_at'] == iso(now)
    finally: await obj.close()


@pytest.mark.asyncio
async def test_permission_failure_preserves_list_but_marks_old_ranking_unknown():
    obj, _ = manager(fail=True)
    obj.records = [{'id': 'ACP:old', 'name': 'old', 'gpu_utilization': 0,
                    'metric_status': 'ok', 'metric_updated_at': iso(time.time())}]
    obj.updated_at = iso(time.time() - 60)
    try:
        data = await completed(obj)
        assert data['status'] == 'error' and data['warnings']
        assert data['tasks'][0]['id'] == 'ACP:old'
        assert data['tasks'][0]['gpu_utilization'] is None
        assert 'private' not in json.dumps(data)
    finally: await obj.close()


@pytest.mark.asyncio
async def test_refresh_deduplicates_and_close_cancels_pending_work():
    obj, _ = manager()
    entered = asyncio.Event()
    async def blocked():
        entered.set()
        await asyncio.Event().wait()
    obj.refresh = blocked
    first = await obj.snapshot()
    work = obj.refresh_task
    await entered.wait()
    assert first['loading'] and (await obj.snapshot())['loading']
    assert obj.refresh_task is work
    await obj.close()
    assert work.cancelled()


def test_api_is_site_scoped_and_readonly(tmp_path):
    from fastapi.testclient import TestClient
    from server.main import create_app, SiteRuntime
    from server.service import Monitor
    from server.store import Store
    class Queue:
        def __init__(self, site): self.site = site
        async def snapshot(self): return {'site': self.site.id, 'tasks': [], 'loading': False}
        async def close(self): pass
    runtimes = {s.id: SiteRuntime(Monitor(Store(tmp_path / s.id), None), None, None, site=s,
                                 utilization_queue=Queue(s)) for s in (MAOSHANWANG, SENSECORE)}
    with TestClient(create_app(runtimes=runtimes, background=False), base_url='http://localhost') as client:
        for site in runtimes:
            response = client.get('/api/utilization-queue', headers={'X-Monitor-Site': site})
            assert response.status_code == 200 and response.json()['site'] == site
        assert client.get('/api/utilization-queue', headers={'X-Monitor-Site': 'bad'}).status_code == 400
        assert client.post('/api/utilization-queue', headers={'X-Monitor-Request': '1'}).status_code == 405


def test_mobile_readonly_device_can_view_queue(tmp_path):
    from tests.test_mobile_gateway import setup
    _, device, calls, client = setup(tmp_path)
    with client:
        response = client.get('/api/utilization-queue', headers={'Authorization': 'Bearer ' + device['token']})
        assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid,age', [(None, 0), (.5, 301), (.5, 120), (1.5, 0)])
async def test_one_missing_or_misaligned_gpu_never_becomes_idle(invalid, age):
    now = int(time.time())
    obj, _ = manager()
    request = obj.reader.request
    async def metrics(client, service, path, **kwargs):
        if not path.endswith('/metrics'):
            return await request(client, service, path, **kwargs)
        return {'metrics_result': [
            {'metrics_attrs': {'exported_pod': 'worker-0', 'gpu': str(index)},
             'values': [{'metric_time': iso(stamp), 'values': value}]}
            for index, value, stamp in [(0, 0, now), (1, invalid, now - age)]]}
    obj.reader.request = metrics
    try:
        data = await completed(obj)
        assert data['tasks'][0]['gpu_utilization'] is None
        assert data['tasks'][0]['metric_status'] == 'unknown'
    finally: await obj.close()


def test_construct_before_server_loop_supports_more_than_three_jobs():
    previous_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    setup_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(setup_loop)
    names = ['job-' + str(index) for index in range(4)]
    now = int(time.time())
    obj, _ = manager(rows=[job(name=name) for name in names],
                     values={name: [('worker-0', 0, now)] for name in names})
    read_gpu = obj.read_gpu
    async def slow_gpu(*args):
        await asyncio.sleep(.01)
        await read_gpu(*args)
    obj.read_gpu = slow_gpu
    async def run():
        try:
            data = await completed(obj)
            assert data['status'] == 'ok'
            assert [task['gpu_utilization'] for task in data['tasks']] == [0, 0, 0, 0]
        finally: await obj.close()
    try:
        asyncio.run(run())
    finally:
        setup_loop.close()
        asyncio.set_event_loop_policy(previous_policy)


@pytest.mark.asyncio
@pytest.mark.parametrize("slots", [3, 4, 6, 8])
async def test_gpu_collection_respects_machine_concurrency(monkeypatch, slots):
    monkeypatch.setattr("server.utilization_queue.get_runtime_profile", lambda: {"queue_concurrency": slots})
    obj, _ = manager(rows=[job(name='job-' + str(index)) for index in range(7)])
    active = 0
    maximum = 0

    async def measured_gpu(*args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1

    obj.read_gpu = measured_gpu
    try:
        data = await completed(obj)
        assert maximum == min(slots, 7)
    finally:
        await obj.close()


@pytest.mark.asyncio
async def test_training_list_and_metadata_start_in_parallel():
    obj, _ = manager(rows=[job(name='parallel')])
    listing_started = asyncio.Event()
    metadata_started = asyncio.Event()

    async def listing(*args, **kwargs):
        listing_started.set()
        await metadata_started.wait()
        return [job(name='parallel')]

    async def metadata(*args, **kwargs):
        metadata_started.set()
        await listing_started.wait()
        obj.reader.workspace_uid = 'workspace-uid'
        obj.reader.station = '/station'

    obj.reader.paginate = listing
    obj.reader.metadata = metadata
    obj.gpu = lambda *args: asyncio.sleep(0)
    try:
        await asyncio.wait_for(obj.collect(), .2)
    finally:
        await obj.close()


def test_performance_queue_constructed_before_server_loop_handles_request_contention(monkeypatch):
    previous_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    setup_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(setup_loop)
    monkeypatch.setattr('server.utilization_queue.get_runtime_profile', lambda: {'queue_concurrency': 8})
    monkeypatch.setattr('server.collector.get_runtime_profile', lambda: {'collector_concurrency': 6, 'metric_concurrency': 4})
    names = ['job-' + str(index) for index in range(8)]
    now = int(time.time())
    obj, _ = manager(rows=[job(name=name) for name in names],
                     values={name: [('worker-0', 0, now)] for name in names})
    handler = obj.transport.handler
    async def delayed(request):
        if request.url.path.endswith('/workers'):
            await asyncio.sleep(.03)
        return await handler(request)
    obj.transport = httpx.MockTransport(delayed)
    async def run():
        try:
            data = await completed(obj)
            assert data['status'] == 'ok', [(row['id'], row['metric_status']) for row in data['tasks']]
            assert [row['gpu_utilization'] for row in data['tasks']] == [0] * 8
        finally:
            await obj.close()
    try:
        asyncio.run(run())
    finally:
        setup_loop.close()
        asyncio.set_event_loop_policy(previous_policy)
