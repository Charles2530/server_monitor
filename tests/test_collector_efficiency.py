"""Cold metadata and worker validation avoid independent serial/repeated work."""
import asyncio
import json

import httpx
import pytest

from server.auth import AuthRequired
from server.collector import Collector, SourceError
from server.sites import MAOSHANWANG, SENSECORE


def station_row(collector):
    return {'rid': collector.workspace.split('/workspaces/')[0] + '/telemetryStations/tenant',
            'properties': json.dumps({'station_type': 'TENANT'})}


@pytest.mark.asyncio
@pytest.mark.parametrize('site', [MAOSHANWANG, SENSECORE], ids=lambda site: site.id)
async def test_cold_metadata_reads_overlap_and_warm_metadata_does_not_fetch(site):
    collector = Collector(site)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request)
        if len(calls) == 2:
            started.set()
        await release.wait()
        return httpx.Response(200, json={'uid': 'workspace'} if request.url.path.endswith(collector.workspace)
                              else {'resources': [station_row(collector)]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(collector.metadata(client))
        try:
            await asyncio.wait_for(started.wait(), .5)
            release.set()
            await task
            assert collector.workspace_uid == 'workspace'
            assert collector.station == station_row(collector)['rid']
            await collector.metadata(client)
            assert len(calls) == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['auth', 'invalid', 'cancel'])
async def test_metadata_failure_or_cancel_drains_sibling_before_client_close(failure):
    collector = Collector()
    started, fail = asyncio.Event(), asyncio.Event()
    calls, cleaned = [], []
    async def handler(request):
        calls.append(request)
        if len(calls) == 2:
            started.set()
        try:
            if request.url.path.endswith(collector.workspace):
                await fail.wait()
                return httpx.Response(403 if failure == 'auth' else 200, json={})
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.append(client.is_closed)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(collector.metadata(client))
        try:
            await asyncio.wait_for(started.wait(), .5)
            if failure == 'cancel':
                task.cancel()
                expected = asyncio.CancelledError
            else:
                fail.set()
                expected = AuthRequired if failure == 'auth' else SourceError
            with pytest.raises(expected):
                await asyncio.wait_for(task, .5)
            assert cleaned == [False, False]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_metadata_retries_only_missing_part_and_preserves_tenant_filter():
    collector = Collector()
    collector.workspace_uid = 'cached-workspace'
    calls = []
    valid = station_row(collector)
    async def handler(request):
        calls.append(request)
        assert request.url.path == '/rmh/v1/resources'
        rows = [{'rid': valid['rid'], 'properties': 'invalid json'},
                {'rid': '/other/telemetryStations/tenant', 'properties': valid['properties']},
                {'rid': valid['rid'], 'properties': '{"station_type":"USER"}'}]
        if len(calls) > 1:
            rows.append(valid)
        return httpx.Response(200, json={'resources': rows})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError):
            await collector.metadata(client)
        assert collector.workspace_uid == 'cached-workspace' and collector.station is None
        await collector.metadata(client)
    assert len(calls) == 2 and collector.station == valid['rid']


