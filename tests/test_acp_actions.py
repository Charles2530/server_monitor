import asyncio
import copy
import json
import uuid
import httpx
import pytest
from server.config import WORKSPACE
from server.store import Store


def spec(gpus, cpu=14, memory=240):
    return {'name':f'N6lS.Iu.I80.{gpus}', 'device':{'number':gpus,'type':'N6lS','memory':80},
            'cpu':{'vcpu_allocatable':cpu*gpus},'memory':{'allocatable':memory*gpus}}


def job(name='pt-template', display='charles_latest', created='2026-09-29T09:25:08Z', state='SUSPENDED'):
    return {'id':WORKSPACE+'/trainingJobs/'+name,'name':name,'display_name':display,'state':state,
            'create_time':created,'framework':'PYTORCH','uid':'private-uid',
            'roles':[{'name':'Worker','total_replicas':1,'image_path':'private/image:v13',
                      'startup_script':'SECRET_OLD_SCRIPT', 'resource_spec':[{'name':'N6lS.Iu.I80.1','replicas':1,'limits':{'device':'1'}}]}],
            'env':[{'key':'EXISTING','value':'private-env'}], 'metadata':{'items':[]},
            'mount':[{'type':'PV_AFS','id':'disk','zone':'z','subdir':'/','mount_path':'/mnt/data'}],
            'ssh':{'auto_key_setup':False},'tensorboard':{'log_path':''},'lme':{'max_retries':'3'},
            'fault_tolerance':{'backoff_limit':0,'rules':[]},'async_checkpoint':{'max_ckpt_rounds':3},
            'resource_pool':{'name':'modeltoolchain-test','zone':'z','vpc_id':'vpc'},
            'scheduling':{'priority':'HIGHEST','quota_type':'RESERVED','scoring_strategy':{'type':'LEAST_ALLOCATED'}}}


class Cloud:
    def __init__(self):
        self.jobs=[job(),job('pt-other','someone_else','2026-09-30T00:00:00Z')]
        self.bindings=[{'name':n,'display_name':n.replace('-','_'),'id':WORKSPACE.split('/zones/')[0]+'/zones/z/aec2s/'+n,
                       'state':'ACTIVE','reserved_number':'8','reserved_cpu':'112','reserved_memory':'1920Gi','vpc_id':'vpc'}
                       for n in ('modeltoolchain-test','modeltoolchain','vigen')]
        self.specs=[spec(1),spec(2),spec(4),spec(8)]
        self.node_reads=[]
        self.node_free={n:[(8,112,1920)] for n in ('modeltoolchain-test','modeltoolchain')}
        self.writes=[]; self.timeout=False; self.reject=False
    async def __call__(self, request):
        path=request.url.path
        if request.method=='GET':
            if path.endswith(('/acns','/observe')):
                from tests.test_inventory import allocation_node, observation
                name=path.split('/aec2s/')[1].split('/')[0]
                self.node_reads.append(name)
                free=self.node_free.get(name)
                if free is None:return httpx.Response(403,json={})
                nodes=[allocation_node(str(i)) for i in range(len(free))]
                if path.endswith('/acns'):
                    return httpx.Response(200,json={'acns':nodes,'total_size':len(nodes),'next_page_token':''})
                return httpx.Response(200,json={'observations':{'acns':[observation(str(i),*v) for i,v in enumerate(free)]},'total_size':len(nodes),'next_page_token':''})
            if path.endswith('/trainingJobsOwn'):
                return httpx.Response(200,json={'training_jobs':self.jobs,'next_page_token':''})
            if path.endswith('/workspaceAEC2Bindings'):
                return httpx.Response(200,json={'aec2s':self.bindings,'next_page_token':''})
            if path.endswith('/resourceSpecs'):
                return httpx.Response(200,json={'resource_specs':self.specs})
            name=path.rsplit('/',1)[-1]
            found=next((j for j in self.jobs if j['name']==name),None)
            return httpx.Response(200,json=found) if found else httpx.Response(404,json={})
        self.writes.append(request)
        if self.timeout:raise httpx.ReadTimeout('SECRET RAW ERROR',request=request)
        if self.reject:return httpx.Response(400,json={'error':'SECRET RAW RESPONSE'})
        if path.endswith(':stop'):
            name=path.rsplit('/',1)[-1][:-5]
            j=next(j for j in self.jobs if j['name']==name);j['state']='SUSPENDING'
            return httpx.Response(200,json={})
        payload=json.loads(request.content)
        self.jobs.append({**payload,'id':WORKSPACE+'/trainingJobs/'+payload['name'],'state':'STARTING','create_time':'2026-09-29T10:00:00Z'})
        return httpx.Response(200,json=self.jobs[-1])


