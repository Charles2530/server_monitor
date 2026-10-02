"""Replacement must be accepted before releasing the original ACP allocation."""
import asyncio,json
import httpx
import pytest
from server.acp_actions import ACPManager,ActionError
from test_acp_actions import setup

async def preview(m):
    options=await m.restart_options('ACP:pt-template',release=True)
    return await m.restart_preview({'task_id':'ACP:pt-template','revision':options['revision'],'command':'python retry.py'},release=True)

def restored(m,c,store):
    return ACPManager(store,transport=httpx.MockTransport(c),session_loader=lambda:{'headers':{}})

@pytest.mark.asyncio
async def test_release_restart_orders_writes_keeps_config_and_deduplicates(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING'
    p=await preview(m);assert p['release_restart'] and not c.writes
    first,second=await asyncio.gather(m.create(p['preview_id']),m.create(p['preview_id']))
    assert first==second and first['release_restart'] and first['original_id']=='ACP:pt-template'
    assert len(c.writes)==2 and not c.writes[0].url.path.endswith(':stop') and c.writes[1].url.path.endswith('/pt-template:stop')
    body=json.loads(c.writes[0].content)
    assert body['display_name']=='charles_latest' and body['name']!='pt-template'
    assert body['roles'][0]['startup_script']=='python retry.py\nsleep inf'
    for key in ('resource_pool','mount','env','framework','scheduling'):assert body[key]==c.jobs[0][key]
    assert (await restored(m,c,store).create(p['preview_id']))==first and len(c.writes)==2
    assert 'python retry.py' not in json.dumps(store.operation(p['preview_id']))

@pytest.mark.asyncio
async def test_rejected_replacement_never_stops_original(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m);c.reject=True
    with pytest.raises(ActionError):await m.create(p['preview_id'])
    with pytest.raises(ActionError):await restored(m,c,store).create(p['preview_id'])
    assert len(c.writes)==1 and c.jobs[0]['state']=='RUNNING'

@pytest.mark.asyncio
async def test_unknown_replacement_never_stops_or_reposts_until_verified(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m);c.timeout=True
    with pytest.raises(ActionError):await m.create(p['preview_id'])
    with pytest.raises(ActionError):await restored(m,c,store).create(p['preview_id'])
    assert len(c.writes)==1 and c.jobs[0]['state']=='RUNNING'
    payload=json.loads(c.writes[0].content)
    c.jobs.append({**payload,'id':m.workspace+'/trainingJobs/'+payload['name'],'state':'WAIT_QUOTA'})
    c.timeout=False
    result=await restored(m,c,store).create(p['preview_id'])
    assert result['release_restart'] and len(c.writes)==2 and c.jobs[0]['state']=='SUSPENDING'

@pytest.mark.asyncio
async def test_stop_timeout_recovers_without_repeating_either_write(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m)
    original=c.__call__
    async def cloud(request):
        if request.method=='POST' and request.url.path.endswith(':stop'):
            c.timeout=True
        return await original(request)
    m.transport=httpx.MockTransport(cloud)
    with pytest.raises(ActionError) as error:await m.create(p['preview_id'])
    assert error.value.code=='release_incomplete' and len(c.writes)==2
    c.timeout=False
    with pytest.raises(ActionError):await restored(m,c,store).create(p['preview_id'])
    assert len(c.writes)==2
    c.jobs[0]['state']='SUSPENDED'
    assert (await restored(m,c,store).create(p['preview_id']))['release_restart']
    assert len(c.writes)==2

@pytest.mark.asyncio
async def test_replacement_terminal_failure_keeps_original_and_reports_partial(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m)
    original=c.__call__
    async def cloud(request):
        response=await original(request)
        if request.method=='POST':c.jobs[-1]['state']='FAILED'
        return response
    m.transport=httpx.MockTransport(cloud)
    with pytest.raises(ActionError) as error:await m.create(p['preview_id'])
    assert error.value.code=='release_replacement_ended' and c.jobs[0]['state']=='RUNNING' and len(c.writes)==1
    assert store.operation(p['preview_id'])['status']=='failed'

@pytest.mark.asyncio
async def test_only_running_source_and_no_duplicate_release_workflows(setup):
    m,c,_=setup
    with pytest.raises(ActionError):await m.restart_options('ACP:pt-template',release=True)
    c.jobs[0]['state']='RUNNING';p=await preview(m);p2=await preview(m)
    c.timeout=True
    with pytest.raises(ActionError):await m.create(p['preview_id'])
    with pytest.raises(ActionError):await m.create(p2['preview_id'])
    assert len(c.writes)==1

@pytest.mark.asyncio
async def test_source_change_before_submission_prevents_both_writes(setup):
    m,c,_=setup;c.jobs[0]['state']='RUNNING';p=await preview(m)
    c.jobs[0]['roles'][0]['image_path']='changed'
    with pytest.raises(ActionError):await m.create(p['preview_id'])
    assert not c.writes

@pytest.mark.asyncio
async def test_source_change_after_create_preserves_original_and_child_identity(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m)
    original=c.__call__
    async def cloud(request):
        response=await original(request)
        if request.method=='POST':c.jobs[0]['roles'][0]['image_path']='changed'
        return response
    m.transport=httpx.MockTransport(cloud)
    with pytest.raises(ActionError) as error:await m.create(p['preview_id'])
    assert error.value.code=='release_incomplete' and len(c.writes)==1
    with pytest.raises(ActionError):await restored(m,c,store).create(p['preview_id'])
    assert len(c.writes)==1 and c.jobs[0]['state']=='RUNNING'

@pytest.mark.asyncio
async def test_cloud_read_failure_after_create_can_resume_after_process_restart(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m)
    original=c.__call__
    async def cloud(request):
        if c.writes and request.method=='GET':raise httpx.ReadTimeout('PRIVATE',request=request)
        return await original(request)
    m.transport=httpx.MockTransport(cloud)
    with pytest.raises(ActionError) as error:await m.create(p['preview_id'])
    assert error.value.code=='release_incomplete' and 'PRIVATE' not in str(error.value)
    assert len(c.writes)==1
    result=await restored(m,c,store).create(p['preview_id'])
    assert result['release_restart'] and len(c.writes)==2

@pytest.mark.asyncio
async def test_stop_rejection_does_not_repeat_create_or_stop(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m)
    original=c.__call__
    async def cloud(request):
        if request.method=='POST' and request.url.path.endswith(':stop'):c.reject=True
        return await original(request)
    m.transport=httpx.MockTransport(cloud)
    with pytest.raises(ActionError):await m.create(p['preview_id'])
    c.reject=False
    with pytest.raises(ActionError):await restored(m,c,store).create(p['preview_id'])
    assert len(c.writes)==2 and c.jobs[0]['state']=='RUNNING'

@pytest.mark.asyncio
async def test_lost_in_memory_preview_before_any_child_write_keeps_original(setup):
    import uuid
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m);key=p['preview_id']
    context={'source_id':'ACP:pt-template','revision':'r','display_name':'charles_latest',
             'create_key':uuid.uuid5(uuid.UUID(key),'replacement').hex,
             'stop_key':str(uuid.uuid5(uuid.UUID(key),'release-original')),'new_id':'ACP:new','stage':'creating'}
    store.begin_operation(key,'release_restart','pt-template','charles_latest',result=context)
    with pytest.raises(ActionError) as error:await restored(m,c,store).create(key)
    assert error.value.code=='release_failed' and not c.writes and store.operation(key)['status']=='failed'


