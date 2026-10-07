import asyncio
import json
import uuid

import httpx
import pytest

from server.acp_actions import ActionError
from server.sites import MAOSHANWANG, SENSECORE
from server.store import Store


def setup(tmp_path, site=MAOSHANWANG, kind='CCI'):
    from server.task_rename import TaskRenamer
    collection = 'apps' if kind == 'CCI' else 'trainingJobs'
    raw = {'id': site.workspace + '/' + collection + '/job-1', 'name': 'job-1',
           'display_name': 'charles_old', 'state': 'RUNNING'}
    calls = []
    mode = {'status': 200, 'timeout': False, 'apply': True, 'owned': True, 'readback_error': False}
    async def cloud(request):
        calls.append(request)
        if request.method == 'PATCH':
            if mode['apply'] and mode['status'] == 200:
                raw['display_name'] = json.loads(request.content)['display_name']
            if mode['timeout']:
                raise httpx.ReadTimeout('PRIVATE UPSTREAM TOKEN', request=request)
            return httpx.Response(mode['status'], json={'private': 'PRIVATE UPSTREAM TOKEN'})
        if request.url.path.endswith(collection + 'Own'):
            return httpx.Response(200, json={collection if kind == 'CCI' else 'training_jobs': [raw] if mode['owned'] else []})
        assert request.url.path.endswith(collection + '/job-1')
        if mode['readback_error'] and any(r.method == 'PATCH' for r in calls):
            return httpx.Response(403, json={})
        return httpx.Response(200, json=raw)
    store = Store(tmp_path / 'db')
    manager = TaskRenamer(store, site=site, transport=httpx.MockTransport(cloud), session_loader=lambda: {'headers': {}})
    return manager, raw, calls, mode


def form(kind='CCI', **changes):
    return dict(task_id=kind + ':job-1', expected_name='charles_old', display_name='charles_新名称-2.0:test',
                request_id=str(uuid.uuid4()), **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize('site', [MAOSHANWANG, SENSECORE])
@pytest.mark.parametrize('kind', ['CCI', 'ACP'])
async def test_rename_only_display_name_on_verified_own_task(tmp_path, site, kind):
    manager, raw, calls, mode = setup(tmp_path, site, kind)
    data = form(kind)
    result = await manager.rename(**data)
    assert result['task_id'] == data['task_id'] and result['site'] == site.id
    assert result['name'] == data['display_name'] and result['verified_at']
    writes = [r for r in calls if r.method == 'PATCH']
    assert len(writes) == 1
    req = writes[0]
    resource = 'apps' if kind == 'CCI' else 'trainingJobs'
    service = 'CCI' if kind == 'CCI' else 'AEC2'
    assert str(req.url) == site.services[service] + '/compute/' + kind.lower() + '/data/v2' + site.workspace + '/' + resource + '/job-1'
    assert json.loads(req.content) == {'display_name': data['display_name']}
    assert calls[0].method == 'GET' and calls[0].url.path.endswith(resource + 'Own')
    assert calls[-1].method == 'GET' and calls[-1].url.path.endswith(resource + '/job-1')
    assert raw['name'] == 'job-1' and raw['state'] == 'RUNNING'
    assert await manager.rename(**data) == result
    assert sum(r.method == 'PATCH' for r in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['', 'other_user', 'charles\nnew', 'charles name', 'charles<script>', '_charles', 'charles/' , 'charles' + 'x' * 250, None, True])
async def test_invalid_new_name_never_contacts_cloud(tmp_path, name):
    manager, _, calls, _ = setup(tmp_path)
    data = form(); data['display_name'] = name
    with pytest.raises(ActionError): await manager.rename(**data)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('task_id','CCI:../other'), ('task_id','UNKNOWN:job-1'), ('request_id','bad'), ('expected_name',None)])
async def test_invalid_identity_never_contacts_cloud(tmp_path, field, value):
    manager, _, calls, _ = setup(tmp_path)
    data = form(); data[field] = value
    with pytest.raises(ActionError): await manager.rename(**data)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['not_owned','workspace','name','scope','stale'])
async def test_ownership_and_stale_name_block_write(tmp_path, reason):
    manager, raw, calls, mode = setup(tmp_path)
    if reason == 'not_owned': mode['owned'] = False
    if reason == 'workspace': raw['id'] = SENSECORE.workspace + '/apps/job-1'
    if reason == 'name': raw['name'] = 'another-job'
    if reason == 'scope': raw['display_name'] = 'someone_else'
    if reason == 'stale': raw['display_name'] = 'charles_changed'
    with pytest.raises(ActionError): await manager.rename(**form())
    assert all(r.method == 'GET' for r in calls)


