import asyncio

import pytest

from server.acp_actions import ActionError
from tests.test_ide_recovery import DATA, ORIGIN, existing_editor


def test_details_route_rejects_cross_origin_and_unscoped_requests(tmp_path):
    from fastapi.testclient import TestClient
    from server.main import create_app
    from server.service import Monitor
    from server.store import Store
    app = create_app(Monitor(Store(tmp_path / 'db'), None), background=False)
    with TestClient(app, base_url=ORIGIN) as client:
        path = '/api/cci/ide/details'
        headers = {'X-Monitor-Request': '1'}
        assert client.post(path, json=DATA).status_code == 403
        assert client.post(path, json=DATA, headers={**headers, 'origin': 'http://evil.test'}).status_code == 403
        assert client.post(path, json={**DATA, 'url': 'http://arbitrary'}, headers=headers).status_code == 400
        assert client.post(path, json=DATA, headers=headers).status_code == 404


@pytest.mark.asyncio
async def test_setup_status_tracks_background_completion_without_cloud_or_credentials(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch)
    identity = (*DATA.values(), ORIGIN)
    session['codex'] = {'configured': True, 'installed': False, 'secret': 'never-send'}
    session['appearance'] = {'installed': False, 'version': 'old'}
    release = asyncio.Event()
    async def install():
        await release.wait()
        session['codex']['installed'] = True
        session['appearance'] = {'installed': True, 'version': 'new'}
    editor.optional_jobs[identity] = asyncio.create_task(install())
    try:
        pending = editor.details(DATA, ORIGIN)
        assert pending['optional_pending'] is True
        assert pending['codex'] == {'configured': True, 'installed': False}
        with pytest.raises(ActionError): editor.details(DATA, 'http://localhost:9000')
        with pytest.raises(ActionError): editor.details({**DATA, 'container': 'other'}, ORIGIN)
        release.set()
        await editor.optional_jobs[identity]
        complete = editor.details(DATA, ORIGIN)
        assert complete['optional_pending'] is False
        assert complete['appearance'] == {'installed': True, 'version': 'new'}
        assert complete['codex']['installed'] is True
        assert pending['codex']['installed'] is False
        assert 'password' not in complete and 'ticket' not in complete
        recovered = await editor.recover(DATA, ORIGIN)
        assert recovered['codex'] == complete['codex']
    finally:
        await editor.close()
