import httpx
import pytest
from server.collector import Collector, SourceError
from server.auth import AuthRequired

@pytest.mark.asyncio
async def test_pagination_includes_second_page_and_rejects_repeat():
    c=Collector(); calls=[]
    async def handler(request):
        token=request.url.params['page_token']; calls.append(token)
        return httpx.Response(200,json={'items':[token],'next_page_token':'2' if token=='1' else ''})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await c.paginate(client,'CCI','/list','items')==['1','2']
    assert calls==['1','2']
    async def repeat(request): return httpx.Response(200,json={'items':[],'next_page_token':'1'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(repeat)) as client:
        with pytest.raises(SourceError): await c.paginate(client,'CCI','/list','items')

@pytest.mark.asyncio
async def test_auth_error_is_sanitized():
    async def handler(request):return httpx.Response(401,text='secret token value')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthRequired) as caught:await Collector().request(client,'CCI','/list')
    assert 'secret' not in str(caught.value)

@pytest.mark.asyncio
async def test_partial_list_failure_preserves_source_and_filters(monkeypatch):
    monkeypatch.setattr('server.collector.load_session',lambda:{'headers':{}})
    c=Collector()
    async def meta(client): pass
    async def pages(client,service,path,key,extra_params=None):
        if key=='training_jobs':raise SourceError('no')
        if key=='aec2s':return []
        return [{'name':name,'display_name':name,'state':state,'replicas':1} for name,state in [('prefix-charles','RUNNING'),('charles-stopped','STOPPED'),('someone','RUNNING')]]
    async def metrics(client,task,now):task['metrics']={'cpu':1}
    monkeypatch.setattr(c,'metadata',meta);monkeypatch.setattr(c,'paginate',pages);monkeypatch.setattr(c,'task_metrics',metrics)
    old={'tasks':[{'id':'ACP:old','kind':'ACP','series':[]}],'source_status':{'ACP':{'updated_at':'old'}}}
    result=await c.collect(old)
    assert [t['id'] for t in result['tasks']]==['CCI:prefix-charles','ACP:old']
    assert result['source_status']['ACP']['updated_at']=='old' and result['status']=='partial'

@pytest.mark.asyncio
async def test_metric_failure_keeps_previous_samples(monkeypatch):
    monkeypatch.setattr('server.collector.load_session',lambda:{'headers':{}})
    c=Collector()
    async def meta(client): pass
    async def pages(client,service,path,key,extra_params=None):
        if key=='apps':return [{'name':'charles','display_name':'charles','state':'RUNNING'}]
        return []
    async def metrics(client,task,now): raise SourceError('metrics unavailable')
    monkeypatch.setattr(c,'metadata',meta);monkeypatch.setattr(c,'paginate',pages);monkeypatch.setattr(c,'task_metrics',metrics)
    old={'tasks':[{'id':'CCI:charles','kind':'CCI','metrics':{'cpu':35},'series':[],'workers':['old'],'updated_at':'old'}]}
    result=await c.collect(old)
    t=result['tasks'][0]
    assert t['metrics']['cpu']==35 and t['updated_at']=='old' and t['metric_error']

@pytest.mark.asyncio
async def test_missing_worker_is_partial(monkeypatch):
    c=Collector(); c.workspace_uid='workspace'; c.station='/station'
    async def pages(*args):return [{'name':'master'},{'name':'worker'}]
    async def request(*args,**kwargs):return {'metrics_result':[{'metrics_attrs':{'exported_pod':'master'},'values':[{'metric_time':'2026-09-29T07:00:00Z','values':.5}]}]}
    monkeypatch.setattr(c,'paginate',pages);monkeypatch.setattr(c,'request',request)
    task={'kind':'ACP','source_id':'x','uid':'uid','series':[]}
    await c.task_metrics(None,task,1790665200)
    assert 'worker' in task['metric_error']

