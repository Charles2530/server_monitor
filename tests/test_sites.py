"""Multiple clouds must never share credentials, requests, history or mutations."""
import time
from dataclasses import replace
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from server.auth import load_session, save_session, load_credentials
from server.sites import MAOSHANWANG, SENSECORE
from server.collector import Collector
from server.acp_actions import ACPManager, ActionError
from server.task_logs import TaskLogs
from server.store import Store
from server.service import Monitor
from server.main import create_app, SiteRuntime


def test_credentials_and_sessions_are_site_scoped(tmp_path,monkeypatch):
    sites=[replace(s,data=tmp_path/s.id) for s in (MAOSHANWANG,SENSECORE)]
    for site in sites:
        for key in ('TENANT','USERNAME','PASSWORD'):monkeypatch.setenv(site.credentials_prefix+key,site.id+'-'+key)
        save_session({'headers':{'authorization':site.id}},site)
    for site in sites:
        assert load_session(site)['headers']['authorization']==site.id
        assert load_credentials(site)['tenant']==site.id+'-TENANT'
        assert (site.data/'session.json').stat().st_mode & 0o777==0o600


@pytest.mark.asyncio
async def test_cloud_destinations_and_target_guards_are_site_scoped(tmp_path):
    for site in (MAOSHANWANG,SENSECORE):
        calls=[]
        task={'name':'shared','id':site.workspace+'/trainingJobs/shared','display_name':'charles shared','uid':'shared-uid'}
        def handler(request):
            calls.append(request)
            assert request.url.host==httpx.URL(site.services['AEC2']).host
            assert site.workspace in request.url.path
            assert request.headers['authorization']==site.id
            if request.url.path.endswith('Own'):return httpx.Response(200,json={'training_jobs':[task]})
            if request.url.path.endswith('/workers'):return httpx.Response(200,json={'workers':[]})
            return httpx.Response(200,json=task)
        transport=httpx.MockTransport(handler)
        manager=ACPManager(Store(tmp_path/(site.id+'.db')),site=site,transport=transport,session_loader=lambda:{'headers':{'authorization':site.id}})
        logs=TaskLogs(site=site,transport=transport,session_loader=lambda:{'headers':{'authorization':site.id}})
        async with manager.client() as client:
            assert (await manager.detail(client,'shared'))['id']==task['id']
            await manager.write_once(client,manager.api+'/shared:stop')
        assert (await logs.targets('ACP:shared'))['workers']==[]
        task['id']=(SENSECORE if site==MAOSHANWANG else MAOSHANWANG).workspace+'/trainingJobs/shared'
        async with manager.client() as client:
            with pytest.raises(ActionError):await manager.detail(client,'shared')
        with pytest.raises(ActionError):await logs.targets('ACP:shared')
        assert sum(r.method=='POST' for r in calls)==1


