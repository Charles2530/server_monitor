"""Local regression checks for independent cloud reads and history range scans."""
import asyncio

import pytest

from server.auth import AuthRequired
from server.collector import Collector, SourceError
from server.store import Store


@pytest.fixture
def collector(monkeypatch):
    obj = Collector()
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr('server.collector.load_session', lambda: {'headers': {}})
    monkeypatch.setattr('server.collector.enrich_clusters', noop)
    monkeypatch.setattr(obj, 'metadata', noop)
    monkeypatch.setattr(obj, 'task_metrics', noop)
    return obj


def rows(path):
    name = path.rsplit('/', 1)[-1]
    if name == 'workspaceAEC2Bindings':
        return [{'name': 'cluster', 'state': 'ACTIVE'}]
    if name == 'appsOwn':
        return [{'name': 'cci', 'display_name': 'charles_cci', 'state': 'RUNNING'}]
    if name == 'trainingJobsOwn':
        return [{'name': 'acp', 'display_name': 'charles_acp', 'state': 'RUNNING'},
                {'name': 'waiting', 'display_name': 'charles_waiting', 'state': 'QUEUEING'}]
    return [{'name': 'waiting', 'display_name': 'charles_waiting',
             'state': 'QUEUEING' if name == 'trainingJobs' else 'QUEUING'}]


@pytest.mark.asyncio
async def test_metadata_overlaps_lists_but_metrics_wait_for_metadata(collector, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    seen, metrics = [], []

    async def metadata(client):
        await release.wait()
        collector.workspace_uid = 'ready'

    async def pages(client, service, path, key, extra_params=None):
        seen.append(path)
        if len(seen) == 5:
            started.set()
        return rows(path)

    async def read_metrics(client, task, now):
        assert collector.workspace_uid == 'ready'
        metrics.append(task['id'])

    monkeypatch.setattr(collector, 'metadata', metadata)
    monkeypatch.setattr(collector, 'paginate', pages)
    monkeypatch.setattr(collector, 'task_metrics', read_metrics)
    task = asyncio.create_task(collector.collect())
    try:
        await asyncio.wait_for(started.wait(), .5)
        assert not metrics and not task.done()
        release.set()
        result = await task
        assert metrics == ['CCI:cci', 'ACP:acp']
        assert result['waiting_tasks'][1]['can_stop'] is True
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['metadata', 'list', 'cancel'])
async def test_parallel_metadata_and_lists_drain_on_failure(collector, monkeypatch, failure):
    started, fail = asyncio.Event(), asyncio.Event()
    entered, cleaned = [], []

    async def read(client, name):
        entered.append(name)
        if len(entered) == 6:
            started.set()
        try:
            await fail.wait()
            if name == ('metadata' if failure == 'metadata' else 'appsOwn'):
                raise AuthRequired('expired')
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.append((name, client.is_closed))

    async def pages(client, service, path, key, extra_params=None):
        return await read(client, path.rsplit('/', 1)[-1])

    monkeypatch.setattr(collector, 'metadata', lambda client: read(client, 'metadata'))
    monkeypatch.setattr(collector, 'paginate', pages)
    task = asyncio.create_task(collector.collect())
    try:
        await asyncio.wait_for(started.wait(), .5)
        if failure == 'cancel':
            task.cancel()
        else:
            fail.set()
        with pytest.raises(asyncio.CancelledError if failure == 'cancel' else AuthRequired):
            await asyncio.wait_for(task, .5)
        assert len(cleaned) == 6
        assert all(not closed for _, closed in cleaned)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_five_lists_overlap_but_assembly_and_ownership_are_ordered(collector, monkeypatch):
    started = asyncio.Event()
    gates = {}
    async def pages(client, service, path, key, extra_params=None):
        gate = gates[path] = asyncio.Event()
        if len(gates) == 5:
            started.set()
        await gate.wait()
        return rows(path)
    monkeypatch.setattr(collector, 'paginate', pages)
    task = asyncio.create_task(collector.collect())
    try:
        await asyncio.wait_for(started.wait(), 1)
        # Queues and bindings finish before personal ownership is known.
        for gate in reversed(list(gates.values())):
            gate.set()
            await asyncio.sleep(0)
        state = await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert [item['id'] for item in state['tasks']] == ['CCI:cci', 'ACP:acp']
    assert [item['kind'] for item in state['waiting_tasks']] == ['CCI', 'ACP']
    assert state['waiting_tasks'][1]['can_stop'] is True
    assert list(state['source_status']) == ['CCI', 'ACP', 'CCI_queue', 'ACP_queue', 'resources']


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', ['appsOwn', 'trainingJobsOwn', 'workspaceAEC2Bindings'])
async def test_personal_or_resource_auth_failure_remains_fatal(collector, monkeypatch, failed):
    async def pages(client, service, path, key, extra_params=None):
        if path.endswith('/' + failed):
            raise AuthRequired('expired')
        return rows(path)
    monkeypatch.setattr(collector, 'paginate', pages)
    with pytest.raises(AuthRequired):
        await collector.collect()


