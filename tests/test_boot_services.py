import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import boot_services as boot


def account(home):
    return SimpleNamespace(pw_name='testuser', pw_uid=501, pw_gid=20, pw_dir=str(home))


def agent(home, label='charles.server-monitor'):
    return {'Label': label, 'ProgramArguments': ['/usr/bin/python3', '-m', 'uvicorn',
            'server.main:app', '--host', '127.0.0.1', '--port', '8766'],
            'WorkingDirectory': str(home / 'runtime'), 'RunAtLoad': True, 'KeepAlive': True,
            'EnvironmentVariables': {'MONITOR_MOBILE_DATA': str(home / 'private-data')}}


def test_business_daemon_preserves_loopback_data_and_uses_regular_user(tmp_path):
    result = boot.daemon_payload(agent(tmp_path), account(tmp_path))
    assert result['UserName'] == 'testuser'
    assert result['GroupName'] == 'staff'
    assert result['RunAtLoad'] is True and result['KeepAlive'] is True
    assert result['ProgramArguments'] == agent(tmp_path)['ProgramArguments']
    assert result['EnvironmentVariables']['HOME'] == str(tmp_path)
    assert result['EnvironmentVariables']['MONITOR_MOBILE_DATA'] == str(tmp_path / 'private-data')


def test_rejects_root_and_unknown_services_before_migration(tmp_path):
    root = SimpleNamespace(pw_name='root', pw_uid=0, pw_gid=0, pw_dir='/var/root')
    with pytest.raises(ValueError):
        boot.daemon_payload(agent(tmp_path), root)
    with pytest.raises(ValueError):
        boot.daemon_payload(agent(tmp_path, 'some.other.service'), account(tmp_path))


def test_network_service_uses_private_userspace_state_and_no_auth_key(tmp_path):
    payload = boot.network_payload(account(tmp_path))
    args = payload['ProgramArguments']
    assert '--tun=userspace-networking' in args
    assert any(a.startswith('--state=' + str(tmp_path)) for a in args)
    assert not any('authkey' in a or 'auth-key' in a for a in args)
    assert payload['UserName'] == 'testuser'


def test_collect_plans_requires_monitor_and_gateway_and_skips_missing_feishu(tmp_path):
    user = account(tmp_path)
    agents = tmp_path / 'Library/LaunchAgents'
    agents.mkdir(parents=True)
    for label in boot.LABELS[:2]:
        (agents / (label + '.plist')).write_bytes(plistlib.dumps(agent(tmp_path, label)))
    plans = boot.collect_plans(user)
    assert set(plans) == set(boot.LABELS[:2]) | {boot.NETWORK_LABEL}
    (agents / (boot.LABELS[1] + '.plist')).unlink()
    with pytest.raises(ValueError, match='missing'):
        boot.collect_plans(user)


def test_status_checks_system_domain_not_login_domain(monkeypatch):
    targets = []
    def run(args, **kwargs):
        targets.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(boot.subprocess, 'run', run)
    assert boot.loaded('system/charles.server-monitor')
    assert targets == [['/bin/launchctl', 'print', 'system/charles.server-monitor']]


def test_failed_bootstrap_restores_original_agent_and_removes_partial_daemon(tmp_path, monkeypatch):
    user = account(tmp_path)
    agents = tmp_path / 'Library/LaunchAgents'
    agents.mkdir(parents=True)
    label = boot.LABELS[0]
    original = plistlib.dumps(agent(tmp_path, label))
    source = agents / (label + '.plist')
    source.write_bytes(original)
    system = tmp_path / 'system'
    system.mkdir()
    running = {'gui/501', 'gui/501/' + label}
    calls = []
    monkeypatch.setattr(boot, 'loaded', lambda target: target in running)
    def launchctl(*args):
        calls.append(args)
        if args[0] == 'bootout':
            running.discard(args[1])
        elif args[:2] == ('bootstrap', 'system'):
            raise RuntimeError('simulated bootstrap failure')
        elif args[:2] == ('bootstrap', 'gui/501'):
            running.add('gui/501/' + label)
    monkeypatch.setattr(boot, 'launchctl', launchctl)
    with pytest.raises(RuntimeError, match='simulated'):
        boot.migrate({label: boot.daemon_payload(agent(tmp_path), user)}, user, system)
    assert source.read_bytes() == original
    assert 'gui/501/' + label in running
    assert not (system / (label + '.plist')).exists()
    assert not (boot.support(user, 'CharlesMonitorBoot') / 'migration.json').exists()


def test_migration_conflict_does_not_stop_or_replace_any_service(tmp_path, monkeypatch):
    user = account(tmp_path)
    system = tmp_path / 'system'
    system.mkdir()
    path = system / (boot.LABELS[0] + '.plist')
    path.write_bytes(b'preexisting')
    calls = []
    monkeypatch.setattr(boot, 'loaded', lambda target: False)
    monkeypatch.setattr(boot, 'launchctl', lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='already exists'):
        boot.migrate({boot.LABELS[0]: agent(tmp_path)}, user, system)
    assert path.read_bytes() == b'preexisting'
    assert calls == []


