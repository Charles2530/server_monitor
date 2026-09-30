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
