"""Fault injection for latency, cancellation and unrelated concurrent work."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from server.storage import StorageManager
from server.sites import MAOSHANWANG


@pytest.mark.asyncio
async def test_storage_refresh_serves_old_snapshot_without_blocking_or_duplicate_reads(monkeypatch):
    manager=StorageManager(MAOSHANWANG,session_loader=lambda:{'headers':{}})
    entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def volumes(client):
        calls.append(1)
        if len(calls)>1:
            entered.set();await release.wait();raise OSError('PRIVATE')
        return [{'id':'v','zone':'a','name':'volume','mounts':[]}]
    async def usage(*args):pass
    async def mounts(*args):return {'CCI':'ok','ACP':'ok'}
    monkeypatch.setattr(manager,'volumes',volumes);monkeypatch.setattr(manager,'usage',usage);monkeypatch.setattr(manager,'mounts',mounts)
    try:
        initial=await manager.snapshot();await manager.mount_task
        manager.cached_at-=31
        result=await asyncio.wait_for(manager.snapshot(),.2)
        await asyncio.wait_for(entered.wait(),.2)
        assert result['updated_at']==initial['updated_at'] and result['refreshing']
        others=await asyncio.wait_for(asyncio.gather(*(manager.snapshot() for _ in range(8))),.2)
        assert len(calls)==2 and all(v['volumes']==initial['volumes'] for v in others)
        release.set();await manager.refresh_task
        failed=await manager.snapshot()
        assert failed['updated_at']==initial['updated_at'] and failed['status']=='partial'
        assert not failed['refreshing'] and failed['warnings']
        assert 'PRIVATE' not in str(failed) and len(calls)==2
    finally:
        release.set();await manager.close()


@pytest.mark.asyncio
async def test_cancelled_first_storage_visitor_does_not_cancel_shared_fetch(monkeypatch):
    manager=StorageManager(MAOSHANWANG,session_loader=lambda:{'headers':{}})
    entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def volumes(client):calls.append(1);entered.set();await release.wait();return []
    async def usage(*args):pass
    async def mounts(*args):return {'CCI':'ok','ACP':'ok'}
    monkeypatch.setattr(manager,'volumes',volumes);monkeypatch.setattr(manager,'usage',usage);monkeypatch.setattr(manager,'mounts',mounts)
    first=asyncio.create_task(manager.snapshot())
    try:
        await entered.wait();first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        second=asyncio.create_task(manager.snapshot());release.set()
        assert (await second)['volumes']==[] and len(calls)==1
    finally:release.set();await manager.close()


@pytest.mark.asyncio
async def test_storage_first_fetch_has_total_deadline(monkeypatch):
    import server.storage as module
    monkeypatch.setattr(module,'CAPACITY_TIMEOUT_SECONDS',.02,raising=False)
    manager=StorageManager(MAOSHANWANG,session_loader=lambda:{'headers':{}})
    async def slow(*args):await asyncio.Event().wait()
    monkeypatch.setattr(manager,'volumes',slow)
    try:
        job=asyncio.create_task(manager.snapshot())
        done,_=await asyncio.wait([job],timeout=.2)
        assert job in done,'Server must bound the whole initial storage read'
        with pytest.raises(Exception):await job
    finally:
        job.cancel();await asyncio.gather(job,return_exceptions=True);await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/logs?task_id=CCI:one&worker=one&container=main','/api/acp/options','/api/acp/preview'])
async def test_readonly_operations_have_total_deadline_and_cancel_work(tmp_path,monkeypatch,path):
    import server.main as module
    from server.service import Monitor
    from server.store import Store
    monkeypatch.setattr(module,'READ_TIMEOUT_SECONDS',.02,raising=False)
    monkeypatch.setattr(module,'LOG_TIMEOUT_SECONDS',.02,raising=False)
    cancelled=asyncio.Event()
    async def stuck(*args,**kwargs):
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    app=module.create_app(Monitor(Store(tmp_path/'db'),None),background=False,
                          actions=SimpleNamespace(options=stuck,preview=stuck),logs=SimpleNamespace(read=stuck))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
        if path.endswith('preview'):
            job=asyncio.create_task(client.post(path,headers={'X-Monitor-Request':'1'},json={'display_name':'charles','replicas':1,'gpus_per_replica':1,'command':''}))
        else:job=asyncio.create_task(client.get(path))
        try:
            done,_=await asyncio.wait([job],timeout=.2)
            assert job in done,'Readonly upstream work must finish before browser/gateway deadline'
            response=await job
            assert response.status_code==504 and response.json()['code']=='source_timeout'
            assert cancelled.is_set()
        finally:job.cancel();await asyncio.gather(job,return_exceptions=True)


@pytest.mark.asyncio
async def test_write_operation_is_not_subject_to_readonly_timeout(tmp_path,monkeypatch):
    import server.main as module
    from server.service import Monitor
    from server.store import Store
    monkeypatch.setattr(module,'READ_TIMEOUT_SECONDS',.001,raising=False)
    calls=[]
    async def create(key):
        calls.append(key);await asyncio.sleep(.02);return {'id':'ACP:one'}
    monitor=Monitor(Store(tmp_path/'db'),None)
    app=module.create_app(monitor,background=False,actions=SimpleNamespace(create=create))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
        response=await client.post('/api/acp/create',headers={'X-Monitor-Request':'1'},json={'preview_id':'one'})
    await monitor.close()
    assert response.status_code==200 and calls==['one']


@pytest.mark.asyncio
async def test_stuck_terminal_heartbeat_releases_input_lock(monkeypatch):
    import server.terminal_bridge as module
    from tests.test_terminal_bridge import FakeSocket
    monkeypatch.setattr(module,'HEARTBEAT_INTERVAL_SECONDS',.001,raising=False)
    monkeypatch.setattr(module,'WRITE_TIMEOUT_SECONDS',.02,raising=False)
    socket=FakeSocket()
    async def blocked(data):await asyncio.Event().wait()
    socket.send=blocked
    session=module.Session(socket,'local')
    try:
        done,_=await asyncio.wait([session.heartbeat],timeout=.2)
        assert session.heartbeat in done and session.ended
        assert not session.write_lock.locked() and socket.closed
    finally:await session.close()


@pytest.mark.asyncio
async def test_terminal_input_deadline_includes_waiting_for_write_lock(monkeypatch):
    import server.terminal_bridge as module
    from server.acp_actions import ActionError
    from tests.test_terminal_bridge import FakeSocket,resolver
    monkeypatch.setattr(module,'WRITE_TIMEOUT_SECONDS',.02,raising=False)
    socket=FakeSocket();session=module.Session(socket,'local')
    bridge=module.TerminalBridge(MAOSHANWANG,resolver('CCI'),resolver('ACP'))
    bridge.sessions['session']=session
    await session.write_lock.acquire()
    job=asyncio.create_task(bridge.write('session',1,'command',None,'local'))
    try:
        done,_=await asyncio.wait([job],timeout=.2)
        assert job in done,'Input must not wait indefinitely behind a heartbeat'
        with pytest.raises(ActionError):await job
        assert not socket.sent and session.ended and session.sequence==0
    finally:
        session.write_lock.release();job.cancel();await asyncio.gather(job,return_exceptions=True);await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('healthy', [True, False])
async def test_editor_install_does_not_block_another_editor_recovery(monkeypatch, healthy):
    import server.cci_ide as module
    from tests.test_ide_recovery import DATA, ORIGIN, existing_editor
    editor, session, _ = existing_editor(monkeypatch)
    entered = asyncio.Event()
    async def install(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    async def health(value): return healthy
    reconnect = AsyncMock(return_value=session)
    monkeypatch.setattr(module, 'run_python', install)
    monkeypatch.setattr(module.SSHForward, 'warm', AsyncMock())
    monkeypatch.setattr(editor, 'healthy', health)
    monkeypatch.setattr(editor, 'connect', reconnect)
    opening = asyncio.create_task(editor.open({**DATA, 'task_id': 'CCI:other'}, ORIGIN))
    try:
        await entered.wait()
        result = await asyncio.wait_for(editor.recover(DATA, ORIGIN), .2)
        assert result['port'] == session['proxy'].port
        assert reconnect.await_count == (0 if healthy else 1)
    finally:
        opening.cancel()
        await asyncio.gather(opening, return_exceptions=True)
        await editor.close()


@pytest.mark.asyncio
async def test_editor_parallel_preparations_reserve_slots_and_release_on_cancel(monkeypatch):
    import server.cci_ide as module
    from server.acp_actions import ActionError
    from tests.test_ide_recovery import DATA, ORIGIN
    editor = module.CCIEditor(MAOSHANWANG, None, None)
    entered = []
    async def resolve(data): return {'name': 'charles'}, '10.0.0.1'
    async def install(bridge, data, *args, **kwargs):
        entered.append(data['task_id'])
        await asyncio.Event().wait()
    monkeypatch.setattr(editor, 'resolve', resolve)
    monkeypatch.setattr(module, 'run_python', install)
    monkeypatch.setattr(module.SSHForward, 'warm', AsyncMock())
    jobs = [asyncio.create_task(editor.open({**DATA, 'task_id': 'CCI:job'+str(i)}, ORIGIN)) for i in range(4)]
    try:
        for _ in range(30):
            if len(entered) == 4: break
            await asyncio.sleep(.01)
        assert len(entered) == 4, 'Unrelated editor preparations should proceed independently'
        with pytest.raises(ActionError) as error:
            await editor.open({**DATA, 'task_id': 'CCI:extra'}, ORIGIN)
        assert error.value.code == 'ide_limit'
    finally:
        for job in jobs: job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await editor.close()
    assert not editor.preparing and not editor.locks


@pytest.mark.asyncio
async def test_cancelled_acp_journal_write_does_not_block_loop_or_send_cloud_mutation(tmp_path, monkeypatch):
    import threading
    from server.acp_actions import ACPManager
    from server.store import Store
    from tests.test_acp_actions import Cloud
    store = Store(tmp_path/'db')
    cloud = Cloud()
    manager = ACPManager(store, transport=httpx.MockTransport(cloud), session_loader=lambda: {'headers': {}})
    preview = await manager.preview({'display_name': 'charles_test', 'replicas': 1, 'gpus_per_replica': 1, 'command': ''})
    original = store.begin_operation
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    def slow(*args, **kwargs):
        started.set()
        release.wait(.5)
        result = original(*args, **kwargs)
        finished.set()
        return result
    monkeypatch.setattr(store, 'begin_operation', slow)
    action = asyncio.create_task(manager.create(preview['preview_id']))
    try:
        while not started.is_set(): await asyncio.sleep(.001)
        assert not finished.is_set(), 'SQLite must not block the event loop'
        action.cancel()
        await asyncio.sleep(.01)
        assert not action.done() and manager.lock.locked()
        action.cancel()
        await asyncio.sleep(.01)
        assert not action.done() and manager.lock.locked()
        assert not cloud.writes
        release.set()
        with pytest.raises(asyncio.CancelledError): await action
        assert finished.is_set() and not manager.lock.locked() and not cloud.writes
        assert store.operation(preview['preview_id']) is not None
    finally:
        release.set()
        action.cancel()
        await asyncio.gather(action, return_exceptions=True)


@pytest.mark.asyncio
async def test_mobile_authorization_database_wait_keeps_gateway_responsive(tmp_path, monkeypatch):
    import threading
    from server.mobile_access import DeviceStore
    from server.mobile_gateway import create_gateway
    store = DeviceStore(tmp_path/'mobile.db')
    device = store.redeem(store.issue_pairing('https://test.tail.ts.net')['ticket'], 'phone')
    original = store.status
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        started.set(); release.wait(.5)
        result = original(*args, **kwargs)
        finished.set()
        return result
    monkeypatch.setattr(store, 'status', slow)
    app = create_gateway(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
        request = asyncio.create_task(client.get('/mobile/status', headers={'Authorization': 'Bearer '+device['token']}))
        try:
            while not started.is_set(): await asyncio.sleep(.001)
            assert not finished.is_set(), 'Device SQLite access must not block other gateway requests'
            assert (await client.get('/health')).status_code == 200
            release.set()
            assert (await request).status_code == 200
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['CCI', 'ACP'])
@pytest.mark.parametrize('missing_cpu', [False, True])
async def test_cpu_only_tasks_do_not_request_or_require_gpu_metrics(monkeypatch, kind, missing_cpu):
    from server.collector import Collector
    collector = Collector()
    collector.workspace_uid = 'workspace'
    collector.station = '/station'
    panels = []
    async def pages(*args): return [{'name': 'worker'}]
    async def request(client, service, path, **kwargs):
        panel = path.split('/panels/')[1].split('/')[0]
        panels.append(panel)
        empty = 'Gpu' in panel or (missing_cpu and 'Cpu' in panel)
        return {'metrics_result': [] if empty else [{'metrics_attrs': {'exported_pod': 'worker'}, 'values': [{'metric_time': '2026-09-29T07:00:00Z', 'values': .5}]}]}
    monkeypatch.setattr(collector, 'paginate', pages)
    monkeypatch.setattr(collector, 'request', request)
    task = {'kind': kind, 'source_id': 'x', 'uid': 'uid', 'gpu_count': 0, 'series': []}
    await collector.task_metrics(None, task, 1790665200)
    assert not any('Gpu' in p for p in panels)
    assert task['failed_metrics'] == (['cpu'] if missing_cpu else [])
    assert bool(task['metric_error']) == missing_cpu
    assert task['metrics']['gpu'] is None and task['metrics']['gpu_memory'] is None
    assert task['metrics']['memory'] is not None


@pytest.mark.asyncio
async def test_maoshanwang_template_uses_running_query_without_scanning_history(tmp_path):
    from server.acp_actions import ACPManager
    from server.store import Store
    from tests.test_acp_actions import Cloud
    cloud = Cloud()
    cloud.jobs[0]['state'] = 'RUNNING'
    filters = []
    async def transport(request):
        if request.url.path.endswith('trainingJobsOwn'):
            filters.append(request.url.params.get('filter'))
            assert request.url.params.get('filter') == "state='RUNNING'", 'Template must not scan thousands of completed jobs'
        return await cloud(request)
    manager = ACPManager(Store(tmp_path/'db'), transport=httpx.MockTransport(transport), session_loader=lambda: {'headers': {}})
    result = await manager.options()
    assert result['template']['id'] == 'ACP:pt-template'
    assert filters == ["state='RUNNING'"] and not cloud.writes


@pytest.mark.asyncio
async def test_running_collector_queries_active_states_but_preserves_queue_ownership(monkeypatch):
    from server.collector import Collector
    from server.queue_states import QUEUE_LABELS
    collector = Collector()
    monkeypatch.setattr('server.collector.load_session', lambda: {'headers': {}})
    monkeypatch.setattr(collector, 'metadata', AsyncMock())
    async def pages(client, service, path, key, extra_params=None):
        if path.endswith('appsOwn'):
            assert extra_params == {'filter': "state='RUNNING'"}
            return []
        if path.endswith('trainingJobsOwn'):
            expression = (extra_params or {}).get('filter', '')
            assert "state='RUNNING'" in expression
            assert all("state='"+state+"'" in expression for state in QUEUE_LABELS['ACP'])
            assert 'SUCCEEDED' not in expression and 'SUSPENDED' not in expression
            return [{'name': 'mine', 'display_name': 'charles_q', 'state': 'QUEUEING'}]
        if path.endswith('trainingJobs'):
            return [{'name': 'mine', 'display_name': 'charles_q', 'state': 'QUEUEING'}]
        return []
    monkeypatch.setattr(collector, 'paginate', pages)
    result = await collector.collect()
    assert result['source_status']['CCI']['status'] == 'ok'
    assert result['source_status']['ACP']['status'] == 'ok'
    assert result['waiting_tasks'][0]['can_stop']
