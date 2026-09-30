"""Terminal task history must not mistake queued cancellations for executed jobs."""
import httpx
import pytest
from server.task_history import TaskHistory, history_task
from server.sites import MAOSHANWANG,SENSECORE
from server.store import Store


def job(site=MAOSHANWANG,kind='ACP',name='job-1',state='SUSPENDED',start='2026-09-29T01:00:00Z'):
    return {'id':site.workspace+('/trainingJobs/' if kind=='ACP' else '/apps/')+name,'name':name,
            'display_name':'charles '+name,'state':state,'start_time':start,
            'create_time':'2026-09-29T00:00:00Z','suspend_time':'2026-09-29T02:00:00Z',
            'complete_time':'2026-09-29T03:00:00Z','update_time':'2026-09-29T04:00:00Z',
            'roles':[{'total_replicas':1,'resource_spec':[{'limits':{'device':{'number':1}}}]}],
            'env':{'SECRET':'private'},'resource_pool':{'display_name':'gpu-cluster'}}

@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE])
def test_stopped_needs_execution_evidence(site):
    row=job(site)
    result=history_task(row,'ACP',site,set())
    assert result['status']=='stopped' and result['duration_seconds']==3600
    assert result['url'].startswith(site.console+'/acp/list/detail?')
    assert 'env' not in result and 'roles' not in result
    for invalid in (None,'','bad','0001-01-01T00:00:00Z','2026-09-29T05:00:00Z'):
        assert history_task({**row,'start_time':invalid},'ACP',site,set()) is None
    result=history_task({**row,'start_time':None},'ACP',site,{'ACP:job-1'})
    assert result['run_evidence']=='observed' and result['started_at'] is None
    assert result['duration_seconds'] is None

@pytest.mark.parametrize('state,status',[('SUCCEEDED','succeeded'),('FAILED','failed')])
def test_success_and_failure_keep_platform_terminal_state(state,status):
    row=job(state=state,start=None)
    result=history_task(row,'ACP',MAOSHANWANG,set())
    assert result['status']==status and result['ended_at']==row['complete_time'].replace('Z','+00:00')

@pytest.mark.parametrize('state',['RUNNING','SUSPENDING','QUEUEING','INIT','UNKNOWN'])
def test_nonterminal_excluded(state):
    assert history_task(job(state=state),'ACP',MAOSHANWANG,set()) is None

def test_foreign_names_ids_and_cci_unknown_execution_are_excluded():
    for row in ({**job(),'display_name':'alice'},{**job(),'id':SENSECORE.workspace+'/trainingJobs/job-1'}):
        assert history_task(row,'ACP',MAOSHANWANG,set()) is None
    cci=job(kind='CCI',start=None)
    assert history_task(cci,'CCI',MAOSHANWANG,set()) is None
    result=history_task(cci,'CCI',MAOSHANWANG,{'CCI:job-1'})
    assert result['ended_at'] is None  # CCI update_time is not a proven stop time.
    assert result['duration_seconds'] is None and '/cci/app/detail?' in result['url']

@pytest.mark.asyncio
async def test_background_history_pages_and_retains_old_source_on_failure(tmp_path):
    store=Store(tmp_path/'db');calls=[];fail=False
    def handler(request):
        calls.append(request)
        assert request.method=='GET' and request.headers['authorization']=='mao-token'
        assert MAOSHANWANG.workspace in request.url.path
        if request.url.path.endswith('appsOwn'):return httpx.Response(200,json={'apps':[]})
        if fail:return httpx.Response(403,json={})
        token=request.url.params['page_token']
        rows=[job(name='new'),job(name='cancelled',start=None)] if token=='1' else [job(name='failed',state='FAILED',start=None),{**job(),'display_name':'alice'}]
        return httpx.Response(200,json={'training_jobs':rows,'next_page_token':'2' if token=='1' else ''})
    manager=TaskHistory(store,MAOSHANWANG,transport=httpx.MockTransport(handler),session_loader=lambda:{'headers':{'authorization':'mao-token'}})
    assert (await manager.snapshot())['loading']
    await manager.refresh_task
    data=await manager.snapshot()
    assert len(data['tasks'])==2 and data['filtered_stopped']['ACP']==1
    assert data['source_status']['ACP']['status']=='ok'
    before=len(calls);assert await manager.snapshot()==data;assert len(calls)==before
    fail=True;manager.checked_at-=61
    await manager.snapshot();await manager.refresh_task
    failed=await manager.snapshot()
    assert failed['tasks']==data['tasks'] and failed['source_status']['ACP']['status']=='error'
    assert failed['source_status']['ACP']['updated_at']==data['source_status']['ACP']['updated_at']
    assert failed['warnings']
    await manager.close()

def test_run_evidence_survives_metric_pruning(tmp_path):
    path=tmp_path/'db';store=Store(path)
    store.save({'tasks':[{'id':'CCI:ran','state':'running','series':[]}],'clusters':[]},100)
    store.prune(100+86400*30)
    assert 'CCI:ran' in Store(path).running_task_ids()

@pytest.mark.asyncio
async def test_local_evidence_read_failure_is_reported_not_left_loading():
    class BrokenStore:
        def running_task_ids(self):raise OSError('private database path')
    manager=TaskHistory(BrokenStore(),MAOSHANWANG)
    await manager.snapshot();await manager.refresh_task
    data=await manager.snapshot()
    assert not data['loading'] and all(s['status']=='error' for s in data['source_status'].values())
    assert data['warnings'] and 'private' not in str(data)