@pytest.fixture
def setup(tmp_path):
    from server.acp_actions import ACPManager
    cloud=Cloud();store=Store(tmp_path/'db')
    manager=ACPManager(store,transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{}})
    return manager,cloud,store


@pytest.mark.asyncio
async def test_options_and_preview_are_read_only_and_redacted(setup):
    manager,cloud,_=setup
    options=await manager.options()
    assert options['template']['id']=='ACP:pt-template'
    assert options['gpu_options']==[1,2,4,8]
    preview=await manager.preview({'display_name':'new_charles_test','replicas':2,'gpus_per_replica':2,'command':'python train.py'})
    assert preview['total_gpus']==4 and preview['cluster']['name']=='modeltoolchain_test'
    assert preview['command']=='python train.py\nsleep inf' and not preview['will_queue']
    assert preview['template_config']==options['template_config']
    assert preview['template_config']['images']==['private/image:v13']
    assert cloud.writes==[]
    assert 'private-env' not in json.dumps(preview) and 'SECRET' not in json.dumps(options)


@pytest.mark.asyncio
async def test_capacity_checks_cpu_memory_priority_and_queue(setup):
    m,c,_=setup
    c.bindings[0]['reserved_cpu']='0'
    data={'display_name':'charles_test','replicas':1,'gpus_per_replica':1,'command':''}
    assert (await m.preview(data))['cluster']['name']=='modeltoolchain'
    c.bindings[1]['reserved_memory']='0Gi'
    assert (await m.preview(data))['cluster']['name']=='vigen'
    c.bindings[2]['reserved_number']='0'
    p=await m.preview(data)
    assert p['will_queue'] and p['cluster']['name']=='modeltoolchain_test' and p['command']=='sleep inf'


@pytest.mark.asyncio
@pytest.mark.parametrize('free', [[(4,56,960),(4,56,960)], [(8,48,1664)], [(8,112,100)], None])
async def test_fragmented_or_unknown_nodes_skip_priority_cluster(setup,free):
    m,c,_=setup;c.node_free['modeltoolchain-test']=free
    p=await m.preview({'display_name':'charles_eight','replicas':1,'gpus_per_replica':8,'command':''})
    assert p['cluster']['id']=='modeltoolchain' and not p['will_queue']
    assert p['capacity_checks'][0]['node_check']['status'] in ('insufficient','unknown')
    assert 'vigen' not in c.node_reads and not c.writes


@pytest.mark.asyncio
async def test_node_slots_use_replica_size_not_total_cards(setup):
    m,c,_=setup;c.node_free['modeltoolchain-test']=[(4,56,960),(4,56,960)]
    p=await m.preview({'display_name':'charles_split','replicas':2,'gpus_per_replica':4,'command':''})
    assert p['cluster']['id']=='modeltoolchain-test' and not p['will_queue']
    assert p['capacity_checks'][0]['node_check']['replica_slots']==2