@pytest.mark.asyncio
async def test_partial_panel_updates_healthy_metrics(monkeypatch):
    monkeypatch.setattr("server.collector.time.time",lambda:2)
    monkeypatch.setattr('server.collector.load_session',lambda:{'headers':{}})
    c=Collector()
    async def meta(client): pass
    async def pages(client,service,path,key,extra_params=None):
        return [{'name':'charles','display_name':'charles','state':'RUNNING'}] if key=='apps' else []
    async def metrics(client,task,now):
        task.update(metrics={'cpu':80,'gpu':None},metric_error='gpu missing',failed_metrics=['gpu'],metric_updated_at={'cpu':'new'},updated_at='new',series=[{'metric':'cpu','label':'cpu','points':[[2,80]]}])
    monkeypatch.setattr(c,'metadata',meta);monkeypatch.setattr(c,'paginate',pages);monkeypatch.setattr(c,'task_metrics',metrics)
    old={'tasks':[{'id':'CCI:charles','kind':'CCI','metrics':{'cpu':10,'gpu':20},'series':[{'metric':'cpu','label':'cpu','points':[[1,10]]}], 'updated_at':'old'}]}
    t=(await c.collect(old))['tasks'][0]
    assert t['metrics']=={'cpu':80,'gpu':20}
    assert t['metric_updated_at']=={'cpu':'new','gpu':'old'}
    assert t['series'][0]['points']==[[1.0,10.0],[2.0,80.0]]

@pytest.mark.asyncio
async def test_acp_waiting_separate_without_metrics_and_transition(monkeypatch):
    monkeypatch.setattr('server.collector.load_session',lambda:{'headers':{}})
    c=Collector();states={'a':'QUEUEING','b':'STARTING','c':'WAIT_QUOTA','d':'SUSPENDING','e':'SUSPENDED','f':'RUNNING'};calls=[]
    async def meta(client):pass
    async def pages(client,service,path,key,extra_params=None):
        return [{'name':n,'display_name':'charles_'+n,'state':s,'create_time':'2026-09-29T09:00:00Z'} for n,s in states.items()] if key=='training_jobs' else []
    async def metrics(client,task,now):calls.append(task['id'])
    monkeypatch.setattr(c,'metadata',meta);monkeypatch.setattr(c,'paginate',pages);monkeypatch.setattr(c,'task_metrics',metrics)
    first=await c.collect()
    assert [t['id'] for t in first['waiting_tasks']]==['ACP:a','ACP:b','ACP:c']
    assert calls==['ACP:f'] and [t['id'] for t in first['tasks']]==['ACP:f']
    assert first['waiting_tasks'][0]['state_label']=='排队中'
    assert all('series' not in t and 'metrics' not in t for t in first['waiting_tasks'])
    states['a']='RUNNING';states['b']='SUSPENDED';calls.clear()
    second=await c.collect(first)
    assert [t['id'] for t in second['waiting_tasks']]==['ACP:c']
    assert calls==['ACP:a','ACP:f']

@pytest.mark.asyncio
async def test_waiting_list_failure_preserves_original_timestamp(monkeypatch):
    monkeypatch.setattr('server.collector.load_session',lambda:{'headers':{}})
    c=Collector()
    async def meta(client):pass
    async def pages(client,service,path,key,extra_params=None):
        if key=='training_jobs':raise SourceError('offline')
        return []
    monkeypatch.setattr(c,'metadata',meta);monkeypatch.setattr(c,'paginate',pages)
    previous={'waiting_tasks':[{'id':'ACP:q','kind':'ACP','name':'charles_q','state':'QUEUEING','observed_at':'old'}],'source_status':{'ACP':{'updated_at':'old'}}}
    result=await c.collect(previous)
    assert result['waiting_tasks']==previous['waiting_tasks'] and result['source_status']['ACP']['status']=='error'