@pytest.mark.asyncio
@pytest.mark.parametrize('release,state',[(True,'RUNNING'),(False,'FAILED')])
@pytest.mark.parametrize('replicas',[1,2])
async def test_restart_submits_total_replicas_without_copying_observed_allocations(setup,release,state,replicas):
    import copy
    m,c,_=setup;source=c.jobs[0];source['state']=state
    role=source['roles'][0]
    role['total_replicas']=replicas
    role['resource_spec']=[{'name':'N6lS.Iu.I80.8','replicas':replicas,'limits':{'device':'8'},'requests':{'device':'8'}}]
    original_source=copy.deepcopy(source)
    original=c.__call__
    async def cloud(request):
        if request.method=='POST' and not request.url.path.endswith(':stop'):
            for role in json.loads(request.content)['roles']:
                # The official copy form uses total_replicas and zero per-spec allocations.
                if role.get('total_replicas',0)>0 and any(s.get('replicas',0)>0 for s in role['resource_spec']):
                    return httpx.Response(400,json={'code':'INVALID_ARGUMENT'})
        return await original(request)
    m.transport=httpx.MockTransport(cloud)
    options=await m.restart_options('ACP:pt-template',release=release)
    p=await m.restart_preview({'task_id':'ACP:pt-template','revision':options['revision'],'command':'python train.py'},release=release)
    assert not c.writes and p['total_gpus']==8*replicas
    result=await m.create(p['preview_id'])
    assert result['id']!='ACP:pt-template'
    body=json.loads(c.writes[0].content)
    assert body['roles'][0]['total_replicas']==replicas
    assert body['roles'][0]['resource_spec']==[{'name':'N6lS.Iu.I80.8','replicas':0}]
    for field in ('mount','env','framework','scheduling','resource_pool'):
        assert body[field]==original_source[field]
    assert source['roles']==original_source['roles']
    assert len(c.writes)==(2 if release else 1)
    assert source['state']==('SUSPENDING' if release else state)


@pytest.mark.asyncio
async def test_release_failure_keeps_safe_action_reason_and_does_not_stop(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m)
    c.jobs[0]['roles'][0]['image_path']='changed'
    with pytest.raises(ActionError) as error:await m.create(p['preview_id'])
    assert '原任务配置已变化' in str(error.value)
    assert '未停止原任务' in str(error.value) and not c.writes
    assert '原任务配置已变化' in store.operation(p['preview_id'])['error']


@pytest.mark.asyncio
async def test_release_cloud_rejection_exposes_status_without_private_body(setup):
    m,c,store=setup;c.jobs[0]['state']='RUNNING';p=await preview(m);c.reject=True
    with pytest.raises(ActionError) as error:await m.create(p['preview_id'])
    assert 'HTTP 400' in str(error.value) and 'SECRET' not in str(error.value)
    assert '未停止原任务' in str(error.value) and len(c.writes)==1
    assert c.jobs[0]['state']=='RUNNING'
