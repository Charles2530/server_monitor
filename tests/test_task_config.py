import copy,json
import httpx,pytest
from server.acp_actions import ACPManager,ActionError
from server.sites import MAOSHANWANG,SENSECORE
from server.store import Store
from tests.test_acp_actions import job


@pytest.mark.asyncio
@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE])
async def test_exact_task_configuration_readonly_and_allowlisted(tmp_path,site):
    raw=job('pt-selected',state='RUNNING');raw['id']=site.workspace+'/trainingJobs/pt-selected'
    command="echo '<script>do_not_execute()</script>'\npython train.py --steps 0\n"
    raw['roles'][0]['startup_script']=command
    raw['roles'].append({'name':'Evaluator','total_replicas':0,'image_path':'eval:latest','startup_script':'','resource_spec':[{'name':'cpu-only','replicas':0}]})
    raw['mount'].append({'type':'PV_AFS','id':'disk-2','zone':'zone-b','subdir':'/datasets','mount_path':'/data','credentials':'DO_NOT_EXPOSE'})
    requests=[]
    async def cloud(request):
        requests.append(request)
        assert request.method=='GET' and request.url.path.endswith('/trainingJobs/pt-selected')
        return httpx.Response(200,json=raw)
    manager=ACPManager(Store(tmp_path/'db'),site=site,transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{}})
    result=await manager.task_config('ACP:pt-selected')
    assert result['site']==site.id and result['task_id']=='ACP:pt-selected'
    assert result['roles'][0]['command']==command
    assert result['roles'][1]['replicas']==0 and result['roles'][1]['command']==''
    assert result['roles'][0]['image']=='private/image:v13'
    assert len(result['afs_mounts'])==2 and result['afs_mounts'][1]['mount_path']=='/data'
    assert result['cluster']=='modeltoolchain-test' and result['framework']=='PYTORCH'
    assert len(requests)==1
    for private in ('private-env','private-uid','vpc_id','DO_NOT_EXPOSE','auto_key_setup','SECRET_OLD_SCRIPT'):
        assert private not in json.dumps(result)
    assert raw['roles'][0]['startup_script']==command


@pytest.mark.asyncio
async def test_configuration_rejects_non_own_mismatched_workspace_and_cci(tmp_path):
    raw=job('pt-selected');requests=[]
    async def cloud(request):requests.append(request);return httpx.Response(200,json=raw)
    manager=ACPManager(Store(tmp_path/'db'),site=MAOSHANWANG,transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{}})
    for task in ('CCI:app-test','ACP:../bad'):
        with pytest.raises(ActionError):await manager.task_config(task)
    assert not requests
    for field,value in [('display_name','someone else'),('id',SENSECORE.workspace+'/trainingJobs/pt-selected')]:
        old=raw[field];raw[field]=value
        with pytest.raises(ActionError):await manager.task_config('ACP:pt-selected')
        raw[field]=old


def test_readonly_config_endpoint_guards_and_sanitized_errors(tmp_path):
    from fastapi.testclient import TestClient
    from server.main import create_app
    from server.service import Monitor
    class Actions:
        async def task_config(self,task_id):
            if task_id=='ACP:fail':raise RuntimeError('PRIVATE SOURCE TOKEN')
            return {'task_id':task_id,'roles':[]}
    with TestClient(create_app(Monitor(Store(tmp_path/'db'),None),actions=Actions(),background=False),base_url='http://localhost:8766') as client:
        assert client.get('/api/acp/task-config',params={'task_id':'ACP:job'}).json()['task_id']=='ACP:job'
        assert client.get('/api/acp/task-config').status_code==422
        assert client.get('/api/acp/task-config',params={'task_id':'ACP:job'},headers={'Origin':'http://evil.test'}).status_code==403
        response=client.get('/api/acp/task-config',params={'task_id':'ACP:fail'})
        assert response.status_code==503 and 'PRIVATE' not in response.text
        assert response.headers['cache-control']=='no-store'
        assert client.post('/api/acp/task-config',headers={'X-Monitor-Request':'1'}).status_code==405


def test_mobile_configuration_is_authenticated_read_only(tmp_path):
    from tests.test_mobile_gateway import setup
    _,device,calls,client=setup(tmp_path)
    path='/api/acp/task-config?task_id=ACP:example'
    with client:
        assert client.get(path).status_code==401
        headers={'Authorization':'Bearer '+device['token'],'X-Monitor-Site':'sensecore'}
        assert client.get(path,headers=headers).status_code==200
        assert client.post(path,headers=headers,json={}).status_code in (403,404)
    assert len(calls)==1 and calls[0].url.params['task_id']=='ACP:example'
