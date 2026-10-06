import asyncio
import pytest
from server.service import Monitor
from server.store import Store
from server.auth import AuthRequired

class Fake:
    def __init__(self): self.calls=0; self.fail=False
    async def collect(self, previous):
        self.calls+=1
        await asyncio.sleep(.01)
        if self.fail: raise AuthRequired('expired')
        return {'tasks':[{'id':'CCI:a','series':[]}], 'clusters':[], 'status':'ok','connected':True,'updated_at':'2026-09-29T00:00:00Z','source_status':{},'error':None}

@pytest.mark.asyncio
async def test_no_overlap_and_failure_retains_snapshot(tmp_path):
    fake=Fake(); monitor=Monitor(Store(tmp_path/'db'),fake)
    await asyncio.gather(monitor.refresh(),monitor.refresh())
    assert fake.calls==1 and monitor.state['status']=='ok'
    fake.fail=True
    await monitor.refresh()
    assert monitor.state['tasks'][0]['id']=='CCI:a'
    assert not monitor.state['connected'] and monitor.state['status']=='disconnected'

@pytest.mark.asyncio
async def test_history_not_served_as_fresh_on_restart(tmp_path):
    store=Store(tmp_path/'db'); fake=Fake(); monitor=Monitor(store,fake)
    await monitor.refresh()
    restarted=Monitor(store,fake)
    assert restarted.state['status']=='collecting'

@pytest.mark.asyncio
async def test_auth_renewal_recovers_once_and_throttles(tmp_path):
    fake=Fake();fake.fail=True;calls=[]
    async def renew(**kwargs):
        calls.append(kwargs);fake.fail=False
    monitor=Monitor(Store(tmp_path/'db'),fake,renew=renew)
    await monitor.refresh()
    assert monitor.state['status']=='ok' and len(calls)==1
    fake.fail=True
    await monitor.refresh()
    assert monitor.state['status']=='disconnected' and len(calls)==1

@pytest.mark.asyncio
async def test_failed_renewal_requires_login_without_leaking_error(tmp_path):
    fake=Fake();fake.fail=True
    async def renew(**kwargs): raise RuntimeError('sensitive upstream content')
    monitor=Monitor(Store(tmp_path/'db'),fake,renew=renew)
    await monitor.refresh()
    assert monitor.state['status']=='disconnected'
    assert 'sensitive' not in monitor.state['error']


@pytest.mark.asyncio
async def test_manual_reconnect_uses_background_credentials_and_waits_for_collector(tmp_path):
    calls=[]
    async def renew(**kwargs):calls.append(kwargs)
    monitor=Monitor(Store(tmp_path/'db'),Fake(),renew=renew)
    await monitor.lock.acquire()
    connecting=asyncio.create_task(monitor.connect())
    await asyncio.sleep(.01)
    assert monitor.state['connecting'] and not calls
    monitor.lock.release()
    await connecting
    assert calls==[{'headless':True,'timeout':75}]
    assert monitor.state['connected'] and not monitor.state['connecting']