def test_successful_migration_removes_login_entry_and_rollback_restores_it(tmp_path, monkeypatch):
    user = account(tmp_path)
    agents = tmp_path / 'Library/LaunchAgents'
    agents.mkdir(parents=True)
    label = boot.LABELS[0]
    original = plistlib.dumps(agent(tmp_path))
    source = agents / (label + '.plist')
    source.write_bytes(original)
    system = tmp_path / 'system'
    system.mkdir()
    running = {'gui/501', 'gui/501/' + label}
    monkeypatch.setattr(boot, 'loaded', lambda target: target in running)
    def launchctl(*args):
        if args[0] == 'bootout':
            running.discard(args[1])
        elif args[0] == 'bootstrap':
            running.add(args[1] + '/' + label)
    monkeypatch.setattr(boot, 'launchctl', launchctl)
    monkeypatch.setattr(boot, 'wait_ready', lambda plans: None)
    boot.migrate({label: boot.daemon_payload(agent(tmp_path), user)}, user, system)
    assert not source.exists()
    assert running == {'gui/501', 'system/' + label}
    assert plistlib.loads((system / (label + '.plist')).read_bytes())['UserName'] == 'testuser'
    boot.restore(user, system)
    assert source.read_bytes() == original
    assert running == {'gui/501', 'gui/501/' + label}


def test_user_installer_guard_rejects_existing_system_service(tmp_path):
    label = boot.LABELS[0]
    (tmp_path / (label + '.plist')).write_bytes(b'installed')
    with pytest.raises(SystemExit, match='system'):
        boot.require_agent_mode(label, system=tmp_path)


def test_control_reports_system_mode_without_starting_duplicate_user_process(tmp_path, monkeypatch, capsys):
    from scripts import monitorctl
    daemon = tmp_path / 'charles.server-monitor.plist'
    daemon.write_bytes(b'installed')
    monkeypatch.setattr(monitorctl, 'SYSTEM_PLIST', daemon)
    monkeypatch.setattr(monitorctl, 'system_loaded', lambda: True)
    monkeypatch.setattr(monitorctl, 'wait_for_api', lambda: None)
    assert monitorctl.system_action('start')
    assert 'LaunchDaemon' in capsys.readouterr().out
    monkeypatch.setattr(monitorctl.os, 'geteuid', lambda: 501)
    with pytest.raises(SystemExit, match='sudo'):
        monitorctl.system_action('stop')


def test_rollback_without_gui_restores_files_and_defers_start(tmp_path, monkeypatch, capsys):
    import json
    user = account(tmp_path)
    root = boot.support(user, 'CharlesMonitorBoot')
    backup = root / 'agents'
    backup.mkdir(parents=True)
    label = boot.LABELS[0]
    (backup / (label + '.plist')).write_bytes(plistlib.dumps(agent(tmp_path)))
    (root / 'migration.json').write_text(json.dumps({'user': user.pw_name, 'uid': 501,
        'labels': [label], 'agents': [label], 'loaded_agents': [label]}))
    system = tmp_path / 'system'
    system.mkdir()
    monkeypatch.setattr(boot, 'loaded', lambda target: False)
    monkeypatch.setattr(boot, 'launchctl', lambda *args: pytest.fail('No GUI bootstrap expected'))
    boot.restore(user, system)
    assert (tmp_path / 'Library/LaunchAgents' / (label + '.plist')).exists()
    assert not (root / 'migration.json').exists()
    assert 'next login' in capsys.readouterr().out


def test_ready_rejects_registered_but_crashed_job(monkeypatch):
    monkeypatch.setattr(boot.subprocess, 'run', lambda *a, **k:
        SimpleNamespace(returncode=0, stdout='state = spawn scheduled\nlast exit code = 1\n'))
    with pytest.raises(RuntimeError, match='ready'):
        boot.wait_ready({boot.LABELS[0]: {}}, timeout=0)


def test_readiness_failure_rolls_back_migration(tmp_path, monkeypatch):
    user = account(tmp_path)
    system = tmp_path / 'system'
    system.mkdir()
    monkeypatch.setattr(boot, 'loaded', lambda target: False)
    monkeypatch.setattr(boot, 'launchctl', lambda *a: None)
    def fail(plans):
        raise RuntimeError('not ready')
    monkeypatch.setattr(boot, 'wait_ready', fail)
    with pytest.raises(RuntimeError, match='not ready'):
        boot.migrate({boot.LABELS[0]: agent(tmp_path)}, user, system)
    assert not list(system.iterdir())
    assert not (boot.support(user, 'CharlesMonitorBoot') / 'migration.json').exists()


def test_ready_recovers_when_http_is_ready_after_process(monkeypatch):
    monkeypatch.setattr(boot.subprocess, 'run', lambda *a, **k:
        SimpleNamespace(returncode=0, stdout='state = running\npid = 100\n'))
    now = [0]
    monkeypatch.setattr(boot.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(boot.time, 'sleep', lambda delay: now.__setitem__(0, now[0] + delay))
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Opener:
        def open(self, *a, **k):
            if now[0] == 0: raise OSError('starting')
            return Response()
    monkeypatch.setattr(boot.urllib.request, 'build_opener', lambda *a: Opener())
    boot.wait_ready({boot.LABELS[0]: {}}, timeout=10)
    assert now[0] == 6
