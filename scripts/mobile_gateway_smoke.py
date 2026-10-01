"""Read-only smoke check against an already running 8766 monitor, without installing a service."""
from pathlib import Path
import sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from server.mobile_access import DeviceStore
from server.mobile_gateway import create_gateway

with tempfile.TemporaryDirectory() as directory:
    store=DeviceStore(Path(directory)/'devices.sqlite3')
    ticket=store.issue_pairing('https://smoke.example.ts.net')
    device=store.redeem(ticket['ticket'],'smoke');store.approve(device['device_id'])
    with TestClient(create_gateway(store)) as client:
        headers={'Authorization':'Bearer '+device['token']}
        assert client.get('/api/state').status_code==401
        response=client.get('/',headers=headers)
        assert response.status_code==200 and 'text/html' in response.headers['content-type']
        for site in ('maoshanwang','sensecore'):
            response=client.get('/api/state',headers={**headers,'X-Monitor-Site':site})
            assert response.status_code==200 and response.json()['site']['id']==site
            print(site+': live read through authenticated gateway passed')
        assert client.post('/api/acp/stop',headers=headers,json={}).status_code==403
        store.revoke(device['device_id'])
        assert client.get('/api/state',headers=headers).status_code==401
    print('Gateway smoke passed: both sites, HTML, auth, read-only enforcement, revocation; no cloud writes.')
