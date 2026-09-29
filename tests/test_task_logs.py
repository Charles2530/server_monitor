import base64
import json
import httpx
import pytest
from server.config import WORKSPACE
from server.acp_actions import ActionError


class LogCloud:
    def __init__(self):
        self.requests=[];self.foreign=False;self.bad_endpoint=False;self.poll_error=False
        self.entries=[{'type':'LOG','content':base64.b64encode('你好 <script>alert(1)</script>\n\x1b[31mred\x1b[0m\n'.encode()).decode()}, {'type':'HEARTBEAT'}, {'type':'EOF'}]
    async def __call__(self,r):
        self.requests.append(r)
        path=r.url.path
        if path.endswith(('appsOwn','trainingJobsOwn')):
            kind='CCI' if path.endswith('appsOwn') else 'ACP'
            key='apps' if kind=='CCI' else 'training_jobs'
            return httpx.Response(200,json={key:[{'name':'job-1','uid':'uid-1','display_name':'someone' if self.foreign else 'charles_train','state':'RUNNING'}]})
        if path.endswith('/workers'):
            return httpx.Response(200,json={'workers':[{'name':'worker-0','phase':'Running','containers':[{'name':'pytorch'}],'init_containers':[{'name':'barrier'}],'ip':'private-ip'}]})
        if path.endswith('/instances'):
            return httpx.Response(200,json={'instances':[{'name':'pod-0','state':'RUNNING','container_infos':[{'container_name':'template'}],'instance_ip':'private-ip'}]})
        if path.endswith('/logLivestream/token'):
            return httpx.Response(200,json={'token':'PRIVATE_LOG_TOKEN','endpoint':'evil.example' if self.bad_endpoint else 'monitor-data.ms-sc-01.maoshanwangtechapi.com','expire_time':'2099-01-01T00:00:00Z'})
        if '/v1/polling/resources/' in path:
            if self.poll_error:return httpx.Response(500,text='PRIVATE_RAW_ERROR')
            return httpx.Response(200,json={'entries':self.entries,'last_entry_id':'opaque-cursor'})
        raise AssertionError('Unexpected cloud path')


def manager(cloud):
    from server.task_logs import TaskLogs
    m=TaskLogs(transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{'authorization':'PRIVATE_IAM_TOKEN'}})
    m.reader.workspace_uid='workspace-uid';m.reader.station=WORKSPACE.split('/workspaces/')[0]+'/telemetryStations/tenant'
    return m

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,worker,container',[('ACP','worker-0','pytorch'),('CCI','pod-0','template')])
async def test_targets_and_logs_use_verified_member_and_workload_filters(kind,worker,container):
    cloud=LogCloud();m=manager(cloud)
    targets=await m.targets(kind+':job-1')
    assert targets['workers'][0]['name']==worker
    assert container in targets['workers'][0]['containers']
    assert 'private-ip' not in json.dumps(targets)
    result=await m.read(kind+':job-1',worker,container,1000)
    assert result['text']=='你好 <script>alert(1)</script>\nred\n'
    assert result['entries']==1 and result['eof'] is True
    assert 'TOKEN' not in json.dumps(result)
    own=next(r for r in cloud.requests if r.url.path.endswith(('trainingJobsOwn','appsOwn')))
    assert own.url.params['filter']=="name='job-1'"
    token=next(r for r in cloud.requests if r.url.path.endswith('/logLivestream/token'))
    body=json.loads(token.content)
    assert body['resource_id']=='workspace-uid'
    filters={x['key']:x['val'] for x in body['filters']}
    assert filters=={'pod':worker,'container':container,'lepton.sensetime.com/workload-name':'job-1','lepton.sensetime.com/workload-uid':'uid-1','lepton.sensetime.com/workload-type':kind.lower()}
    poll=next(r for r in cloud.requests if '/v1/polling/' in r.url.path)
    assert json.loads(poll.content)=={'token':'PRIVATE_LOG_TOKEN','resource_id':'workspace-uid','tail':1000}
    assert 'authorization' not in poll.headers
    assert all(r.method=='GET' or r.url.path.endswith('/logLivestream/token') or '/v1/polling/' in r.url.path for r in cloud.requests)

@pytest.mark.asyncio
async def test_foreign_or_unknown_target_never_requests_log_token():
    for task,worker,container,foreign in [('ACP:../x','worker-0','pytorch',False),('ACP:job-1','worker-0','pytorch',True),('ACP:job-1','other-worker','pytorch',False),('ACP:job-1','worker-0','other-container',False),('ACP:unknown','worker-0','pytorch',False)]:
        cloud=LogCloud();cloud.foreign=foreign;m=manager(cloud)
        with pytest.raises(ActionError):await m.read(task,worker,container,1000)
        assert not any(r.method=='POST' for r in cloud.requests)

@pytest.mark.asyncio
async def test_untrusted_poll_host_is_rejected_before_transmitting_token():
    cloud=LogCloud();cloud.bad_endpoint=True;m=manager(cloud)
    with pytest.raises(ActionError) as exc:await m.read('ACP:job-1','worker-0','pytorch',1000)
    assert exc.value.code=='logs_unavailable'
    assert not any('/v1/polling/' in r.url.path for r in cloud.requests)

@pytest.mark.asyncio
async def test_log_failure_is_sanitized():
    cloud=LogCloud();cloud.poll_error=True;m=manager(cloud)
    with pytest.raises(ActionError) as exc:await m.read('ACP:job-1','worker-0','pytorch',1000)
    assert 'PRIVATE' not in str(exc.value)

@pytest.mark.asyncio
async def test_no_logs_eof_and_bounded_output():
    cloud=LogCloud();m=manager(cloud);cloud.entries=[]
    result=await m.read('ACP:job-1','worker-0','pytorch',1000)
    assert result['text']=='' and result['entries']==0 and not result['eof']
    cloud.entries=[{'type':'LOG','content':base64.b64encode(b'x'*1200000).decode()}]
    result=await m.read('ACP:job-1','worker-0','pytorch',1000)
    assert len(result['text'])<=1000000 and result['truncated'] and result['warning']

@pytest.mark.asyncio
async def test_invalid_log_tail_rejected_before_cloud():
    cloud=LogCloud();m=manager(cloud)
    for tail in [0,1001,True,'10']:
        with pytest.raises(ActionError):await m.read('ACP:job-1','worker-0','pytorch',tail)
    assert cloud.requests==[]