@pytest.mark.asyncio
async def test_no_node_fit_queues_and_manual_selection_stays_fixed(setup):
    m,c,_=setup
    c.node_free={n:[(4,56,960),(4,56,960)] for n in c.node_free}
    form={'display_name':'charles_queue','replicas':1,'gpus_per_replica':8,'command':''}
    p=await m.preview(form)
    assert p['cluster']['id']=='vigen' and not p['will_queue']
    assert p['capacity_checks'][-1]['node_check']['status']=='not_checked'
    c.bindings[2]['reserved_number']='0'
    p=await m.preview(form)
    assert p['cluster']['id']=='modeltoolchain-test' and p['will_queue']
    p=await m.preview({**form,'cluster_id':'modeltoolchain'})
    assert p['cluster']['id']=='modeltoolchain' and p['will_queue']
    assert len(p['capacity_checks'])==1 and not c.writes


@pytest.mark.asyncio
async def test_restart_checks_nodes_but_keeps_original_cluster(setup):
    m,c,_=setup;c.node_free['modeltoolchain-test']=[(0,112,1920)]
    options=await m.restart_options('ACP:pt-template')
    p=await m.restart_preview({'task_id':'ACP:pt-template','revision':options['revision'],'command':''})
    assert p['cluster']['id']=='modeltoolchain-test' and p['will_queue']
    assert p['capacity_checks'][0]['node_check']['status']=='insufficient' and not c.writes


@pytest.mark.asyncio
async def test_create_preserves_config_replaces_script_and_deduplicates_across_restart(setup):
    from server.acp_actions import ACPManager
    m,c,store=setup
    p=await m.preview({'display_name':'charles_new','replicas':2,'gpus_per_replica':1,'command':''})
    a,b=await asyncio.gather(m.create(p['preview_id']),m.create(p['preview_id']))
    assert a==b and len(c.writes)==1
    req=c.writes[0];body=json.loads(req.content)
    assert req.url.params['enable_queuing']=='true'
    assert body['roles']==[{'name':'Worker','resource_spec':[{'name':'N6lS.Iu.I80.1','replicas':0}],'total_replicas':2,'startup_script':'sleep inf','image_path':'private/image:v13'}]
    assert body['framework']=='PYTORCH_DDP'
    for k in ('mount','env','metadata','scheduling','tensorboard','fault_tolerance','ssh','async_checkpoint','lme'):
        assert body[k]==job()[k]
    assert 'uid' not in body and 'state' not in body and 'SECRET_OLD_SCRIPT' not in str(body)
    restarted=ACPManager(store,transport=httpx.MockTransport(c),session_loader=lambda:{'headers':{}})
    assert await restarted.create(p['preview_id'])==a and len(c.writes)==1


@pytest.mark.asyncio
async def test_ambiguous_create_never_retries_even_after_restart(setup):
    from server.acp_actions import ACPManager,ActionError
    m,c,store=setup
    p=await m.preview({'display_name':'charles_new','replicas':1,'gpus_per_replica':1,'command':''})
    c.timeout=True
    with pytest.raises(ActionError) as e:await m.create(p['preview_id'])
    assert e.value.code=='outcome_unknown' and 'SECRET' not in str(e.value)
    restarted=ACPManager(store,transport=httpx.MockTransport(c),session_loader=lambda:{'headers':{}})
    with pytest.raises(ActionError):await restarted.create(p['preview_id'])
    assert len(c.writes)==1


@pytest.mark.asyncio
async def test_lost_create_response_reconciles_committed_task_without_second_post(setup):
    from server.acp_actions import ACPManager,ActionError
    m,c,store=setup
    p=await m.preview({'display_name':'charles_reconcile','replicas':1,'gpus_per_replica':1,'command':''})
    c.timeout=True
    with pytest.raises(ActionError):await m.create(p['preview_id'])
    payload=json.loads(c.writes[0].content)
    c.jobs.append({**payload,'id':WORKSPACE+'/trainingJobs/'+payload['name'],'state':'WAIT_QUOTA'})
    restarted=ACPManager(store,transport=httpx.MockTransport(c),session_loader=lambda:{'headers':{}})
    result=await restarted.create(p['preview_id'])
    assert result['id']=='ACP:'+payload['name'] and result['state']=='WAIT_QUOTA'
    assert len(c.writes)==1 and store.operation(p['preview_id'])['status']=='succeeded'


