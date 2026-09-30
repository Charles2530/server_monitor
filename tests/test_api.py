from fastapi.testclient import TestClient
from server.main import create_app
from server.service import Monitor
from server.store import Store


def test_local_boundaries_and_state_redaction(tmp_path):
    monitor=Monitor(Store(tmp_path/'db'),None)
    monitor.state['tasks']=[{'id':'CCI:x','uid':'private','source_id':'x','series':[1]}]
    with TestClient(create_app(monitor,background=False),base_url='http://localhost:8765') as client:
        assert client.get('/').status_code==200
        task=client.get('/api/state').json()['tasks'][0]
        assert task=={'id':'CCI:x','url':None}
        assert client.get('/data/session.json').status_code==404
        assert client.get('/static/../data/session.json').status_code==404
        assert client.get('/api/state',headers={'Host':'evil.test'}).status_code==403
        assert client.post('/api/refresh').status_code==403
        assert client.post('/api/connect',headers={'X-Monitor-Request':'1','Origin':'https://evil.test'}).status_code==403
        assert client.get('/api/history?task_id=x&hours=169').status_code==422


def test_acp_routes_reject_cross_site_invalid_bodies_and_preserve_error_sanitization(tmp_path):
    from server.acp_actions import ActionError
    class Actions:
        async def options(self,partition_id=None):return {'template':{'id':'ACP:pt-1'},'gpu_options':[1],'partition_id':partition_id}
        async def preview(self,data):return {'preview_id':'p','command':data['command']}
        async def create(self,preview_id):raise ActionError('结果待核实。','outcome_unknown',502)
        async def stop(self,*args):raise RuntimeError('SECRET CLOUD ERROR')
    monitor=Monitor(Store(tmp_path/'db'),None)
    with TestClient(create_app(monitor,background=False,actions=Actions()),base_url='http://localhost:8765') as client:
        assert client.get('/api/acp/options').json()['gpu_options']==[1]
        assert client.get('/api/acp/options?partition_id=cn-sh-01b').json()['partition_id']=='cn-sh-01b'
        assert client.get('/api/acp/options?partition_id='+('x'*254)).status_code==422
        assert client.post('/api/acp/create',json={'preview_id':'p'}).status_code==403
        headers={'X-Monitor-Request':'1'}
        form={'display_name':'charles_test','replicas':1,'gpus_per_replica':1,'command':''}
        for extra in ({},{'cluster_id':''},{'cluster_id':'vigen'},{'partition_id':'cn-sh-01b','cluster_id':'l40s-mtc'}):
            assert client.post('/api/acp/preview',json={**form,**extra},headers=headers).status_code==200
        assert client.post('/api/acp/preview',json={**form,'unexpected':1},headers=headers).status_code==400
        assert client.post('/api/acp/preview',json=[],headers=headers).status_code==400
        assert client.post('/api/acp/preview',content='invalid',headers=headers).status_code==400
        assert client.post('/api/acp/create',json={'preview_id':'p','payload':'forbidden'},headers=headers).status_code==400
        result=client.post('/api/acp/create',json={'preview_id':'p'},headers=headers)
        assert result.status_code==502 and result.json()['code']=='outcome_unknown'
        result=client.post('/api/acp/stop',json={'task_id':'ACP:pt-1','display_name':'charles','request_id':'x'},headers=headers)
        assert result.status_code==503 and 'SECRET' not in result.text
        assert client.post('/api/acp/preview',json={},headers={**headers,'Origin':'http://evil.test'}).status_code==403


def test_log_routes_validate_queries_and_keep_errors_private(tmp_path):
    class Logs:
        async def targets(self,task_id):return {'task_id':task_id,'workers':[]}
        async def read(self,*args):raise RuntimeError('PRIVATE LOG TOKEN')
    monitor=Monitor(Store(tmp_path/'db'),None)
    with TestClient(create_app(monitor,background=False,logs=Logs()),base_url='http://localhost:8765') as client:
        r=client.get('/api/logs/targets',params={'task_id':'ACP:pt-1'})
        assert r.status_code==200 and r.json()['workers']==[]
        assert r.headers['cache-control']=='no-store'
        params={'task_id':'ACP:pt-1','worker':'worker','container':'pytorch'}
        r=client.get('/api/logs',params={**params,'tail':1001})
        assert r.status_code==422
        r=client.get('/api/logs',params=params)
        assert r.status_code==503 and 'PRIVATE' not in r.text
        assert client.get('/api/logs',params=params,headers={'Origin':'https://evil.test'}).status_code==403


def test_restart_routes_validate_readonly_preparation_and_preview(tmp_path):
    class Actions:
        async def restart_options(self,task_id):return {'task_id':task_id,'command':'private-command'}
        async def restart_preview(self,data):return {'preview_id':'prepared','command':data['command']}
    monitor=Monitor(Store(tmp_path/'db'),None)
    with TestClient(create_app(monitor,background=False,actions=Actions()),base_url='http://localhost:8765') as client:
        response=client.get('/api/acp/restart-options?task_id=ACP:old')
        assert response.json()['task_id']=='ACP:old' and response.headers['cache-control']=='no-store'
        assert client.get('/api/acp/restart-options').status_code==422
        body={'task_id':'ACP:old','revision':'old','command':'python retry.py'}
        assert client.post('/api/acp/restart-preview',json=body).status_code==403
        headers={'X-Monitor-Request':'1'}
        assert client.post('/api/acp/restart-preview',json=body,headers=headers).json()['preview_id']=='prepared'
        assert client.post('/api/acp/restart-preview',json={**body,'payload':{}},headers=headers).status_code==400
        assert client.post('/api/acp/restart-preview',json=body,headers={**headers,'Origin':'https://evil.test'}).status_code==403