def test_api_routes_history_logs_and_actions_without_global_switch(tmp_path):
    runtimes={};calls=[]
    class Actions:
        def __init__(self,site):self.site=site
        async def options(self):calls.append((self.site,'options'));return {'site':self.site}
        async def preview(self,data):calls.append((self.site,'preview'));return {'site':self.site}
        async def restart_options(self,task_id):return {'site':self.site}
        async def restart_preview(self,data):return {'site':self.site}
        async def create(self,preview):calls.append((self.site,'create'));raise ActionError('not in this site','preview_expired',409)
        async def stop(self,*args):calls.append((self.site,'stop'));return {'site':self.site}
    class Storage:
        def __init__(self,site):self.site=site
        async def snapshot(self):return {'site':self.site}
    class Logs:
        def __init__(self,site):self.site=site
        async def targets(self,task):return {'site':self.site}
        async def read(self,*args):return {'site':self.site}
    for n,site in enumerate((MAOSHANWANG,SENSECORE)):
        store=Store(tmp_path/(site.id+'.db'));now=time.time()
        store.save({'tasks':[{'id':'ACP:same','name':site.id,'kind':'ACP','series':[{'metric':'gpu','label':'GPU','points':[[now,n+1]]}]}],'clusters':[]},now)
        monitor=Monitor(store,None)
        async def refresh():return True
        monitor.refresh=refresh
        runtimes[site.id]=SiteRuntime(monitor,Actions(site.id),Logs(site.id),site,Storage(site.id),Storage(site.id))
    with TestClient(create_app(runtimes=runtimes,background=False),base_url='http://localhost:8766') as client:
        for site_id in ('sensecore','maoshanwang','sensecore','maoshanwang'):
            headers={'X-Monitor-Site':site_id,'X-Monitor-Request':'1'}
            state=client.get('/api/state',headers=headers).json()
            assert state['site']['id']==site_id and state['tasks'][0]['name']==site_id
            assert state['tasks'][0]['url'].startswith(runtimes[site_id].site.console+'/acp/list/detail?')
            history=client.get('/api/history?task_id=ACP:same',headers=headers).json()
            assert history['series'][0]['points'][0][1]==(2 if site_id=='sensecore' else 1)
            for path in ('/api/task-history','/api/storage','/api/acp/restart-options?task_id=ACP:same','/api/acp/options','/api/logs/targets?task_id=ACP:same','/api/logs?task_id=ACP:same&worker=w&container=c'):
                assert client.get(path,headers=headers).json()['site']==site_id
            assert client.post('/api/acp/preview',headers=headers,json={'display_name':'charles','replicas':1,'gpus_per_replica':1,'command':''}).json()['site']==site_id
            assert client.post('/api/acp/restart-preview',headers=headers,json={'task_id':'ACP:same','revision':'rev','command':''}).json()['site']==site_id
            assert client.post('/api/acp/stop',headers=headers,json={'task_id':'ACP:same','display_name':'charles','request_id':'id'}).json()['site']==site_id
        assert client.get('/api/state').json()['site']['id']=='maoshanwang'
        before=list(calls)
        for path in ('/api/acp/create','/api/acp/stop'):
            assert client.post(path,headers={'X-Monitor-Site':'invalid','X-Monitor-Request':'1'},json={}).status_code==400
        assert calls==before
        assert client.get('/api/state',headers={'X-Monitor-Site':'invalid'}).status_code==400


@pytest.mark.asyncio
async def test_preview_and_operation_ids_are_not_reused_across_sites(tmp_path):
    stores=[Store(tmp_path/(site.id+'.db')) for site in (MAOSHANWANG,SENSECORE)]
    key='a'*32
    stores[0].begin_operation(key,'create','same','charles')
    stores[0].finish_operation(key,'succeeded',result={'id':'ACP:same'})
    a=ACPManager(stores[0],site=MAOSHANWANG);b=ACPManager(stores[1],site=SENSECORE)
    assert await a.create(key)=={'id':'ACP:same'}
    with pytest.raises(ActionError) as e:await b.create(key)
    assert e.value.code=='preview_expired'


@pytest.mark.asyncio
async def test_log_token_cannot_cross_clouds_or_reach_arbitrary_endpoint():
    from tests.test_task_logs import LogCloud
    for site in (MAOSHANWANG,SENSECORE):
        cloud=LogCloud()
        requests=[]
        endpoint=site.log_endpoint
        async def handler(request):
            requests.append(request)
            response=await cloud(request)
            if request.url.path.endswith('/logLivestream/token'):
                body=__import__('json').loads(response.content);body['endpoint']=endpoint
                return httpx.Response(200,json=body)
            return response
        reader=TaskLogs(site=site,transport=httpx.MockTransport(handler),session_loader=lambda:{'headers':{'authorization':'private'}})
        reader.reader.workspace_uid='workspace-uid';reader.reader.station=site.workspace.split('/workspaces/')[0]+'/telemetryStations/tenant'
        assert (await reader.read('ACP:job-1','worker-0','pytorch'))['entries']==1
        poll=next(r for r in requests if '/v1/polling/' in r.url.path)
        assert poll.url.host==site.log_endpoint and 'authorization' not in poll.headers
        endpoint=(SENSECORE if site==MAOSHANWANG else MAOSHANWANG).log_endpoint
        requests.clear()
        with pytest.raises(ActionError):await reader.read('ACP:job-1','worker-0','pytorch')
        assert not any('/v1/polling/' in r.url.path for r in requests)
