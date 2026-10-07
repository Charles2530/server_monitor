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


def test_deploy_writes_hardware_selected_runtime_profile(monkeypatch, tmp_path):
    from scripts import deploy_launchagent as deploy
    monkeypatch.setattr(deploy, 'SOURCE', tmp_path)
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda: {
        'model': 'Mac16,1', 'machine': 'arm64', 'ncpu': 14, 'memory_gb': 48,
    })
    profile = deploy.write_runtime_profile(tmp_path)
    assert profile['name'] == 'm4-performance'
    assert (tmp_path / 'runtime-profile.json').stat().st_mode & 0o777 == 0o600


def test_redeploy_archives_exact_previous_profile_without_duplicate_backups(tmp_path, monkeypatch):
    from scripts import deploy_launchagent as deploy
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda: {
        'model': 'Mac16,10', 'machine': 'arm64', 'ncpu': 10, 'memory_gb': 24})
    path = tmp_path / 'runtime-profile.json'
    original = b'{"name":"previous-pro-parameters","target_cache_seconds":6}\n'
    path.write_bytes(original)
    deploy.write_runtime_profile(tmp_path)
    saved = list((tmp_path/'runtime-profile-history').glob('*.json'))
    assert len(saved) == 1 and saved[0].read_bytes() == original
    assert saved[0].stat().st_mode & 0o777 == 0o600
    deploy.write_runtime_profile(tmp_path)
    assert list((tmp_path/'runtime-profile-history').glob('*.json')) == saved


def test_explicit_saved_profile_restores_parameters_and_survives_redeploy(tmp_path, monkeypatch):
    import json
    from scripts import deploy_launchagent as deploy
    from server.runtime_profile import choose_profile
    hardware = {'model':'Mac16,1','machine':'arm64','ncpu':10,'memory_gb':24}
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda: hardware)
    saved = tmp_path/'saved.json'
    custom = choose_profile(hardware)
    custom['target_cache_seconds'] = 9
    saved.write_text(json.dumps(custom))
    assert deploy.write_runtime_profile(tmp_path, saved)['target_cache_seconds'] == 9
    assert deploy.write_runtime_profile(tmp_path)['target_cache_seconds'] == 9
    saved.write_text('{"collector_concurrency":999}')
    before = (tmp_path/'runtime-profile.json').read_bytes()
    with pytest.raises(ValueError): deploy.write_runtime_profile(tmp_path, saved)
    assert (tmp_path/'runtime-profile.json').read_bytes() == before
    assert deploy.write_runtime_profile(tmp_path, 'auto')['target_cache_seconds'] == 8


def test_mini_deployment_does_not_change_saved_pro_presets(tmp_path, monkeypatch):
    from scripts import deploy_launchagent as deploy
    import json
    paths = list((deploy.SOURCE/'config/runtime-profiles').glob('macbook-pro-*-baseline.json'))
    assert len(paths) == 2
    original = {path:path.read_bytes() for path in paths}
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda: {
        'model':'Mac16,10','machine':'arm64','ncpu':10,'memory_gb':24})
    deploy.write_runtime_profile(tmp_path)
    for path, data in original.items():
        assert path.read_bytes() == data
        settings = json.loads(data)
        assert settings['collector_concurrency'] == 6
        assert settings['metric_concurrency'] == 4
        assert 'state_poll_seconds' not in settings


def test_invalid_override_is_checked_before_installer_commands(tmp_path, monkeypatch):
    from scripts import deploy_launchagent as deploy
    monkeypatch.setattr(deploy, 'require_agent_mode', lambda *a:None)
    monkeypatch.setattr(deploy, 'RUNTIME', tmp_path)
    monkeypatch.setattr(deploy.sys, 'platform', 'darwin')
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda: {
        'model':'Mac16,10','ncpu':10,'memory_gb':24})
    bad = tmp_path/'invalid.json'; bad.write_text('{}')
    monkeypatch.setattr(deploy.subprocess, 'run', lambda *a, **kw:pytest.fail('must validate before installation'))
    with pytest.raises(ValueError): deploy.main(['--profile-file', str(bad)])


@pytest.mark.parametrize('model,expected', [('Mac16,10','Interactive'), ('Mac16,1','Background'), ('unknown','Background')])
def test_deployed_web_job_uses_machine_scheduling_and_migration_retains_it(model, expected, tmp_path):
    from scripts import deploy_launchagent as deploy
    from scripts import boot_services as boot
    from server.runtime_profile import choose_profile
    from types import SimpleNamespace
    import os
    settings = choose_profile({'model':model,'ncpu':10,'memory_gb':24})
    job = deploy.launchagent_payload(tmp_path/'python', settings)
    assert job['ProcessType'] == expected
    assert job['ProgramArguments'][job['ProgramArguments'].index('--host')+1] == '127.0.0.1'
    assert job['KeepAlive'] and job['RunAtLoad']
    user = SimpleNamespace(pw_name='testuser',pw_uid=501,pw_gid=os.getgid(),pw_dir=str(tmp_path))
    assert boot.daemon_payload(job,user)['ProcessType'] == expected


def test_legacy_manual_profile_keeps_background_scheduling_on_mini(tmp_path, monkeypatch):
    import json
    from scripts import deploy_launchagent as deploy
    hardware = {'model':'Mac16,10','machine':'arm64','ncpu':10,'memory_gb':24}
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda:hardware)
    legacy = {'name':'m4-balanced','collector_concurrency':6,'metric_concurrency':4,
              'queue_concurrency':6,'terminal_max_sessions':8,'target_cache_seconds':8,
              'selection':'manual','hardware':hardware}
    (tmp_path/'runtime-profile.json').write_text(json.dumps(legacy))
    selected = deploy.select_runtime_profile(tmp_path)
    assert selected == legacy
    assert deploy.launchagent_payload(tmp_path/'python',selected)['ProcessType'] == 'Background'


@pytest.mark.parametrize('bad_name', [[], {}])
def test_malformed_saved_profile_name_falls_back_before_deployment(tmp_path, monkeypatch, bad_name):
    import json
    from scripts import deploy_launchagent as deploy
    hardware = {'model':'Mac16,12','machine':'arm64','ncpu':10,'memory_gb':16}
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda:hardware)
    (tmp_path/'runtime-profile.json').write_text(json.dumps({'name':bad_name}))
    selected = deploy.select_runtime_profile(tmp_path)
    assert selected['selection'] == 'auto'
    assert selected['hardware'] == hardware


def test_saved_air_profile_survives_redeployment_without_affecting_mini(tmp_path, monkeypatch):
    import json
    from scripts import deploy_launchagent as deploy
    hardware = {'model':'Mac16,12','machine':'arm64','ncpu':10,'memory_gb':16}
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda:hardware)
    saved = deploy.SOURCE/'config/runtime-profiles/macbook-air-current.json'
    selected = deploy.write_runtime_profile(tmp_path, saved)
    assert selected == json.loads(saved.read_text())
    assert selected['queue_concurrency'] == 3
    assert deploy.launchagent_payload(tmp_path/'python', selected)['ProcessType'] == 'Standard'
    assert deploy.select_runtime_profile(tmp_path) == selected
    monkeypatch.setattr('server.runtime_profile.detect_hardware', lambda: {
        'model':'Mac16,10','machine':'arm64','ncpu':10,'memory_gb':24})
    mini = deploy.select_runtime_profile(tmp_path)
    assert mini['selection'] == 'auto'
    assert mini['queue_concurrency'] == 6
    assert mini['web_process_type'] == 'Interactive'
