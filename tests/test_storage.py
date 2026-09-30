"""Read-only AFS inventory: site isolation, pagination, and unknown measurements."""
import json
import httpx
import pytest
from server.storage import StorageManager, capacity_bytes
from server.collector import SourceError
from server.sites import MAOSHANWANG, SENSECORE


def volume(site, uid='vol', zone=None):
    zone=zone or ('cn-sh-01a' if site.id=='sensecore' else 'ms-sc-01a')
    return {'id':uid,'name':'afs-'+uid,'display_name':'Shared AFS','state':'ACTIVE',
            'zone':zone,'type':'storage.afs.v2.volume',
            'rid':site.workspace.split('/zones/')[0]+'/zones/'+zone+'/virtualVolumes/afs-'+uid,
            'properties':json.dumps({'resources':{'billing_items':{'capacity':2,'capacity_unit':'TB'}}}),
            'order_info':{'end_time':'2026-12-31T00:00:00Z','secret':'private'}}


def job(site, kind, name, date):
    return {'id':site.workspace+('/trainingJobs/' if kind=='ACP' else '/apps/')+name,
            'name':name,'display_name':'charles '+name,'create_time':date,'state':'STOPPED',
            'mount':[{'type':'PV_AFS','id':'vol','zone':volume(site)['zone'],'subdir':'/charles','mount_path':'/mnt/afs'}],
            'env':{'secret':'private'},'roles':[{'command':'private'}],
            **({'template':{'containers':[{'volume_mounts':[{'type':'PV_AFS','id':'vol','zone':volume(site)['zone'],'subdir':'/charles','mount_path':'/mnt/afs'}]}]}} if kind=='CCI' else {})}


@pytest.mark.parametrize('value,unit,expected',[(0,'KB',0),(1.5,'TB',1.5e12),(2,'GB',2e9),(2,'GiB',2*1024**3),(None,'GB',None),(-1,'GB',None),('nan','GB',None),(True,'TB',None),(1,'unknown',None)])
def test_capacity(value,unit,expected):
    assert capacity_bytes(value,unit)==expected


@pytest.mark.asyncio
@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE])
async def test_pages_safe_fields_mounts_and_site_credentials(site):
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.headers['authorization']==site.id
        if request.url.path.endswith('resources:page'):
            assert request.method=='POST' and request.url.host==httpx.URL(site.services['MANAGEMENT']).host
            if request.url.params['page_token']=='1':
                foreign=volume(SENSECORE if site==MAOSHANWANG else MAOSHANWANG,'foreign')
                return httpx.Response(200,json={'resources':[volume(site),foreign], 'next_page_token':'2'})
            return httpx.Response(200,json={'resources':[volume(site,'zero'),volume(site,'unknown')]})
        if request.url.path.endswith('running_status'):
            assert request.method=='POST' and request.url.host==httpx.URL(site.services['AFS']).host
            assert set(json.loads(request.content)['volume_ids'].split(';'))=={'vol','zero','unknown'}
            return httpx.Response(200,json={'volume_running_status':[{'volume_id':'vol','used_capacity':.5,'unit':'TB'}, {'volume_id':'zero','used_capacity':0,'unit':'KB'}]})
        kind='ACP' if 'trainingJobsOwn' in request.url.path else 'CCI'
        assert request.method=='GET' and site.workspace in request.url.path
        rows=[job(site,kind,'new','2026-09-20T00:00:00Z'),job(site,kind,'old','2025-01-01T00:00:00Z')]
        rows.append({**rows[0],'display_name':'other user','name':'other'})
        return httpx.Response(200,json={('training_jobs' if kind=='ACP' else 'apps'):rows})
    manager=StorageManager(site,transport=httpx.MockTransport(handler),session_loader=lambda:{'headers':{'authorization':site.id}})
    await manager.snapshot()
    await manager.mount_task
    data=await manager.snapshot()
    assert len(data['volumes'])==3
    v=next(v for v in data['volumes'] if v['id']=='vol')
    assert v['total_bytes']==2e12 and v['used_bytes']==.5e12
    assert v['free_bytes']==1.5e12 and v['used_percent']==25
    assert len(v['mounts'])==2 and all(m['task_name']=='charles new' for m in v['mounts'])
    assert next(v for v in data['volumes'] if v['id']=='zero')['used_bytes']==0
    assert next(v for v in data['volumes'] if v['id']=='unknown')['used_bytes'] is None
    assert 'private' not in json.dumps(data) and 'order_info' not in json.dumps(data)
    before=len(calls)
    assert await manager.snapshot()==data and len(calls)==before


@pytest.mark.asyncio
async def test_repeated_page_is_an_error():
    async def handler(request):
        return httpx.Response(200,json={'resources':[volume(SENSECORE)],'next_page_token':'1'})
    manager=StorageManager(SENSECORE,transport=httpx.MockTransport(handler),session_loader=lambda:{'headers':{}})
    with pytest.raises(SourceError):await manager.snapshot()


