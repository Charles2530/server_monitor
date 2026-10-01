import pytest
from server.inventory import summarize_nodes, enrich_clusters


def node(uid, cards=8, state='ACTIVE', available='ENABLED'):
    return {'uid':uid,'state':state,'properties':{'available_status':available,'resources':{'device':{'number':cards,'type':'test-gpu'}}}}


def allocation_node(uid, cards=8, state='ACTIVE', available='ENABLED', status='READY'):
    n=node(uid,cards,state,available)
    n['properties']['status']=status
    n['properties']['resources']['device']['type']='N6lS'
    return n


def observation(uid, gpu=8, cpu=112, memory=1920):
    return {'uid':uid,'capacity':{'device':'8','cpu':'112','memory':'1920'},
            'allocated':{'device':str(8-gpu),'cpu':str(112-cpu),'memory':str(1920-memory)}}


def test_idle_gpu_groups_exclude_disabled_abnormal_and_unschedulable_nodes():
    from server.inventory import summarize_allocation
    nodes=[allocation_node('a'),allocation_node('b'),allocation_node('c',state='ERROR'),
           allocation_node('d',available='DISABLED'),allocation_node('e',status='UNSCHEDULABLE')]
    rows=[observation(n['uid']) for n in nodes];rows[1]=observation('b',4)
    result=summarize_allocation(nodes,rows,100)
    assert result['idle_gpu_groups']==[{'gpus_per_node':8,'nodes':1}]
    assert len(result['nodes'])==2 and result['status']=='ok'


@pytest.mark.parametrize('change', ['missing','duplicate','negative','over','fraction','capacity_mismatch','unknown_status'])
def test_incomplete_allocation_is_never_reported_as_idle(change):
    from server.inventory import summarize_allocation
    nodes=[allocation_node('a')];rows=[observation('a')]
    if change=='missing':rows=[]
    if change=='duplicate':rows.append(observation('a'))
    if change=='negative':rows[0]['allocated']['device']='-1'
    if change=='over':rows[0]['allocated']['device']='9'
    if change=='fraction':rows[0]['allocated']['device']='0.5'
    if change=='capacity_mismatch':rows[0]['capacity']['device']='16'
    if change=='unknown_status':nodes[0]['properties'].pop('status')
    with pytest.raises(ValueError):summarize_allocation(nodes,rows,100)


def test_node_fit_requires_matching_gpu_and_all_resources_per_replica():
    from server.inventory import summarize_allocation, allocation_fit
    from tests.test_acp_actions import spec
    data=summarize_allocation([allocation_node('a'),allocation_node('b')],
                              [observation('a',4,56,960),observation('b',4,56,960)],100)
    assert allocation_fit(data,spec(8),1)['status']=='insufficient'
    assert allocation_fit(data,spec(4),2)['replica_slots']==2
    assert allocation_fit(data,spec(4),3)['status']=='insufficient'
    data=summarize_allocation([allocation_node('a')],[observation('a',8,48,1664)],100)
    assert data['idle_gpu_groups']==[{'gpus_per_node':8,'nodes':1}]
    assert allocation_fit(data,spec(8),1)['status']=='insufficient'
    other=spec(1);other['device']['type']='other-gpu'
    assert allocation_fit(data,other,1)['status']=='insufficient'


def test_inventory_counts_heterogeneous_and_abnormal_nodes():
    data=summarize_nodes([node('a',8),node('b',4,'ERROR'),node('c',0,available='DISABLED')],100)
    assert data['total_gpus']==12 and data['node_count']==3
    assert data['abnormal_nodes']==1 and data['abnormal_node_gpus']==4
    assert data['disabled_nodes']==1


def test_empty_inventory_is_zero_but_missing_spec_is_unknown():
    assert summarize_nodes([],100)['total_gpus']==0
    with pytest.raises(ValueError):summarize_nodes([node('a',None)],100)
    with pytest.raises(ValueError):summarize_nodes([node('a',-1)],100)
    with pytest.raises(ValueError):summarize_nodes([node('a',1.5)],100)