@pytest.mark.asyncio
async def test_reconnect_timeout_releases_monitor_while_browser_cleanup_stalls(tmp_path, monkeypatch, caplog):
    from server import service
    monkeypatch.setattr(service, 'CONNECT_TIMEOUT', .04, raising=False)
    monkeypatch.setattr(service, 'RENEW_TIMEOUT', .02, raising=False)
    cleanup = asyncio.Event()
    entered = asyncio.Event()
    async def renew(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await cleanup.wait()
    monitor = Monitor(Store(tmp_path/'db'), Fake(), renew=renew)
    task = asyncio.create_task(monitor.connect())
    try:
        await entered.wait()
        done, _ = await asyncio.wait({task}, timeout=.2)
        assert task in done, 'Browser cleanup must not keep reconnect or the collection lock blocked'
        assert not monitor.lock.locked() and not monitor.state['connecting']
        assert monitor.state['status'] == 'disconnected'
        assert '超时' in monitor.state['error']
        assert 'timeout' in caplog.text
        assert await monitor.refresh()
        assert monitor.state['status'] == 'ok'
    finally:
        cleanup.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await monitor.close()


@pytest.mark.asyncio
async def test_reconnect_waiting_for_collector_has_deadline(tmp_path, monkeypatch):
    from server import service
    monkeypatch.setattr(service, 'CONNECT_TIMEOUT', .02, raising=False)
    calls = []
    async def renew(**kwargs): calls.append(kwargs)
    monitor = Monitor(Store(tmp_path/'db'), Fake(), renew=renew)
    monitor.state.update(status='collecting', connected=True)
    await monitor.lock.acquire()
    task = asyncio.create_task(monitor.connect())
    try:
        done, _ = await asyncio.wait({task}, timeout=.15)
        assert task in done, 'Waiting for another collector must also be bounded'
        assert monitor.lock.locked() and not calls
        assert not monitor.state['connecting']
        assert monitor.state['status'] == 'collecting' and monitor.state['connected']
    finally:
        monitor.lock.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_automatic_renewal_visible_and_cancellation_clears_state(tmp_path):
    entered = asyncio.Event()
    async def renew(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    fake = Fake(); fake.fail = True
    monitor = Monitor(Store(tmp_path/'db'), fake, renew=renew)
    task = asyncio.create_task(monitor.refresh())
    try:
        await entered.wait()
        assert monitor.state['connecting']
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await monitor.close()
    assert not monitor.state['connecting'] and not monitor.lock.locked()
    assert monitor.state['status'] != 'collecting'


@pytest.mark.asyncio
async def test_manual_cancellation_clears_state_and_allows_retry(tmp_path):
    entered = asyncio.Event()
    async def renew(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    monitor = Monitor(Store(tmp_path/'db'), Fake(), renew=renew)
    task = asyncio.create_task(monitor.connect())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert not monitor.state['connecting'] and not monitor.lock.locked()
    assert monitor.state['status'] == 'disconnected'
    await monitor.close()
    assert await monitor.refresh()


@pytest.mark.asyncio
async def test_renewal_logs_stage_site_and_type_without_secrets(tmp_path, caplog):
    from types import SimpleNamespace
    fake = Fake(); fake.fail = True
    fake.site = SimpleNamespace(id='maoshanwang')
    async def renew(**kwargs): raise RuntimeError('SECRET password=foo Authorization=bar')
    monitor = Monitor(Store(tmp_path/'db'), fake, renew=renew)
    await monitor.refresh()
    assert 'stage=renew' in caplog.text and 'site=maoshanwang' in caplog.text
    assert 'RuntimeError' in caplog.text
    assert 'SECRET' not in caplog.text and 'password' not in caplog.text and 'Authorization' not in caplog.text


@pytest.mark.asyncio
async def test_refresh_requests_report_busy_and_reserve_before_scheduling(tmp_path):
    monitor = Monitor(Store(tmp_path/'db'), Fake())
    first = monitor.request_refresh()
    second = monitor.request_refresh()
    assert first['status'] == 'started' and first['started']
    assert second['status'] == 'collecting' and not second['started']
    await asyncio.gather(*monitor.jobs)
    assert monitor.collector.calls == 1
    monitor.state['connecting'] = True
    assert monitor.request_refresh()['status'] == 'reconnecting'
    monitor.state['connecting'] = False
    await monitor.lock.acquire()
    try: assert monitor.request_refresh()['status'] == 'collecting'
    finally: monitor.lock.release()


@pytest.mark.asyncio
async def test_manual_login_does_not_trigger_second_login_on_failed_followup(tmp_path):
    calls = []
    async def renew(**kwargs): calls.append(kwargs)
    fake = Fake(); fake.fail = True
    monitor = Monitor(Store(tmp_path/'db'), fake, renew=renew)
    await monitor.connect()
    assert len(calls) == 1
    assert monitor.state['status'] == 'disconnected'
    assert not monitor.state['connecting']


@pytest.mark.asyncio
async def test_cancel_before_connect_starts_releases_reservation(tmp_path):
    async def renew(**kwargs): pass
    monitor = Monitor(Store(tmp_path/'db'), Fake(), renew=renew)
    assert monitor.request_connect()['started']
    monitor.connecting.cancel()
    await asyncio.gather(monitor.connecting, return_exceptions=True)
    assert not monitor.state['connecting']
    assert monitor.request_connect()['started']
    await monitor.connecting
    assert monitor.state['status'] == 'ok'