@pytest.mark.asyncio
async def test_unknown_write_is_reconciled_without_repeating_patch_even_after_restart(tmp_path):
    manager, raw, calls, mode = setup(tmp_path)
    data = form(); mode['timeout'] = True
    with pytest.raises(ActionError) as exc: await manager.rename(**data)
    assert exc.value.code == 'outcome_unknown'
    from server.task_rename import TaskRenamer
    restarted = TaskRenamer(manager.store, site=manager.site, transport=manager.transport, session_loader=manager.session_loader)
    result = await restarted.rename(**data)
    assert result['name'] == data['display_name']
    assert sum(r.method == 'PATCH' for r in calls) == 1
    other = {**data, 'display_name': 'charles_other'}
    with pytest.raises(ActionError) as exc: await restarted.rename(**other)
    assert exc.value.code == 'request_conflict'


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['unapplied','readback_error'])
async def test_unconfirmed_response_is_not_success_or_retried(tmp_path, reason):
    manager, _, calls, mode = setup(tmp_path)
    mode['apply'] = reason != 'unapplied'; mode['readback_error'] = reason == 'readback_error'
    data = form()
    for _ in range(2):
        with pytest.raises(ActionError) as exc: await manager.rename(**data)
        assert exc.value.code == 'outcome_unknown'
    assert sum(r.method == 'PATCH' for r in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status,code', [(403,'auth_required'), (400,'cloud_rejected'), (500,'outcome_unknown'), (408,'outcome_unknown')])
async def test_cloud_errors_are_sanitized_and_never_automatically_retried(tmp_path, status, code):
    manager, _, calls, mode = setup(tmp_path)
    mode['status'] = status
    data = form()
    with pytest.raises(ActionError) as exc: await manager.rename(**data)
    assert exc.value.code == code and 'PRIVATE' not in str(exc.value)
    with pytest.raises(ActionError): await manager.rename(**data)
    assert sum(r.method == 'PATCH' for r in calls) == 1


@pytest.mark.asyncio
async def test_same_name_noop_and_trim(tmp_path):
    manager, _, calls, _ = setup(tmp_path)
    data = form(); data['display_name'] = ' charles_old '
    assert (await manager.rename(**data))['name'] == 'charles_old'
    assert all(r.method == 'GET' for r in calls)


def test_route_guards_isolation_and_sanitized_errors(tmp_path):
    from fastapi.testclient import TestClient
    from server.main import create_app, SiteRuntime
    from server.service import Monitor
    seen = []
    class Renamer:
        def __init__(self, site): self.site = site
        async def rename(self, **data):
            if data['display_name'] == 'charles_fail': raise RuntimeError('PRIVATE TOKEN')
            seen.append((self.site, data))
            return {'site': self.site, 'task_id': data['task_id'], 'name': data['display_name']}
    runtimes = {}
    for site in (MAOSHANWANG, SENSECORE):
        monitor = Monitor(Store(tmp_path / site.id), None)
        monitor.request_refresh = lambda: {}
        runtime = SiteRuntime(monitor, object(), object(), site=site)
        runtime.renamer = Renamer(site.id)
        runtimes[site.id] = runtime
    app = create_app(runtimes=runtimes, background=False)
    with TestClient(app, base_url='http://localhost:8766') as client:
        headers = {'X-Monitor-Request':'1', 'X-Monitor-Site':'sensecore'}
        data = form('ACP')
        assert client.post('/api/tasks/rename',json=data).status_code == 403
        assert client.post('/api/tasks/rename',json=data,headers={**headers,'Origin':'http://evil.test'}).status_code == 403
        assert client.post('/api/tasks/rename',json={**data,'command':'bad'},headers=headers).status_code == 400
        response = client.post('/api/tasks/rename',json=data,headers=headers)
        assert response.status_code == 200 and response.json()['site'] == 'sensecore'
        assert seen == [('sensecore', data)]
        assert client.post('/api/tasks/rename',json=data,headers={**headers,'X-Monitor-Site':'evil'}).status_code == 400
        response = client.post('/api/tasks/rename',json={**data,'display_name':'charles_fail'},headers=headers)
        assert response.status_code == 503 and 'PRIVATE' not in response.text
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/api/tasks/rename').status_code == 405


@pytest.mark.parametrize('write', [False, True])
def test_mobile_rename_requires_write_permission(tmp_path, write):
    from tests.test_mobile_gateway import setup as mobile
    _, device, calls, client = mobile(tmp_path, write=write)
    with client:
        assert client.post('/api/tasks/rename',json=form()).status_code == 401
        r = client.post('/api/tasks/rename', json=form(), headers={'Authorization':'Bearer '+device['token'], 'X-Monitor-Site':'sensecore'})
        assert r.status_code == (200 if write else 403)
    assert len(calls) == int(write)


@pytest.mark.asyncio
async def test_cancelled_write_leaves_record_and_replay_never_rewrites(tmp_path):
    from server.task_rename import TaskRenamer
    started = asyncio.Event()
    calls = []
    raw = {'id':MAOSHANWANG.workspace+'/apps/job-1','name':'job-1','display_name':'charles_old'}
    async def cloud(request):
        calls.append(request.method)
        if request.method == 'PATCH':
            raw['display_name'] = json.loads(request.content)['display_name']
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200,json={'apps':[raw]} if request.url.path.endswith('appsOwn') else raw)
    manager = TaskRenamer(Store(tmp_path/'cancel-db'),site=MAOSHANWANG,
                          transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{}})
    data = form()
    task = asyncio.create_task(manager.rename(**data))
    await asyncio.wait_for(started.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    result = await manager.rename(**data)
    assert result['name'] == data['display_name'] and calls.count('PATCH') == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_saves_write_once(tmp_path):
    manager, _, calls, _ = setup(tmp_path)
    data = form()
    first, second = await asyncio.gather(manager.rename(**data),manager.rename(**data))
    assert first == second
    assert sum(r.method == 'PATCH' for r in calls) == 1


@pytest.mark.asyncio
async def test_check_only_without_record_never_submits_or_contacts_cloud(tmp_path):
    manager, _, calls, _ = setup(tmp_path)
    with pytest.raises(ActionError) as exc:
        await manager.rename(**form(), check_only=True)
    assert exc.value.code == 'not_submitted'
    assert not calls


@pytest.mark.asyncio
async def test_check_only_reconciles_existing_unknown_record(tmp_path):
    manager, _, calls, mode = setup(tmp_path)
    data = form(); mode['timeout'] = True
    with pytest.raises(ActionError): await manager.rename(**data)
    result = await manager.rename(**data, check_only=True)
    assert result['name'] == data['display_name']
    assert sum(r.method == 'PATCH' for r in calls) == 1