@pytest.mark.asyncio
async def test_lost_stop_response_reconciles_and_rejects_reused_identity(setup):
    from server.acp_actions import ActionError
    m,c,store=setup;c.jobs[0]['state']='RUNNING';c.timeout=True
    key=str(uuid.uuid4())
    with pytest.raises(ActionError):await m.stop('ACP:pt-template','charles_latest',key)
    assert c.writes[0].content==b''
    c.jobs[0]['state']='SUSPENDED'
    result=await m.stop('ACP:pt-template','charles_latest',key)
    assert result['state']=='SUSPENDED' and len(c.writes)==1
    with pytest.raises(ActionError) as exc:await m.stop('ACP:pt-template','charles_renamed',key)
    assert exc.value.code=='request_conflict' and len(c.writes)==1


@pytest.mark.asyncio
async def test_expired_preview_is_not_sent(setup,monkeypatch):
    from server.acp_actions import ActionError
    m,c,_=setup
    monkeypatch.setattr('server.acp_actions.time.time',lambda:100)
    p=await m.preview({'display_name':'charles_new','replicas':1,'gpus_per_replica':1,'command':''})
    monkeypatch.setattr('server.acp_actions.time.time',lambda:10000)
    with pytest.raises(ActionError):await m.create(p['preview_id'])
    assert not c.writes


@pytest.mark.asyncio
async def test_stop_rechecks_id_display_and_membership_without_leaking_raw_data(setup):
    from server.acp_actions import ActionError
    m,c,_=setup;c.jobs[0]['state']='RUNNING'
    for task_id,name in [('CCI:pt-template','charles_latest'),('ACP:../pt-template','charles_latest'),('ACP:pt-other','someone_else'),('ACP:pt-template','wrong-charles')]:
        with pytest.raises(ActionError):await m.stop(task_id,name,str(uuid.uuid4()))
    assert c.writes==[]
    key=str(uuid.uuid4())
    result=await m.stop('ACP:pt-template','charles_latest',key)
    assert result['state']=='SUSPENDING' and c.writes[0].url.path.endswith('/trainingJobs/pt-template:stop')
    assert await m.stop('ACP:pt-template','charles_latest',key)==result and len(c.writes)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'display_name':'someone'}, {'replicas':0},{'replicas':True},{'replicas':1.5},{'gpus_per_replica':0},{'command':None},{'extra':'forbidden'}])
async def test_invalid_create_is_rejected_before_cloud_reads(setup,change):
    from server.acp_actions import ActionError
    m,c,_=setup
    data={'display_name':'charles_new','replicas':1,'gpus_per_replica':1,'command':''};data.update(change)
    with pytest.raises(ActionError):await m.preview(data)
    assert not c.writes

@pytest.mark.asyncio
async def test_running_template_command_preferred_to_newer_stopped_job(setup):
    m,c,_=setup
    running=job('pt-running','charles_running','2026-09-28T00:00:00Z','RUNNING')
    running['roles'][0]['startup_script']='python train.py\nsleep inf'
    c.jobs.extend([running,job('pt-older','charles_older','2026-09-27T00:00:00Z','RUNNING')])
    options=await m.options()
    assert options['template']['id']=='ACP:pt-running'
    assert options['default_command']=='python train.py\nsleep inf'
    assert options['command_source']=={'id':'ACP:pt-running','name':'charles_running'}
    preview=await m.preview({'display_name':'charles_new','replicas':1,'gpus_per_replica':1,'command':options['default_command']})
    assert preview['template']['id']=='ACP:pt-running'
    assert preview['command']=='python train.py\nsleep inf'
    assert not c.writes

@pytest.mark.asyncio
async def test_no_running_template_defaults_sleep_without_old_script(setup):
    m,c,_=setup
    options=await m.options()
    assert options['default_command']=='sleep inf' and options['command_source'] is None
    assert 'SECRET_OLD_SCRIPT' not in json.dumps(options)

