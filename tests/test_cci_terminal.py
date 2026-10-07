"""Terminal links resolve current running containers without opening shells server-side."""
import json
from urllib.parse import urlparse,parse_qs
import httpx,pytest
from server.acp_actions import ActionError
from server.sites import MAOSHANWANG,SENSECORE

class Cloud:
    def __init__(self,site):
        self.site=site;self.requests=[]
        self.app={'name':'app-test','id':site.workspace+'/apps/app-test','display_name':'charles container','state':'RUNNING','env':'PRIVATE'}
        self.instances=[{'name':'pod-running','app_name':'app-test','state':'RUNNING','instance_ip':'PRIVATE',
                         'container_infos':[{'container_name':'main','container_type':'MAIN','container_state':'RUNNING'},
                                            {'container_name':'init','container_type':'INIT','container_state':'RUNNING'},
                                            {'container_name':'stopped','container_type':'MAIN','container_state':'TERMINATED'}]},
                        {'name':'pod-stopped','app_name':'app-test','state':'SUSPENDED','container_infos':[{'container_name':'main','container_type':'MAIN','container_state':'RUNNING'}]}]
    async def __call__(self,req):
        self.requests.append(req);assert req.method=='GET'
        assert req.url.host==httpx.URL(self.site.services['CCI']).host and self.site.workspace in req.url.path
        if req.url.path.endswith('appsOwn'):
            assert req.url.params['filter']=="name='app-test'"
            return httpx.Response(200,json={'apps':[self.app]})
        assert req.url.path.endswith('/apps/app-test/instances')
        return httpx.Response(200,json={'instances':self.instances})

def manager(cloud):
    from server.cci_terminal import CCITerminal
    return CCITerminal(cloud.site,transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{'authorization':'PRIVATE_TOKEN'}})

@pytest.mark.asyncio
@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE])
async def test_terminal_targets_only_running_main_containers_in_selected_site(site):
    cloud=Cloud(site);data=await manager(cloud).targets('CCI:app-test')
    assert data['site']==site.id and data['name']=='charles container' and len(data['targets'])==1
    target=data['targets'][0];url=urlparse(target['url']);query=parse_qs(url.query)
    assert target['url'].startswith(site.console+'/cci/terminal?')
    assert query=={'workspace':[site.workspace],'app_name':['app-test'],'container_instance_name':['pod-running'],'container_name':['main']}
    assert target['instance']=='pod-running' and target['container']=='main'
    assert 'PRIVATE' not in json.dumps(data) and len(cloud.requests)==2

@pytest.mark.asyncio
async def test_invalid_and_foreign_task_never_opens_terminal():
    cloud=Cloud(MAOSHANWANG);m=manager(cloud)
    for target in ('ACP:app-test','CCI:../app','CCI:'):
        with pytest.raises(ActionError):await m.targets(target)
    assert not cloud.requests
    for field,value in [('display_name','someone_else'),('id',SENSECORE.workspace+'/apps/app-test'),('name','other'),('state','SUSPENDED')]:
        cloud=Cloud(MAOSHANWANG);cloud.app[field]=value
        with pytest.raises(ActionError):await manager(cloud).targets('CCI:app-test')
        assert len(cloud.requests)==1

@pytest.mark.asyncio
async def test_multiple_containers_stale_instances_and_empty_targets():
    cloud=Cloud(MAOSHANWANG)
    cloud.instances[0]['container_infos'].append({'container_name':'second','container_type':'MAIN','container_state':'RUNNING'})
    assert len((await manager(cloud).targets('CCI:app-test'))['targets'])==2
    cloud.instances[0]['app_name']='foreign'
    assert (await manager(cloud).targets('CCI:app-test'))['targets']==[]
    cloud.instances[0]['app_name']='app-test';cloud.instances[0]['name']='../invalid'
    assert (await manager(cloud).targets('CCI:app-test'))['targets']==[]

@pytest.mark.asyncio
async def test_recent_target_resolution_can_be_reused_for_terminal_open():
    cloud=Cloud(MAOSHANWANG);m=manager(cloud)
    first=await m.targets('CCI:app-test')
    second=await m.targets('CCI:app-test',reuse=True)
    assert second==first and len(cloud.requests)==2
