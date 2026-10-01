import concurrent.futures
import sqlite3
import pytest
from server.mobile_access import DeviceStore, AccessError


def test_pair_approve_revoke_and_no_plaintext_secrets(tmp_path):
    path=tmp_path/'mobile.sqlite3';store=DeviceStore(path)
    ticket=store.issue_pairing('https://mac.example.ts.net',now=100)
    device=store.redeem(ticket['ticket'],'Pixel',now=110)
    assert store.status(device['token'],now=120)['state']=='pending'
    with pytest.raises(AccessError):store.authorize(device['token'],write=False,now=120)
    store.approve(device['device_id'],write=False,now=130)
    assert store.authorize(device['token'],write=False,now=140)['name']=='Pixel'
    with pytest.raises(AccessError):store.authorize(device['token'],write=True,now=140)
    store.approve(device['device_id'],write=True,now=150)
    store.authorize(device['token'],write=True,now=160)
    store.revoke(device['device_id'])
    with pytest.raises(AccessError):store.status(device['token'],now=170)
    assert ticket['ticket'].encode() not in path.read_bytes()
    assert device['token'].encode() not in path.read_bytes()


def test_ticket_single_use_atomic_and_expiry(tmp_path):
    store=DeviceStore(tmp_path/'db')
    ticket=store.issue_pairing('https://mac.example.ts.net',now=100)
    def redeem(_):
        try:return store.redeem(ticket['ticket'],'phone',now=101)
        except AccessError:return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(r is not None for r in pool.map(redeem,range(4)))==1
    expired=store.issue_pairing('https://mac.example.ts.net',now=100)
    with pytest.raises(AccessError):store.redeem(expired['ticket'],'phone',now=401)
    d=store.redeem(store.issue_pairing('https://mac.example.ts.net',now=500)['ticket'],'phone',now=501)
    with pytest.raises(AccessError):store.status(d['token'],now=1200)


@pytest.mark.parametrize('url',['http://mac.ts.net','https://evil.test','https://mac.ts.net@evil.test','https://mac.ts.net/path','https://mac.ts.net/?x=1','https://mac.ts.net#x'])
def test_invalid_pairing_hosts_rejected(tmp_path,url):
    with pytest.raises(ValueError):DeviceStore(tmp_path/'db').issue_pairing(url)


def test_approved_credentials_expire_and_unknown_devices_cannot_be_approved(tmp_path):
    store=DeviceStore(tmp_path/'db')
    device=store.redeem(store.issue_pairing('https://mac.tail.ts.net',now=100)['ticket'],'phone',now=101)
    store.approve(device['device_id'],now=102)
    with pytest.raises(AccessError):store.authorize(device['token'],now=102+90*86400)
    with pytest.raises(AccessError):store.approve('unknown')
    assert 'hash' not in store.devices()[0] and 'token' not in store.devices()[0]


def test_host_identity_persists_and_differs_for_another_database(tmp_path):
    a=DeviceStore(tmp_path/'a');again=DeviceStore(tmp_path/'a');b=DeviceStore(tmp_path/'b')
    assert len(a.host_id)==32 and a.host_id==again.host_id and a.host_id!=b.host_id
    device=a.redeem(a.issue_pairing('https://mac.tail.ts.net')['ticket'],'phone')
    assert device['host_id']==a.host_id==a.status(device['token'])['host_id']
