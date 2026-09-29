"""SenseCore templates and mounts must stay bound to their selected AFS partition."""
import copy
import json
from dataclasses import replace
import httpx
import pytest
from server.acp_actions import ACPManager,ActionError
from server.sites import SENSECORE
from server.config import WORKSPACE
from server.store import Store
from tests.test_acp_actions import Cloud,job


@pytest.fixture
def partition_setup(tmp_path):
    cloud=Cloud()
    a=job('a-new','charles_a','2026-09-29T00:00:00Z')
    b=job('b-new','charles_b','2026-09-28T00:00:00Z')
    b['mount']=[{**b['mount'][0],'id':'b-disk','zone':'b','mount_path':'/b','subdir':'/charles'}]
    b['roles'][0]['image_path']='private/image:b'
    b['resource_pool'].update(name='b-cluster',zone='b')
    older=copy.deepcopy(a);older.update(name='a-running',id=WORKSPACE+'/trainingJobs/a-running',state='RUNNING',create_time='2026-09-27T00:00:00Z')
    no_mount=copy.deepcopy(b);no_mount.update(name='b-empty',id=WORKSPACE+'/trainingJobs/b-empty',create_time='2026-09-30T00:00:00Z');no_mount['mount']=[]
    foreign=copy.deepcopy(b);foreign.update(name='foreign',id=WORKSPACE+'/trainingJobs/foreign',display_name='someone',create_time='2026-10-01T00:00:00Z')
    cloud.jobs=[a,b,older,no_mount,foreign]
    cloud.bindings=[cloud.bindings[0]]
    for name,zone in [('b-cluster','b'),('e-cluster','e')]:
        binding=copy.deepcopy(cloud.bindings[0]);binding.update(name=name,display_name=name,id=WORKSPACE.split('/zones/')[0]+'/zones/'+zone+'/aec2s/'+name)
        cloud.bindings.append(binding)
    requests=[]
    async def transport(request):
        requests.append(request)
        if '/crossAttach' in request.url.path:
            zone=request.url.path.split('/zones/')[1].split('/')[0]
            assert request.url.params['source_zone']==zone
            return httpx.Response(200,json={'zones':['z'] if zone=='e' else []})
        return await cloud(request)
    site=replace(SENSECORE,workspace=WORKSPACE)
    m=ACPManager(Store(tmp_path/'db'),site=site,transport=httpx.MockTransport(transport),session_loader=lambda:{'headers':{}})
    return m,cloud,requests


@pytest.mark.asyncio
async def test_options_select_latest_complete_template_per_afs_partition(partition_setup):
    m,c,requests=partition_setup
    a=await m.options()
    assert {p['id'] for p in a['partitions']}=={'z','b'}
    assert a['partition']['id']=='z' and a['template']['id']=='ACP:a-new'
    assert a['template_config']['images']==['private/image:v13']
    assert a['template_config']['afs_mounts'][0]['mount_path']=='/mnt/data'
    assert {r['id'] for r in a['clusters']}=={'modeltoolchain-test','e-cluster'}
    b=await m.options(partition_id='b')
    assert b['template']['id']=='ACP:b-new'
    assert b['template_config']['images']==['private/image:b']
    assert b['template_config']['afs_mounts'][0]['mount_path']=='/b'
    assert [r['id'] for r in b['clusters']]==['b-cluster']
    assert b['default_command']=='sleep inf'
    assert all(r.method=='GET' for r in requests)
    assert 'private-env' not in json.dumps(b) and 'SECRET' not in json.dumps(b)


@pytest.mark.asyncio
async def test_preview_payload_keeps_selected_partition_image_and_all_mounts(partition_setup):
    m,c,_=partition_setup
    p=await m.preview({'display_name':'charles_copy','replicas':1,'gpus_per_replica':1,'command':'','partition_id':'b','cluster_id':'b-cluster'})
    assert p['partition']['id']=='b' and p['template']['id']=='ACP:b-new'
    assert p['template_config']['afs_mounts'][0]['mount_path']=='/b'
    await m.create(p['preview_id'])
    payload=json.loads(c.writes[0].content)
    original=next(r for r in c.jobs if r['name']=='b-new')
    assert payload['mount']==original['mount'] and payload['env']==original['env']
    assert payload['roles'][0]['image_path']=='private/image:b'
    assert payload['resource_pool']['name']=='b-cluster'


@pytest.mark.asyncio
async def test_partition_mismatch_unknown_and_cross_attach_guards(partition_setup):
    m,c,_=partition_setup
    base={'display_name':'charles_copy','replicas':1,'gpus_per_replica':1,'command':''}
    for data in ({'partition_id':'missing'},{'partition_id':'b','cluster_id':'modeltoolchain-test'},{'partition_id':'z','cluster_id':'b-cluster'}):
        with pytest.raises(ActionError):await m.preview({**base,**data})
    p=await m.preview({**base,'partition_id':'z','cluster_id':'e-cluster'})
    assert p['cluster']['id']=='e-cluster'
    assert m.previews[p['preview_id']]['payload']['mount']==c.jobs[0]['mount']
    assert not c.writes


@pytest.mark.asyncio
async def test_cross_attach_missing_rules_cannot_prepare_cloud_write(partition_setup):
    m,c,_=partition_setup
    original=m.reader.request
    async def read(client,service,path,**kwargs):
        if service=='AFS':return {'zones':None}
        return await original(client,service,path,**kwargs)
    m.reader.request=read
    with pytest.raises(ActionError) as caught:
        await m.preview({'display_name':'charles_copy','replicas':1,'gpus_per_replica':1,'command':'','partition_id':'z','cluster_id':'e-cluster'})
    assert caught.value.code=='resources_unavailable'
    assert not c.writes and not m.previews


@pytest.mark.asyncio
async def test_maoshanwang_cannot_accept_partition_selection(tmp_path):
    cloud=Cloud()
    m=ACPManager(Store(tmp_path/'db'),transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{}})
    with pytest.raises(ActionError):await m.options(partition_id='b')
    with pytest.raises(ActionError):await m.preview({'display_name':'charles_copy','replicas':1,'gpus_per_replica':1,'command':'','partition_id':'b'})
    options=await m.options()
    assert 'partitions' not in options and 'template_config' not in options
    assert options['cluster_order']==['modeltoolchain_test','modeltoolchain','vigen']
    assert not cloud.writes


@pytest.mark.asyncio
async def test_default_is_a_even_when_b_has_a_newer_template(partition_setup):
    m,c,_=partition_setup
    for row in c.jobs:
        for mount in row.get('mount',[]):
            if mount['zone']=='z':mount['zone']='cn-sh-01a'
    c.jobs[1]['create_time']='2026-10-02T00:00:00Z'
    async with m.client() as client:
        default,_=await m.template_context(client)
        selected,_=await m.template_context(client,partition_id='b')
    assert default['name']=='a-new'
    assert selected['name']=='b-new'
    assert not c.writes
