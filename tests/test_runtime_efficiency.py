"""Regression checks for hot paths, without cloud calls or timing thresholds."""
import asyncio
import copy
import sqlite3
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from server.collector import Collector, SourceError
from server.main import create_app
from server.service import Monitor
from server.store import Store


def snapshot():
    return {'tasks': [{'id': 'CCI:one', 'kind': 'CCI', 'state': 'running',
                      'series': [{'metric': 'gpu', 'label': 'gpu-0',
                                  'points': [[60, None], [120, 20]]}]}],
            'clusters': [], 'status': 'ok', 'connected': True}


def test_unchanged_samples_are_not_written_but_corrections_are(tmp_path):
    store = Store(tmp_path / 'db')
    state = snapshot()
    original = copy.deepcopy(state)
    store.save(state, 120)
    with store.connect() as db:
        db.executescript('''
            CREATE TABLE writes (value REAL);
            CREATE TRIGGER sample_insert AFTER INSERT ON samples BEGIN
                INSERT INTO writes VALUES (new.value);
            END;
            CREATE TRIGGER sample_update AFTER UPDATE ON samples BEGIN
                INSERT INTO writes VALUES (new.value);
            END;
        ''')
    store.save(state, 120)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writes').fetchone()[0] == 0
    assert state == original
    state['tasks'][0]['series'][0]['points'] = [[60, 10], [120, None], [180, 30]]
    store.save(state, 180)
    assert store.history('CCI:one', 0)[0]['points'] == [[60, 10], [120, None], [180, 30]]
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writes').fetchone()[0] == 3


@pytest.mark.parametrize('fail', [False, True])
def test_database_context_closes_and_preserves_transaction_semantics(tmp_path, fail):
    store = Store(tmp_path / 'db')
    try:
        with store.connect() as db:
            db.execute("INSERT INTO task_runs VALUES ('new')")
            if fail:
                raise ValueError('rollback')
    except ValueError:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute('SELECT 1')
    assert ('new' in store.running_task_ids()) is not fail


class NoCopySeries(list):
    def __deepcopy__(self, memo):
        raise AssertionError('Private curves must not be copied only to discard them')


def test_state_response_skips_curves_without_mutating_monitor(tmp_path):
    monitor = Monitor(Store(tmp_path / 'db'), None)
    series = NoCopySeries([{'metric': 'gpu', 'points': [[1, 2]]}])
    monitor.state['tasks'] = [{'id': 'CCI:one', 'kind': 'CCI', 'series': series,
                               'uid': 'private', 'source_id': 'one'}]
    monitor.state['waiting_tasks'] = [{'id': 'ACP:queued', 'kind': 'ACP', 'uid': 'private'}]
    with TestClient(create_app(monitor, background=False), base_url='http://localhost') as client:
        data = client.get('/api/state').json()
        assert 'series' not in data['tasks'][0] and 'uid' not in data['tasks'][0]
        assert 'uid' not in data['waiting_tasks'][0]
        assert data['tasks'][0]['url'] and data['waiting_tasks'][0]['url']
    assert monitor.state['tasks'][0]['series'] is series
    assert monitor.state['tasks'][0]['uid'] == 'private'
    assert 'url' not in monitor.state['tasks'][0]


def test_snapshot_save_skips_copying_metric_points(tmp_path):
    store = Store(tmp_path / 'db')
    state = snapshot()
    state['tasks'][0]['series'] = NoCopySeries(state['tasks'][0]['series'])
    store.save(state, 120)
    assert store.latest()['tasks'][0]['series'] == []
    assert len(store.history('CCI:one', 0)[0]['points']) == 2


class Collected:
    async def collect(self, previous):
        return snapshot()


