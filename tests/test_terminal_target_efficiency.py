"""Concurrent terminal reads share cloud work without sharing callers' state."""
import asyncio
import json

import httpx
import pytest

from server.acp_actions import ActionError
from server.sites import MAOSHANWANG, SENSECORE
from tests.test_cci_terminal import Cloud as CCICloud
from tests.test_acp_terminal import Cloud as ACPCloud


@pytest.fixture(params=['CCI', 'ACP'])
def subject(request):
    from server.cci_terminal import CCITerminal
    from server.acp_terminal import ACPTerminal
    kind = request.param
    cloud = CCICloud(MAOSHANWANG) if kind == 'CCI' else ACPCloud(MAOSHANWANG)
    cls = CCITerminal if kind == 'CCI' else ACPTerminal
    task = 'CCI:app-test' if kind == 'CCI' else 'ACP:pt-test'
    return kind, cloud, cls, task


@pytest.mark.asyncio
async def test_eight_overlapping_requests_use_one_cloud_read_pair(subject):
    kind, cloud, cls, task = subject
    started, release = asyncio.Event(), asyncio.Event()
    async def respond(request):
        started.set()
        await release.wait()
        return await cloud(request)
    manager = cls(MAOSHANWANG, transport=httpx.MockTransport(respond), session_loader=lambda:{'headers':{}})
    jobs = [asyncio.create_task(manager.targets(task)) for _ in range(8)]
    try:
        await asyncio.wait_for(started.wait(),1)
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*jobs)
        assert len(cloud.requests) == 2
        results[0]['targets'].clear()
        assert all(result['targets'] for result in results[1:])
        assert (await manager.targets(task,reuse=True))['targets']
        assert len(cloud.requests) == 2
        await manager.targets(task)
        assert len(cloud.requests) == 4, 'a subsequent explicit refresh must still read cloud'
    finally:
        release.set()
        for job in jobs:job.cancel()
        await asyncio.gather(*jobs,return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_one_waiter_does_not_cancel_shared_cloud_read(subject):
    kind, cloud, cls, task = subject
    started, release = asyncio.Event(), asyncio.Event()
    cancelled = []
    async def respond(request):
        started.set()
        try:await release.wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return await cloud(request)
    manager = cls(MAOSHANWANG, transport=httpx.MockTransport(respond), session_loader=lambda:{'headers':{}})
    first = asyncio.create_task(manager.targets(task))
    second = asyncio.create_task(manager.targets(task))
    try:
        await asyncio.wait_for(started.wait(),1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        assert not cancelled
        release.set()
        assert (await second)['targets']
        assert len(cloud.requests) == 2
    finally:
        release.set()
        for job in (first,second):job.cancel()
        await asyncio.gather(first,second,return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_all_waiters_drains_request_and_next_read_retries(subject):
    kind, cloud, cls, task = subject
    started, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def respond(request):
        started.set()
        try:await release.wait()
        finally:stopped.set()
        return await cloud(request)
    manager = cls(MAOSHANWANG, transport=httpx.MockTransport(respond), session_loader=lambda:{'headers':{}})
    jobs = [asyncio.create_task(manager.targets(task)) for _ in range(2)]
    await asyncio.wait_for(started.wait(),1)
    for job in jobs:job.cancel()
    await asyncio.gather(*jobs,return_exceptions=True)
    assert stopped.is_set()
    assert not manager.target_cache.pending
    release.set()
    assert (await manager.targets(task))['targets']
    assert len(cloud.requests) == 2


@pytest.mark.asyncio
async def test_new_session_cannot_use_old_target_cache(subject):
    kind, cloud, cls, task = subject
    headers = {'authorization':'old-session'}
    manager = cls(MAOSHANWANG, transport=httpx.MockTransport(cloud), session_loader=lambda:{'headers':dict(headers)})
    await manager.targets(task)
    headers['authorization'] = 'new-session'
    if kind == 'CCI':cloud.app['state']='STOPPED'
    else:cloud.job['state']='STOPPED'
    with pytest.raises(ActionError):await manager.targets(task,reuse=True)
    assert len(cloud.requests) == 3


@pytest.mark.asyncio
async def test_failed_explicit_refresh_invalidates_previous_cache(subject):
    kind, cloud, cls, task = subject
    manager = cls(MAOSHANWANG, transport=httpx.MockTransport(cloud), session_loader=lambda:{'headers':{}})
    await manager.targets(task)
    if kind == 'CCI':cloud.app['state']='STOPPED'
    else:cloud.job['state']='STOPPED'
    with pytest.raises(ActionError):await manager.targets(task)
    with pytest.raises(ActionError):await manager.targets(task,reuse=True)
    assert len(cloud.requests) == 4


@pytest.mark.asyncio
async def test_cci_address_and_public_results_never_cross_cache_variants():
    from server.cci_terminal import CCITerminal
    cloud=CCICloud(MAOSHANWANG)
    manager=CCITerminal(MAOSHANWANG,transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{}})
    private=await manager.targets('CCI:app-test',include_address=True)
    assert private['targets'][0]['address']=='PRIVATE'
    public=await manager.targets('CCI:app-test',reuse=True)
    assert 'PRIVATE' not in json.dumps(public)
    assert (await manager.targets('CCI:app-test',include_address=True,reuse=True))['targets'][0]['address']=='PRIVATE'
    assert len(cloud.requests)==4


@pytest.mark.asyncio
async def test_cache_is_bounded_and_expiry_forces_a_new_read(monkeypatch):
    from server.terminal_target_cache import TerminalTargetCache
    cache=TerminalTargetCache(ttl=8,limit=2)
    clock=[100.0]
    monkeypatch.setattr('server.terminal_target_cache.time.monotonic',lambda:clock[0])
    calls=[]
    async def read():
        calls.append(True)
        return {'targets':[{'instance':'worker'}]}
    for key in ('a','b','c'):await cache.resolve(key,{},read)
    assert len(cache.entries)==2
    await cache.resolve('a',{},read,reuse=True)
    assert len(calls)==4, 'evicted entry must be read again'
    await cache.resolve('a',{},read,reuse=True)
    assert len(calls)==4
    clock[0]+=8
    await cache.resolve('a',{},read,reuse=True)
    assert len(calls)==5, 'TTL boundary must not extend across reads'


@pytest.mark.asyncio
async def test_failed_shared_read_is_not_cached_and_all_waiters_receive_error():
    from server.terminal_target_cache import TerminalTargetCache
    cache=TerminalTargetCache(8)
    started,release=asyncio.Event(),asyncio.Event()
    calls=[]
    async def fail():
        calls.append(True);started.set();await release.wait()
        raise ActionError('failed')
    jobs=[asyncio.create_task(cache.resolve('a',{},fail)) for _ in range(3)]
    await asyncio.wait_for(started.wait(),1)
    release.set()
    results=await asyncio.gather(*jobs,return_exceptions=True)
    assert all(isinstance(value,ActionError) for value in results)
    assert len(calls)==1
    with pytest.raises(ActionError):await cache.resolve('a',{},fail,reuse=True)
    assert len(calls)==2


@pytest.mark.asyncio
async def test_changed_login_does_not_join_previous_pending_request(subject):
    kind,cloud,cls,task=subject
    headers={'authorization':'old'}
    started,release=asyncio.Event(),asyncio.Event()
    contexts=[]
    async def respond(request):
        contexts.append(request.headers['authorization'])
        if request.headers['authorization']=='old':
            started.set();await release.wait()
        return await cloud(request)
    manager=cls(MAOSHANWANG,transport=httpx.MockTransport(respond),session_loader=lambda:{'headers':dict(headers)})
    first=asyncio.create_task(manager.targets(task))
    try:
        await asyncio.wait_for(started.wait(),1)
        headers['authorization']='new'
        assert (await asyncio.wait_for(manager.targets(task,reuse=True),1))['targets']
        assert contexts.count('new')==2
    finally:
        release.set()
        await first
    assert contexts.count('old')==2
