import pytest
from server.inventory import summarize_nodes, enrich_clusters


def node(uid, cards=8, state='ACTIVE', available='ENABLED'):
    return {'uid':uid,'state':state,'properties':{'available_status':available,'resources':{'device':{'number':cards,'type':'test-gpu'}}}}


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
