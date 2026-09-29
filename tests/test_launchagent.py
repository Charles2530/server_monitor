from scripts import monitorctl as ctl
import pytest


def test_launchagent_start_does_not_spawn_detached_process(monkeypatch, tmp_path):
    monkeypatch.setattr(ctl.sys,'platform','darwin')
    plist=tmp_path/'monitor.plist';plist.write_text('configured')
    monkeypatch.setattr(ctl,'AGENT_PLIST',plist)
    monkeypatch.setattr(ctl,'agent_loaded',lambda:False)
    calls=[]
    monkeypatch.setattr(ctl,'launchctl',lambda *args:calls.append(args))
    monkeypatch.setattr(ctl,'wait_for_api',lambda:None)
    assert ctl.agent_action('start') is True
    assert calls==[('bootstrap',ctl.AGENT_DOMAIN,str(plist))]


def test_launchagent_stop_unloads_keepalive(monkeypatch):
    monkeypatch.setattr(ctl.sys,'platform','darwin')
    monkeypatch.setattr(ctl,'agent_loaded',lambda:True)
    calls=[]
    monkeypatch.setattr(ctl,'launchctl',lambda *args:calls.append(args))
    assert ctl.agent_action('stop') is True
    assert calls==[('bootout',ctl.AGENT_TARGET)]


def test_no_agent_preserves_standalone_mode(monkeypatch,tmp_path):
    monkeypatch.setattr(ctl.sys,'platform','darwin')
    monkeypatch.setattr(ctl,'AGENT_PLIST',tmp_path/'missing')
    monkeypatch.setattr(ctl,'agent_loaded',lambda:False)
    assert ctl.agent_action('start') is False


def test_linux_never_calls_launchctl_even_if_a_plist_exists(monkeypatch,tmp_path):
    monkeypatch.setattr(ctl.sys,'platform','linux')
    plist=tmp_path/'old.plist';plist.write_text('copied from Mac')
    monkeypatch.setattr(ctl,'AGENT_PLIST',plist)
    def unexpected(*args,**kwargs):raise AssertionError('Linux must not invoke launchctl')
    monkeypatch.setattr(ctl.subprocess,'run',unexpected)
    assert ctl.agent_loaded() is False
    for action in ('start','status','stop'):
        assert ctl.agent_action(action) is False


@pytest.mark.parametrize('occupied',[True,False])
def test_deploy_rejects_an_existing_listener(monkeypatch,occupied):
    from scripts import deploy_launchagent as deploy
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def connect_ex(self,address):return 0 if occupied else 61
    monkeypatch.setattr(ctl.socket,'socket',Socket)
    monkeypatch.setattr(deploy.time,'sleep',lambda _:None)
    if occupied:
        with pytest.raises(SystemExit,match='8766'):
            deploy.wait_for_free_port()
    else:
        deploy.wait_for_free_port()