@pytest.mark.asyncio
async def test_usage_or_mount_denied_stays_unknown_and_partial():
    def handler(request):
        if request.url.path.endswith('resources:page'):return httpx.Response(200,json={'resources':[volume(SENSECORE)]})
        return httpx.Response(403,json={})
    manager=StorageManager(SENSECORE,transport=httpx.MockTransport(handler),session_loader=lambda:{'headers':{}})
    await manager.snapshot()
    await manager.mount_task
    data=await manager.snapshot()
    assert data['status']=='partial' and data['warnings']
    assert data['volumes'][0]['used_bytes'] is None and data['volumes'][0]['free_bytes'] is None
    assert data['mount_status']=={'CCI':'error','ACP':'error'}


@pytest.mark.asyncio
async def test_empty_final_page_terminates_even_if_server_advances_token():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'resources':[],'next_page_token':'2'})
    manager=StorageManager(SENSECORE,transport=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=manager.transport) as client:
        assert await manager.volumes(client)==[]
    assert len(calls)==1


@pytest.mark.asyncio
async def test_capacity_does_not_wait_for_slow_mounts_and_reuses_mount_cache(monkeypatch):
    import asyncio
    manager=StorageManager(MAOSHANWANG,session_loader=lambda:{'headers':{}})
    entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def volumes(client):return [{'id':'vol','zone':'ms-sc-01a','name':'shared','mounts':[]}]
    async def usage(client,volumes,warnings):volumes[0]['used_bytes']=123
    async def mounts(client,volumes,warnings):
        calls.append('mounts');entered.set();await release.wait()
        volumes[0]['mounts']=[{'kind':'ACP','task_name':'charles test'}]
        return {'CCI':'ok','ACP':'ok'}
    monkeypatch.setattr(manager,'volumes',volumes);monkeypatch.setattr(manager,'usage',usage);monkeypatch.setattr(manager,'mounts',mounts)
    try:
        data=await asyncio.wait_for(manager.snapshot(),.5)
        assert data['volumes'][0]['used_bytes']==123 and data['mount_refreshing']
        assert data['mount_status']['ACP']=='loading'
        await entered.wait()
        assert (await manager.snapshot())['mount_refreshing'] and len(calls)==1
        release.set();await manager.mount_task
        data=await manager.snapshot()
        assert not data['mount_refreshing'] and data['volumes'][0]['mounts'][0]['task_name']=='charles test'
        manager.cached_at-=31  # Capacity refresh must not repeat the slow history scan.
        assert (await manager.snapshot())['mount_updated_at']==data['mount_updated_at']
        assert len(calls)==1
    finally:
        release.set()
        if hasattr(manager,'close'):await manager.close()


@pytest.mark.asyncio
async def test_failed_background_refresh_keeps_mount_records_but_not_freshness(monkeypatch):
    manager=StorageManager(MAOSHANWANG,session_loader=lambda:{'headers':{}})
    fail=False
    async def volumes(client):return [{'id':'vol','zone':'ms-sc-01a','name':'shared','mounts':[]}]
    async def usage(*args):pass
    async def mounts(client,volumes,warnings):
        if fail:
            warnings.append('ACP 挂载关系读取失败，已有记录可能过期。')
            return {'CCI':'ok','ACP':'error'}
        volumes[0]['mounts']=[{'kind':'ACP','task_name':'charles test'}]
        return {'CCI':'ok','ACP':'ok'}
    monkeypatch.setattr(manager,'volumes',volumes);monkeypatch.setattr(manager,'usage',usage);monkeypatch.setattr(manager,'mounts',mounts)
    await manager.snapshot();await manager.mount_task
    before=await manager.snapshot()
    fail=True;manager.mount_checked_at-=301
    assert (await manager.snapshot())['mount_refreshing']
    await manager.mount_task
    data=await manager.snapshot()
    assert data['volumes'][0]['mounts']==before['volumes'][0]['mounts']
    assert data['mount_updated_at']==before['mount_updated_at']
    assert data['mount_status']['ACP']=='error' and data['status']=='partial'
    assert data['warnings'] and not data['mount_refreshing']
    await manager.close()


@pytest.mark.asyncio
async def test_shutdown_cancels_mount_reader(monkeypatch):
    import asyncio
    manager=StorageManager(MAOSHANWANG,session_loader=lambda:{'headers':{}})
    entered=asyncio.Event()
    async def volumes(client):return []
    async def usage(*args):pass
    async def mounts(*args):entered.set();await asyncio.Event().wait()
    monkeypatch.setattr(manager,'volumes',volumes);monkeypatch.setattr(manager,'usage',usage);monkeypatch.setattr(manager,'mounts',mounts)
    await manager.snapshot();await entered.wait()
    await manager.close()
    assert manager.mount_task.cancelled()
