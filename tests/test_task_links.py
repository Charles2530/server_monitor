"""Console deep links must use each site's trusted workspace and route."""
from urllib.parse import urlparse,parse_qs
import pytest
from server.normalize import task_console_url
from server.sites import MAOSHANWANG,SENSECORE

@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE])
@pytest.mark.parametrize('kind',['CCI','ACP'])
def test_links_route_to_selected_site_workspace_and_task(site,kind):
    result=task_console_url({'id':kind+':task-123','kind':kind},site)
    url=urlparse(result);query=parse_qs(url.query)
    assert result.startswith(site.console+'/')
    assert query['workspace']==[site.workspace]
    if kind=='CCI':
        assert url.path.endswith('/cci/app/detail') and query['name']==['task-123']
    else:
        assert url.path.endswith('/acp/list/detail')
        assert query['id']==[site.workspace+'/trainingJobs/task-123']
        assert query['tab']==['detail']

@pytest.mark.parametrize('task',[{'id':'ACP:../foreign','kind':'ACP'},{'id':'CCI:task','kind':'ACP'},{'id':'ACP:','kind':'ACP'},{'id':'http://evil','kind':'CCI'}])
def test_invalid_task_has_no_link(task):
    assert task_console_url(task,SENSECORE) is None

@pytest.mark.parametrize('site',[MAOSHANWANG,SENSECORE])
def test_state_adds_waiting_links_for_all_users_without_mutating_snapshot(tmp_path,site):
    from fastapi.testclient import TestClient
    from server.main import create_app,SiteRuntime
    from server.service import Monitor
    from server.store import Store
    monitor=Monitor(Store(tmp_path/'db'),None)
    monitor.state['waiting_tasks']=[{'id':kind+':other-user','kind':kind,'name':'someone_else','state':'STARTING','url':'https://untrusted.example'} for kind in ('CCI','ACP')]
    runtime=SiteRuntime(monitor,None,None,site)
    runtimes={'maoshanwang':SiteRuntime(Monitor(Store(tmp_path/'default-db'),None),None,None),site.id:runtime}
    with TestClient(create_app(runtimes=runtimes,background=False),base_url='http://localhost:8766') as client:
        tasks=client.get('/api/state',headers={'X-Monitor-Site':site.id}).json()['waiting_tasks']
        for task in tasks:
            assert task['url']==task_console_url(task,site)
            assert task['name']=='someone_else'
    assert all(t['url']=='https://untrusted.example' for t in monitor.state['waiting_tasks'])
