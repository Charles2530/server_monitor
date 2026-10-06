"""Independent resource reads overlap without weakening validation or cleanup."""
import asyncio

import pytest

from server.acp_actions import ACPManager, ActionError
from server.auth import AuthRequired
from server.inventory import read_allocation
from server.sites import MAOSHANWANG, SENSECORE
from tests.test_acp_actions import spec
from tests.test_inventory import allocation_node, observation


def catalog_fixture(site=MAOSHANWANG, count=4):
    manager=ACPManager(None,site=site,session_loader=lambda:{'headers':{}})
    names=list(site.cluster_priority or ('a','b','c'))+['extra']
    bindings=[{'name':name,'state':'ACTIVE','vpc_id':'vpc',
               'id':site.workspace.split('/zones/')[0]+'/zones/z/aec2s/'+name}
              for name in names[:count]]
    async def paginate(client,service,path,key):
        assert path=='/compute/workspace/data/v1'+site.workspace+'/workspaceAEC2Bindings'
        return bindings
    manager.reader.paginate=paginate
    return manager,bindings


class GatedReads:
    def __init__(self,count):
        self.started=[asyncio.Event() for _ in range(count)]
        self.release=[asyncio.Event() for _ in range(count)]
        self.finished=[asyncio.Event() for _ in range(count)]
        self.calls=[];self.completed=[];self.active=0;self.peak=0
        self.error=None

    async def read(self,index):
        self.calls.append(index);self.active+=1;self.peak=max(self.peak,self.active)
        self.started[index].set()
        try:
            await self.release[index].wait()
            if self.error is not None and index==0:raise self.error
            self.completed.append(index)
            self.finished[index].set()
        finally:
            # Cleanup itself can yield; the caller must drain it before returning.
            await asyncio.sleep(0)
            self.active-=1


def operation(kind,gates,site=MAOSHANWANG):
    count=len(gates.started)
    if kind=='catalog':
        manager,bindings=catalog_fixture(site,count)
        async def request(client,service,path):
            index=next(i for i,b in enumerate(bindings) if path.endswith(b['id']+'/resourceSpecs'))
            await gates.read(index)
            return {'resource_specs':[spec(index+1)]}
        manager.reader.request=request
        return manager.catalog(None,include_all=True,template={'mount':[]})
    nodes=[allocation_node(str(i)) for i in range(count*20)]
    async def request(client,service,path,params):
        assert path=='/compute/aec2/data/v1/fixture/observe'
        names=[uid for key,uid in params]
        assert all(key=='acn_uid' for key,uid in params) and len(names)==20
        index=int(names[0])//20
        assert names==[str(i) for i in range(index*20,(index+1)*20)]
        await gates.read(index)
        return {'observations':{'acns':[observation(uid,gpu=index+1) for uid in names]},'total_size':20}
    return read_allocation(request,None,{'id':'/fixture'},nodes,0)


async def first_three_started(gates):
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in gates.started[:3])),.5)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,site',[('catalog',MAOSHANWANG),('catalog',SENSECORE),('allocation',MAOSHANWANG)],
                         ids=['maoshanwang-catalog','sensecore-catalog','allocation'])