@pytest.mark.asyncio
async def test_database_save_and_prune_do_not_run_on_event_loop(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db')
    loop_thread = threading.get_ident()
    calls = []
    for name in ('save', 'prune'):
        original = getattr(store, name)
        def record(*args, _name=name, _original=original):
            calls.append((_name, threading.get_ident()))
            return _original(*args)
        monkeypatch.setattr(store, name, record)
    monitor = Monitor(store, Collected())
    await monitor.refresh()
    assert monitor.state['status'] == 'ok'
    assert [name for name, _ in calls] == ['save', 'prune']
    assert all(thread != loop_thread for _, thread in calls)


@pytest.mark.asyncio
async def test_history_query_does_not_run_on_event_loop(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db')
    store.save(snapshot(), 120)
    calls = []
    original = store.history
    def history(*args):
        calls.append(threading.get_ident())
        return original(*args)
    monkeypatch.setattr(store, 'history', history)
    app = create_app(Monitor(store, None), background=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
        response = await client.get('/api/history?task_id=CCI:one')
    assert response.status_code == 200
    assert calls and all(thread != threading.get_ident() for thread in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 404, 422])
async def test_permanent_cloud_errors_are_not_retried(status):
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(status, text='private error')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError, match='云接口请求失败'):
            await Collector().request(client, 'CCI', '/missing')
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_backoff_does_not_hold_request_slot(monkeypatch):
    collector = Collector()
    collector.semaphore = asyncio.Semaphore(1)
    slot_states = []
    async def pause(delay):
        slot_states.append(collector.semaphore.locked())
    monkeypatch.setattr('server.collector.asyncio.sleep', pause)
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) < 3 else 200, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await collector.request(client, 'CCI', '/list') == {}
    assert len(calls) == 3 and slot_states == [False, False]


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_write', [False, True])
async def test_cancellation_drains_inflight_database_write_before_unlock(tmp_path, monkeypatch, fail_write):
    store = Store(tmp_path / 'db')
    monitor = Monitor(store, Collected())
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = store.save
    def save(*args):
        entered.set()
        # Bounded fallback ensures a broken implementation cannot hang the test.
        if not release.wait(2):
            raise RuntimeError('write was never released')
        finished.set()
        if fail_write:
            raise OSError('disk unavailable during shutdown')
        original(*args)
    monkeypatch.setattr(store, 'save', save)
    refresh = asyncio.create_task(monitor.refresh())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        refresh.cancel()
        await asyncio.sleep(0)
        assert monitor.lock.locked() and not refresh.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await refresh
    assert finished.is_set() and not monitor.lock.locked()
    if fail_write:
        assert store.latest() is None
    else:
        assert store.latest()['tasks'][0]['id'] == 'CCI:one'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [408, 429, 503, 'timeout', 'invalid_json'])
async def test_transient_errors_keep_bounded_retries_and_sanitized_message(monkeypatch, failure):
    delays = []
    async def pause(delay):
        delays.append(delay)
    monkeypatch.setattr('server.collector.asyncio.sleep', pause)
    calls = []
    async def handler(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('private endpoint', request=request)
        if failure == 'invalid_json':
            return httpx.Response(200, text='private non-json response')
        return httpx.Response(failure, text='private error')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError) as error:
            await Collector().request(client, 'CCI', '/list')
    assert len(calls) == 3 and delays == [1, 2]
    assert 'private' not in str(error.value)


@pytest.mark.asyncio
async def test_write_failure_keeps_previous_snapshot_and_releases_lock(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db')
    monitor = Monitor(store, Collected())
    monitor.state['tasks'] = [{'id': 'CCI:previous'}]
    def fail(*args):
        raise OSError('private disk details')
    monkeypatch.setattr(store, 'save', fail)
    assert await monitor.refresh()
    assert monitor.state['tasks'] == [{'id': 'CCI:previous'}]
    assert monitor.state['status'] == 'error'
    assert 'private' not in monitor.state['error']
    assert not monitor.lock.locked()


@pytest.mark.asyncio
async def test_history_json_encoding_does_not_run_on_event_loop(tmp_path, monkeypatch):
    from starlette.responses import JSONResponse
    store = Store(tmp_path / 'db')
    store.save(snapshot(), 120)
    app = create_app(Monitor(store, None), background=False)
    renders = []
    original = JSONResponse.render
    def render(self, content):
        renders.append(threading.get_ident())
        return original(self, content)
    monkeypatch.setattr(JSONResponse, 'render', render)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
        response = await client.get('/api/history?task_id=CCI:one')
    assert response.status_code == 200
    assert renders and all(thread != threading.get_ident() for thread in renders)