@pytest.mark.asyncio
async def test_inventory_pagination_exact_scope_and_stale_fallback():
    rid='/subscriptions/s/resourceGroups/default/zones/z/aec2s/'
    clusters=[{'id':rid+name,'name':'display','available_gpus':2,'updated_at':'capacity-new'} for name in ('modeltoolchain','modeltoolchain-test','other')]
    old={'clusters':[{'id':rid+'modeltoolchain-test','inventory':{'total_gpus':160,'updated_at':'old'}}]}
    calls=[]
    async def request(client,service,path,params):
        calls.append((path,params['page_token']))
        if 'modeltoolchain-test' in path:raise RuntimeError('private error text')
        token=params['page_token']
        return {'acns':[node(token)],'total_size':2,'next_page_token':'2' if token=='1' else ''}
    state={'clusters':clusters,'source_status':{}}
    await enrich_clusters(request,None,state,old,100)
    assert clusters[0]['inventory']['total_gpus']==16
    assert clusters[1]['inventory']['total_gpus']==160 and clusters[1]['inventory']['updated_at']=='old'
    assert clusters[1]['available_gpus']==2 and clusters[1]['updated_at']=='capacity-new'
    assert 'private' not in clusters[1]['inventory_error']
    assert 'inventory' not in clusters[2]
    assert state['source_status']['inventory']['status']=='error'
    assert ('/compute/aec2/data/v1'+rid+'modeltoolchain/acns','2') in calls
    assert len(calls)==3


@pytest.mark.asyncio
@pytest.mark.parametrize('payload',[
    {'acns':[node('a')],'total_size':2,'next_page_token':''},
    {'acns':[node('a'),node('a')],'total_size':2,'next_page_token':''},
    {'acns':[],'total_size':0,'next_page_token':'1'},
])
async def test_incomplete_or_duplicate_inventory_never_shows_partial_total(payload):
    async def request(*args,**kwargs):return payload
    c={'id':'/subscriptions/s/aec2s/modeltoolchain','name':'modeltoolchain'}
    state={'clusters':[c],'source_status':{}}
    await enrich_clusters(request,None,state,{},100)
    assert c['inventory'] is None and c['inventory_error']

@pytest.mark.asyncio
async def test_inventory_timeout_preserves_other_sources(monkeypatch):
    import asyncio
    monkeypatch.setattr('server.inventory.INVENTORY_TIMEOUT_SECONDS',.01)
    async def request(*args,**kwargs):await asyncio.sleep(1)
    c={'id':'/subscriptions/s/aec2s/modeltoolchain','available_gpus':6,'updated_at':'new'}
    state={'clusters':[c],'source_status':{'resources':{'status':'ok'}},'tasks':[{'metrics':{'gpu':99}}]}
    await enrich_clusters(request,None,state,{},100)
    assert c['inventory_error'] and state['source_status']['inventory']['status']=='error'
    assert c['available_gpus']==6 and state['tasks'][0]['metrics']['gpu']==99
    assert state['source_status']['resources']['status']=='ok'


@pytest.mark.asyncio
async def test_allocation_collection_batches_exact_nodes_and_keeps_total_on_failure():
    from server.inventory import read_inventory
    nodes=[allocation_node(str(i)) for i in range(21)];calls=[];broken=False
    async def request(client,service,path,params):
        calls.append((path,params))
        if path.endswith('/acns'):return {'acns':nodes,'total_size':21,'next_page_token':''}
        if broken:raise RuntimeError('PRIVATE SOURCE ERROR')
        rows=[observation(uid) for key,uid in params]
        return {'observations':{'acns':rows},'total_size':len(rows),'next_page_token':''}
    cluster={'id':'/subscriptions/s/aec2s/modeltoolchain'}
    data=await read_inventory(request,None,cluster,100,include_allocation=True)
    assert data['allocation']['idle_gpu_groups']==[{'gpus_per_node':8,'nodes':21}]
    assert [len(p) for path,p in calls if path.endswith('/observe')]==[20,1]
    broken=True
    data=await read_inventory(request,None,cluster,101,include_allocation=True)
    assert data['total_gpus']==168 and data['allocation']['status']=='unknown'
    assert data['allocation']['idle_gpu_groups'] is None
    assert 'PRIVATE' not in str(data)


@pytest.mark.asyncio
async def test_sensecore_inventory_does_not_gain_maoshanwang_allocation_reads():
    calls=[]
    async def request(client,service,path,params):
        calls.append(path)
        return {'acns':[node('a')],'total_size':1,'next_page_token':''}
    state={'clusters':[{'id':'/subscriptions/s/aec2s/modeltoolchain'}],'source_status':{}}
    await enrich_clusters(request,None,state,{},100,all_bound=True)
    assert len(calls)==1 and 'allocation' not in state['clusters'][0]['inventory']