@pytest.mark.parametrize('command,expected',[
    ('sleep inf','sleep inf'),
    ('python train.py\r\nsleep inf\r\n','python train.py\nsleep inf'),
    ('python train.py','python train.py\nsleep inf'),
    ('echo "sleep inf"','echo "sleep inf"\nsleep inf'),
])
def test_sleep_suffix_is_not_duplicated(command,expected):
    from server.acp_actions import validate_form
    assert validate_form({'display_name':'charles_test','replicas':1,'gpus_per_replica':1,'command':command})['command']==expected


@pytest.mark.asyncio
async def test_manual_cluster_selection_and_queue_preserve_payload(setup):
    m,c,_=setup
    form={'display_name':'charles_manual','replicas':1,'gpus_per_replica':1,'command':'','cluster_id':'vigen'}
    p=await m.preview(form)
    assert p['cluster']['id']=='vigen' and p['selection_mode']=='manual' and not p['will_queue']
    c.bindings[2]['reserved_number']='0'
    p=await m.preview(form)
    assert p['cluster']['id']=='vigen' and p['will_queue']
    await m.create(p['preview_id'])
    payload=json.loads(c.writes[0].content)
    assert payload['resource_pool']['name']=='vigen'
    assert payload['mount']==job()['mount'] and payload['env']==job()['env']


@pytest.mark.asyncio
async def test_manual_nonpriority_cluster_and_unchanged_auto_scope(setup):
    m,c,_=setup
    extra=copy.deepcopy(c.bindings[0]);extra.update(name='extra',display_name='Extra',id=extra['id'].rsplit('/',1)[0]+'/extra')
    c.bindings.append(extra)
    options=await m.options()
    assert options['clusters'][-1]=={'id':'extra','name':'Extra','gpu_options':[1,2,4,8]}
    assert options['cluster_order']==['modeltoolchain_test','modeltoolchain','vigen']
    form={'display_name':'charles_manual','replicas':1,'gpus_per_replica':1,'command':''}
    for b in c.bindings[:3]:b['reserved_number']='0'
    for choice in ({},{'cluster_id':''}):
        p=await m.preview({**form,**choice})
        assert p['cluster']['id']=='modeltoolchain-test' and p['will_queue'] and p['selection_mode']=='auto'
    p=await m.preview({**form,'cluster_id':'extra'})
    assert p['cluster']['id']=='extra' and not p['will_queue']


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['unknown','inactive','zone','foreign','public'])
async def test_invalid_manual_cluster_never_falls_back(setup,change):
    from server.acp_actions import ActionError
    m,c,_=setup
    choice='vigen'
    if change=='unknown':choice='missing'
    elif change=='inactive':c.bindings[2]['state']='INACTIVE'
    elif change=='zone':c.bindings[2]['id']=c.bindings[2]['id'].replace('/zones/z/','/zones/other/')
    elif change=='foreign':c.bindings[2]['id']=c.bindings[2]['id'].replace('/subscriptions/','/subscriptions/foreign-')
    else:c.bindings[2]['type']='PUBLIC'
    with pytest.raises(ActionError):
        await m.preview({'display_name':'charles_manual','replicas':1,'gpus_per_replica':1,'command':'','cluster_id':choice})
    assert not c.writes and not m.previews


@pytest.mark.asyncio
async def test_selected_cluster_specs_are_enforced(setup):
    from server.acp_actions import ActionError
    m,c,_=setup
    original=m.reader.request
    async def read(client,service,path,**kwargs):
        if path.endswith('/vigen/resourceSpecs'):return {'resource_specs':[spec(8)]}
        return await original(client,service,path,**kwargs)
    m.reader.request=read
    options=await m.options()
    assert next(b for b in options['clusters'] if b['id']=='vigen')['gpu_options']==[8]
    with pytest.raises(ActionError):
        await m.preview({'display_name':'charles_manual','replicas':1,'gpus_per_replica':1,'command':'','cluster_id':'vigen'})
    assert not c.writes