@pytest.mark.asyncio
@pytest.mark.parametrize('workspace_status', [200, 403])
@pytest.mark.parametrize('station_valid', [False, True])
async def test_metadata_keeps_workspace_error_priority_and_cache_order(workspace_status, station_valid):
    collector = Collector()
    station_finished, release_workspace = asyncio.Event(), asyncio.Event()
    async def handler(request):
        if request.url.path.endswith(collector.workspace):
            await release_workspace.wait()
            return httpx.Response(workspace_status, json={})
        station_finished.set()
        return httpx.Response(200, json={'resources': [station_row(collector)] if station_valid else []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(collector.metadata(client))
        try:
            await asyncio.wait_for(station_finished.wait(), .5)
            # Let the station result reach the caller while workspace remains blocked.
            for _ in range(3):
                await asyncio.sleep(0)
            release_workspace.set()
            expected = AuthRequired if workspace_status == 403 else SourceError
            with pytest.raises(expected) as caught:
                await task
            if workspace_status == 200:
                assert '工作空间标识缺失' in str(caught.value)
            assert not collector.workspace_uid and collector.station is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('second_station', ['error', 'blocked'])
async def test_concurrent_metadata_reuses_station_completed_by_other_reader(second_station):
    collector = Collector()
    release_workspace = [asyncio.Event(), asyncio.Event()]
    station_started = [asyncio.Event(), asyncio.Event()]
    cleaned = []
    def handler(index):
        async def respond(request):
            if request.url.path.endswith(collector.workspace):
                await release_workspace[index].wait()
                return httpx.Response(200, json={'uid': 'workspace'})
            station_started[index].set()
            try:
                if index == 1 and second_station == 'blocked':
                    await asyncio.Event().wait()
                return httpx.Response(200, json={'resources': [station_row(collector)] if index == 0 else []})
            finally:
                await asyncio.sleep(0)
                cleaned.append(index)
        return respond
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler(0))) as first, \
               httpx.AsyncClient(transport=httpx.MockTransport(handler(1))) as second:
        tasks = [asyncio.create_task(collector.metadata(client)) for client in (first, second)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in station_started)), .5)
            release_workspace[0].set()
            await asyncio.wait_for(tasks[0], .5)
            assert collector.station == station_row(collector)['rid']
            release_workspace[1].set()
            await asyncio.wait_for(tasks[1], .5)
            assert sorted(cleaned) == [0, 1]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_validation_scans_each_series_label_once(monkeypatch):
    collector = Collector()
    collector.workspace_uid = 'workspace'; collector.station = '/station'
    workers = [f'worker-{index}' for index in range(200)]
    scans = []
    class Label(str):
        def split(self, *args, **kwargs):
            scans.append(self)
            return super().split(*args, **kwargs)
    async def pages(*args):
        return [{'name': worker} for worker in workers]
    async def request(*args, **kwargs):
        return {'metrics_result': []}
    def series(payload, metric, kind):
        return [{'metric': metric, 'label': Label(f'{metric} · {worker} / GPU 0'),
                 'points': [[1000, 0]]} for worker in workers]
    monkeypatch.setattr(collector, 'paginate', pages)
    monkeypatch.setattr(collector, 'request', request)
    monkeypatch.setattr('server.collector.metric_series', series)
    task = {'kind': 'ACP', 'source_id': 'job', 'uid': 'uid', 'series': []}
    await collector.task_metrics(None, task, 1000)
    assert task['metric_error'] is None and task['failed_metrics'] == []
    assert task['metrics'] == dict.fromkeys(('cpu', 'memory', 'gpu', 'gpu_memory'), 0)
    assert len(task['series']) == 800
    assert len(scans) <= 800, f'800 series labels were scanned {len(scans)} times'


@pytest.mark.asyncio
@pytest.mark.parametrize('unhealthy', ['missing', 'stale', 'null'])
async def test_worker_grouping_preserves_missing_stale_and_null_metrics(monkeypatch, unhealthy):
    collector = Collector()
    collector.workspace_uid = 'workspace'; collector.station = '/station'
    async def pages(*args):
        return [{'name': 'worker-1'}, {'name': 'worker-10'}]
    async def request(*args, **kwargs):
        return {'metrics_result': []}
    def series(payload, metric, kind):
        result = [{'metric': metric, 'label': f'{metric} · worker-1 / GPU 0', 'points': [[1000, 0]]},
                  {'metric': metric, 'label': f'{metric} · worker-1 / GPU 1', 'points': [[1000, 20]]}]
        if metric != 'gpu' or unhealthy != 'missing':
            point = [0, 40] if metric == 'gpu' and unhealthy == 'stale' else [1000, None if metric == 'gpu' else 40]
            result.append({'metric': metric, 'label': f'{metric} · worker-10', 'points': [point]})
        return result
    monkeypatch.setattr(collector, 'paginate', pages)
    monkeypatch.setattr(collector, 'request', request)
    monkeypatch.setattr('server.collector.metric_series', series)
    task = {'kind': 'ACP', 'source_id': 'job', 'uid': 'uid', 'series': []}
    await collector.task_metrics(None, task, 1000)
    assert task['failed_metrics'] == ['gpu']
    assert task['metrics'] == {'cpu': 20, 'memory': 20, 'gpu_memory': 20, 'gpu': None}
    assert 'worker-10' in task['metric_error']