@pytest.mark.asyncio
async def test_all_user_queue_keeps_running_monitor_personal(monkeypatch):
    monkeypatch.setattr('server.collector.load_session',lambda:{'headers':{}})
    c=Collector(); metric_calls=[]; paths=[]
    rows={
        'CCI':[('own','charles_c','RUNNING'),('other','someone','RUNNING'),('cq','someone','QUEUING'),('cs','someone','SCHEDULED'),('cp','someone','PROGRESSING'),('ci','someone','INIT'),('cw','someone','WAIT_QUOTA'),('cu','someone','UPDATING')],
        'ACP':[('own','charles_a','RUNNING'),('aq','someone','QUEUEING'),('as','someone','STARTING'),('mine','charles_mine','WAIT_QUOTA'),('stop','someone','SUSPENDING'),('end','someone','SUSPENDED')],
    }
    async def meta(client):pass
    async def pages(client,service,path,key,extra_params=None):
        paths.append(path)
        kind='CCI' if key=='apps' else 'ACP'
        if key not in ('apps','training_jobs'):return []
        data=[{'name':n,'display_name':d,'state':s,'ownership':{'user_name':'alice' if d=='someone' else 'gongruihao'},'env':{'SECRET':'hidden'}} for n,d,s in rows[kind]]
        return [r for r in data if 'charles' in r['display_name']] if path.endswith('Own') else data
    async def metrics(client,task,now):metric_calls.append(task['id'])
    monkeypatch.setattr(c,'metadata',meta);monkeypatch.setattr(c,'paginate',pages);monkeypatch.setattr(c,'task_metrics',metrics)
    result=await c.collect()
    assert {t['id'] for t in result['waiting_tasks']}=={'CCI:cq','CCI:cs','CCI:cp','CCI:ci','CCI:cw','ACP:aq','ACP:as','ACP:mine'}
    assert set(metric_calls)=={'CCI:own','ACP:own'}
    assert all('metrics' not in t and 'env' not in t for t in result['waiting_tasks'])
    assert {t['owner'] for t in result['waiting_tasks']}=={'alice','gongruihao'}
    assert [t['id'] for t in result['waiting_tasks'] if t['can_stop']]==['ACP:mine']
    assert any(p.endswith('/apps') for p in paths) and any(p.endswith('/trainingJobs') for p in paths)
    assert result['source_status']['CCI_queue']['status']==result['source_status']['ACP_queue']['status']=='ok'

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[SourceError('offline'),AuthRequired('forbidden')])
async def test_queue_failure_is_isolated_from_running_and_other_queue(monkeypatch,failure):
    monkeypatch.setattr('server.collector.load_session',lambda:{'headers':{}})
    c=Collector()
    async def meta(client):pass
    async def pages(client,service,path,key,extra_params=None):
        if path.endswith('/apps'):raise failure
        if path.endswith('/trainingJobs'):return [{'name':'q','display_name':'alice','state':'STARTING'}]
        return []
    monkeypatch.setattr(c,'metadata',meta);monkeypatch.setattr(c,'paginate',pages)
    old={'waiting_tasks':[{'id':'CCI:old','kind':'CCI','state':'QUEUING','observed_at':'old'}], 'source_status':{'CCI_queue':{'updated_at':'old'}}}
    result=await c.collect(old)
    assert result['waiting_tasks'][0]==old['waiting_tasks'][0]
    assert result['source_status']['CCI_queue']['updated_at']=='old'
    assert result['source_status']['CCI_queue']['status']=='error'
    assert result['source_status']['ACP_queue']['status']=='ok'
    assert result['source_status']['CCI']['status']==result['source_status']['ACP']['status']=='ok'
    assert result['status']=='partial'

@pytest.mark.asyncio
async def test_queue_pagination_preserves_filter_and_small_page_size():
    seen=[]
    async def handler(request):
        seen.append(dict(request.url.params))
        token=request.url.params['page_token']
        return httpx.Response(200,json={'apps':[token],'next_page_token':'2' if token=='1' else ''})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await Collector().paginate(client,'CCI','/apps','apps',{'page_size':20,'filter':"state='QUEUING'"})==['1','2']
    assert seen==[{'page_size':'20','filter':"state='QUEUING'",'page_token':token} for token in ['1','2']]

@pytest.mark.asyncio
async def test_own_list_failure_does_not_hide_all_user_queue_or_allow_stopping(monkeypatch):
    monkeypatch.setattr('server.collector.load_session',lambda:{'headers':{}})
    c=Collector(); queued=True
    async def meta(client):pass
    async def pages(client,service,path,key,extra_params=None):
        if path.endswith('trainingJobsOwn'):raise SourceError('offline')
        if path.endswith('/trainingJobs'):return [{'name':'q','display_name':'charles_q','state':'STARTING' if queued else 'RUNNING'}]
        return []
    monkeypatch.setattr(c,'metadata',meta);monkeypatch.setattr(c,'paginate',pages)
    first=await c.collect()
    assert first['source_status']['ACP']['status']=='error'
    assert first['source_status']['ACP_queue']['status']=='ok'
    assert first['waiting_tasks'][0]['can_stop'] is False
    queued=False
    second=await c.collect(first)
    assert second['waiting_tasks']==[]