@pytest.mark.asyncio
async def test_restart_uses_exact_history_config_and_original_failed_command(setup):
    m,c,store=setup
    source=c.jobs[0];source['state']='FAILED'
    source['roles'][0]['resource_spec'][0]['name']='custom-small'
    c.specs.append({**spec(1,cpu=7,memory=60),'name':'custom-small'})
    c.jobs.append(job('pt-newer','charles_newer','2026-10-01T00:00:00Z','RUNNING'))
    options=await m.restart_options('ACP:pt-template')
    assert options['command']=='SECRET_OLD_SCRIPT'
    assert not c.writes and 'private-env' not in json.dumps(options)
    p=await m.restart_preview({'task_id':'ACP:pt-template','revision':options['revision'],'command':'python retry.py'})
    assert p['vcpu_per_replica']==7 and p['memory_gib_per_replica']==60
    assert p['template']['id']=='ACP:pt-template' and p['command']=='python retry.py\nsleep inf'
    assert not c.writes
    a,b=await asyncio.gather(m.create(p['preview_id']),m.create(p['preview_id']))
    assert a==b and len(c.writes)==1
    body=json.loads(c.writes[0].content)
    assert body['name']!=source['name'] and body['display_name']==source['display_name']
    for key in ('framework','mount','env','scheduling','resource_pool'):
        assert body[key]==source[key]
    assert body['roles'][0]['image_path']==source['roles'][0]['image_path']
    assert body['roles'][0]['resource_spec']==[{'name':'custom-small','replicas':0}]
    assert body['roles'][0]['startup_script']=='python retry.py\nsleep inf'

@pytest.mark.asyncio
@pytest.mark.parametrize('state',['FAILED','SUSPENDED','SUCCEEDED'])
async def test_restart_terminal_tasks_and_blank_command(setup,state):
    m,c,_=setup;c.jobs[0]['state']=state
    o=await m.restart_options('ACP:pt-template')
    p=await m.restart_preview({'task_id':'ACP:pt-template','revision':o['revision'],'command':''})
    assert p['command']=='sleep inf' and not c.writes

@pytest.mark.asyncio
@pytest.mark.parametrize('target',['CCI:pt-template','ACP:pt-other','ACP:missing'])
async def test_restart_rejects_foreign_or_non_acp(setup,target):
    from server.acp_actions import ActionError
    m,c,_=setup
    with pytest.raises(ActionError):await m.restart_options(target)
    assert not c.writes

@pytest.mark.asyncio
async def test_restart_revalidates_source_and_preserves_cluster_when_full(setup):
    from server.acp_actions import ActionError
    m,c,_=setup
    o=await m.restart_options('ACP:pt-template')
    c.jobs[0]['roles'][0]['image_path']='changed'
    with pytest.raises(ActionError):await m.restart_preview({'task_id':'ACP:pt-template','revision':o['revision'],'command':''})
    o=await m.restart_options('ACP:pt-template');c.bindings[0]['reserved_number']='0'
    p=await m.restart_preview({'task_id':'ACP:pt-template','revision':o['revision'],'command':''})
    assert p['will_queue'] and p['cluster']['id']=='modeltoolchain-test'
    c.jobs[0]['state']='RUNNING'
    with pytest.raises(ActionError):await m.create(p['preview_id'])
    assert not c.writes

@pytest.mark.asyncio
async def test_restart_missing_original_spec_does_not_substitute(setup):
    from server.acp_actions import ActionError
    m,c,_=setup;c.jobs[0]['roles'][0]['resource_spec'][0]['name']='removed-spec'
    o=await m.restart_options('ACP:pt-template')
    with pytest.raises(ActionError):await m.restart_preview({'task_id':'ACP:pt-template','revision':o['revision'],'command':''})
    assert not c.writes