@pytest.mark.asyncio
async def test_queue_auth_and_personal_list_failure_keep_previous_sources(collector, monkeypatch):
    async def pages(client, service, path, key, extra_params=None):
        if path.endswith('/appsOwn'):
            raise SourceError('unavailable')
        if path.endswith('/trainingJobs'):
            raise AuthRequired('all-user list denied')
        return rows(path)
    monkeypatch.setattr(collector, 'paginate', pages)
    old_task = {'id': 'CCI:old', 'kind': 'CCI', 'series': []}
    old_waiting = {'id': 'ACP:old', 'kind': 'ACP', 'state': 'QUEUEING'}
    state = await collector.collect({'tasks': [old_task], 'waiting_tasks': [old_waiting],
                                     'source_status': {'CCI': {'updated_at': 'old'}, 'ACP_queue': {'updated_at': 'old'}}})
    assert state['status'] == 'partial'
    assert state['tasks'][0] == old_task
    assert state['waiting_tasks'][-1] == old_waiting
    assert state['source_status']['CCI']['updated_at'] == 'old'
    assert state['source_status']['ACP_queue']['updated_at'] == 'old'


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', ['appsOwn', 'trainingJobsOwn', 'workspaceAEC2Bindings'])
async def test_auth_failure_cancels_slow_lists_before_client_closes(collector, monkeypatch, failed):
    started = asyncio.Event()
    entered, cleaned, clients_closed = set(), set(), []
    async def pages(client, service, path, key, extra_params=None):
        entered.add(path)
        if len(entered) == 5:
            started.set()
        try:
            await started.wait()
            if path.endswith('/' + failed):
                raise AuthRequired('expired')
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            clients_closed.append(client.is_closed)
            cleaned.add(path)
    monkeypatch.setattr(collector, 'paginate', pages)
    with pytest.raises(AuthRequired):
        await asyncio.wait_for(collector.collect(), 1)
    assert len(cleaned) == 5 and cleaned == entered
    assert clients_closed == [False] * 5


@pytest.mark.asyncio
async def test_cancelled_collection_drains_all_list_reads_before_client_closes(collector, monkeypatch):
    started = asyncio.Event()
    entered, cleaned, clients_closed = set(), set(), []
    async def pages(client, service, path, key, extra_params=None):
        entered.add(path)
        if len(entered) == 5:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            clients_closed.append(client.is_closed)
            cleaned.add(path)
    monkeypatch.setattr(collector, 'paginate', pages)
    task = asyncio.create_task(collector.collect())
    try:
        await asyncio.wait_for(started.wait(), 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert len(cleaned) == 5 and cleaned == entered
    assert clients_closed == [False] * 5


def test_existing_database_adds_index_for_task_and_time_without_changing_history(tmp_path):
    path = tmp_path / 'history.db'
    store = Store(path)
    with store.connect() as db:
        # Reproduce an existing database created before the range index.
        db.execute('DROP INDEX IF EXISTS samples_task_time')
        db.executemany('INSERT INTO samples VALUES (?,?,?,?,?)',
                       [('CCI:one', metric, label, stamp, stamp / 10)
                        for metric, label in [('gpu', 'worker-b'), ('cpu', 'worker-a')]
                        for stamp in range(100)])
    expected = store.history('CCI:one', 90)
    upgraded = Store(path)
    assert upgraded.history('CCI:one', 90) == expected
    assert sum(len(series['points']) for series in expected) == 20
    with upgraded.connect() as db:
        plan = db.execute('EXPLAIN QUERY PLAN SELECT metric,label,timestamp,value FROM samples '
                          'WHERE task=? AND timestamp>=? ORDER BY metric,label,timestamp',
                          ('CCI:one', 90)).fetchall()
    assert any('task=? AND timestamp>?' in row[3] for row in plan), plan
