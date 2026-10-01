"""ACP links must target a running worker of the verified personal job."""
import json
from urllib.parse import parse_qs,urlparse
import httpx,pytest
from server.acp_actions import ActionError
from server.sites import MAOSHANWANG,SENSECORE

class Cloud:
    def __init__(self,site):
        self.site=site;self.requests=[]
        self.job={'name':'pt-test','id':site.workspace+'/trainingJobs/pt-test','display_name':'charles job','state':'RUNNING','command':'PRIVATE'}
        self.workers=[{'name':'pt-uid-worker-0','phase':'Running','containers':[{'name':'pytorch'}]},
                      {'name':'pt-uid-worker-1','phase':'Pending'}, {'name':'pt-uid-worker-2','phase':'Failed'}]
    async def __call__(self,req):
        self.requests.append(req);assert req.method=='GET'
        assert req.url.host==httpx.URL(self.site.services['AEC2']).host
        base='/compute/acp/data/v2'+self.site.workspace+'/trainingJobs'
        if req.url.path==base+'Own':
            assert req.url.params['filter']=="name='pt-test'"
            return httpx.Response(200,json={'training_jobs':[self.job]})
        assert req.url.path==base+'/pt-test/workers'
        return httpx.Response(200,json={'workers':self.workers})

def manager(cloud):
    from server.acp_terminal import ACPTerminal
    return ACPTerminal(cloud.site,transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{'authorization':'PRIVATE'}})

@pytest.mark.asyncio
@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE])
async def test_running_workers_site_scoped_and_read_only(site):
    cloud=Cloud(site);result=await manager(cloud).targets('ACP:pt-test')
    assert result['site']==site.id and result['task_id']=='ACP:pt-test'
    assert len(result['targets'])==1
    target=result['targets'][0]
    assert target['url'].startswith(site.console+'/acp/terminal?')
    assert parse_qs(urlparse(target['url']).query)=={'workspace':[site.workspace],'job_id':['pt-test'],'pod_name':['pt-uid-worker-0']}
    assert target['instance']=='pt-uid-worker-0' and 'PRIVATE' not in json.dumps(result)
    assert len(cloud.requests)==2

@pytest.mark.asyncio
async def test_reject_invalid_foreign_and_stopped_job():
    cloud=Cloud(MAOSHANWANG)
    for task in ('CCI:pt-test','ACP:../invalid','ACP:'):
        with pytest.raises(ActionError):await manager(cloud).targets(task)
    assert not cloud.requests
    for key,value in [('display_name','someone_else'),('name','other'),('id',SENSECORE.workspace+'/trainingJobs/pt-test'),('state','STOPPED')]:
        cloud=Cloud(MAOSHANWANG);cloud.job[key]=value
        with pytest.raises(ActionError):await manager(cloud).targets('ACP:pt-test')
        assert len(cloud.requests)==1

@pytest.mark.asyncio
async def test_multiple_duplicate_invalid_and_empty_workers():
    cloud=Cloud(MAOSHANWANG)
    cloud.workers += [dict(cloud.workers[0]),{'name':'pt-uid-master-0','phase':'Running'}, {'name':'../escape','phase':'Running'}]
    result=await manager(cloud).targets('ACP:pt-test')
    assert [t['instance'] for t in result['targets']]==['pt-uid-worker-0','pt-uid-master-0']
    cloud.workers=[]
    assert (await manager(cloud).targets('ACP:pt-test'))['targets']==[]