async def test_resource_reads_overlap_with_limit_three_and_keep_input_order(kind,site):
    gates=GatedReads(4);task=asyncio.create_task(operation(kind,gates,site))
    try:
        await first_three_started(gates)
        assert gates.calls==[0,1,2] and gates.peak==3
        gates.release[2].set()
        await asyncio.wait_for(gates.started[3].wait(),.5)
        gates.release[3].set()
        await asyncio.wait_for(gates.finished[3].wait(),.5)
        gates.release[1].set()
        await asyncio.wait_for(gates.finished[1].wait(),.5)
        gates.release[0].set()
        result=await asyncio.wait_for(task,.5)
        assert gates.completed==[2,3,1,0] and gates.peak==3 and gates.active==0
        if kind=='catalog':
            assert [specs[0]['device']['number'] for binding,specs in result[1]]==[1,2,3,4]
        else:
            assert result['status']=='ok'
            assert [row['free_gpus'] for row in result['nodes']]==[i//20+1 for i in range(80)]
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['catalog','allocation'])
@pytest.mark.parametrize('cancel',[False,True],ids=['error','cancel'])
async def test_resource_reads_drain_siblings_before_returning(kind,cancel):
    gates=GatedReads(3);task=asyncio.create_task(operation(kind,gates))
    try:
        await first_three_started(gates)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,.5)
        else:
            gates.error=AuthRequired('fixture permission expired');gates.release[0].set()
            if kind=='catalog':
                with pytest.raises(AuthRequired):await asyncio.wait_for(task,.5)
            else:
                result=await asyncio.wait_for(task,.5)
                assert result['status']=='unknown' and result['idle_gpu_groups'] is None
        assert gates.active==0 and not gates.completed
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_allocation_deadline_drains_inflight_reads(monkeypatch):
    monkeypatch.setattr('server.inventory.ALLOCATION_TIMEOUT_SECONDS',.1)
    gates=GatedReads(3);task=asyncio.create_task(operation('allocation',gates))
    try:
        await first_three_started(gates)
        result=await asyncio.wait_for(task,.5)
        assert result['status']=='unknown' and result['idle_gpu_groups'] is None
        assert gates.active==0
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE],ids=lambda site:site.id)
async def test_catalog_rejects_other_site_binding_before_resource_reads(site):
    manager,bindings=catalog_fixture(site)
    other=SENSECORE if site==MAOSHANWANG else MAOSHANWANG
    bindings[0]['id']=other.workspace.split('/zones/')[0]+'/zones/z/aec2s/'+bindings[0]['name']
    calls=[]
    async def request(*args,**kwargs):calls.append(args);return {'resource_specs':[spec(1)]}
    manager.reader.request=request
    with pytest.raises(ActionError):await manager.catalog(None,include_all=True,template={'mount':[]})
    assert not calls


@pytest.mark.asyncio
async def test_catalog_reuses_mount_check_before_parallel_specs():
    manager,bindings=catalog_fixture(SENSECORE,3)
    gates=GatedReads(3);mount_reads=[]
    async def request(client,service,path,params=None):
        if path.endswith('/crossAttach'):
            assert service=='AFS' and params=={'source_zone':'z'}
            mount_reads.append(path)
            return {'zones':['source']}
        assert len(mount_reads)==1
        index=next(i for i,b in enumerate(bindings) if path.endswith(b['id']+'/resourceSpecs'))
        await gates.read(index)
        return {'resource_specs':[spec(1)]}
    manager.reader.request=request
    task=asyncio.create_task(manager.catalog(None,template={'mount':[{'type':'PV_AFS','zone':'source'}]}))
    try:
        await first_three_started(gates)
        for release in gates.release:release.set()
        assert len((await asyncio.wait_for(task,.5))[1])==3
        assert len(mount_reads)==1
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_catalog_skips_mount_incompatible_clusters_before_spec_reads():
    manager,bindings=catalog_fixture(MAOSHANWANG)
    calls=[]
    async def request(*args,**kwargs):calls.append(args);return {'resource_specs':[spec(1)]}
    manager.reader.request=request
    with pytest.raises(ActionError):
        await manager.catalog(None,include_all=True,template={'mount':[{'type':'PV_AFS','zone':'other'}]})
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption',['missing','duplicate','count','pagination'])
async def test_allocation_one_invalid_batch_invalidates_whole_result(corruption):
    nodes=[allocation_node(str(i)) for i in range(60)]
    async def request(client,service,path,params):
        rows=[observation(uid) for key,uid in params]
        payload={'observations':{'acns':rows},'total_size':len(rows)}
        if params[0][1]=='20':
            if corruption=='missing':rows.pop()
            if corruption=='duplicate':rows[-1]=rows[0]
            if corruption=='count':payload['total_size']+=1
            if corruption=='pagination':payload['next_page_token']='next'
        return payload
    result=await read_allocation(request,None,{'id':'/fixture'},nodes,0)
    assert result['status']=='unknown' and result['nodes']==[] and result['idle_gpu_groups'] is None
