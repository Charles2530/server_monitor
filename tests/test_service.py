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
