"""Migrate installed monitor services to boot-time launchd jobs, with rollback."""
import argparse
import copy
import grp
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import time
import urllib.request
import shutil
import subprocess
import sys

LABELS = ('charles.server-monitor', 'charles.server-monitor-mobile',
          'charles.monitor-feishu', 'charles.monitor-bitable')
NETWORK_LABEL = 'charles.monitor-network'
SYSTEM = Path('/Library/LaunchDaemons')


def require_agent_mode(label, system=SYSTEM):
    if (system / (label + '.plist')).exists():
        raise SystemExit('This service is installed in the system domain. Use boot_services.py rollback '
                         'before updating the user deployment, then install boot services again.')


def loaded(target):
    return subprocess.run(['/bin/launchctl', 'print', target],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def wait_ready(plans, timeout=60):
    """Require stable running PIDs and local HTTP readiness, not just registration."""
    deadline = time.monotonic() + timeout
    previous = None
    stable_since = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    endpoints = {LABELS[0]: 'http://127.0.0.1:8766/api/state',
                 LABELS[1]: 'http://127.0.0.1:8767/health'}
    while True:
        pids = {}
        for label in plans:
            result = subprocess.run(['/bin/launchctl', 'print', 'system/' + label],
                                    capture_output=True, text=True)
            pid = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.M)
            if result.returncode == 0 and pid and re.search(r'^\s*state = running\s*$', result.stdout, re.M):
                pids[label] = pid.group(1)
        healthy = len(pids) == len(plans)
        if healthy:
            for label, url in endpoints.items():
                if label in plans:
                    try:
                        with opener.open(url, timeout=2) as response:
                            healthy = healthy and response.status == 200
                    except (OSError, ValueError):
                        healthy = False
        now = time.monotonic()
        if healthy and pids == previous and stable_since is not None:
            if now - stable_since >= 5:
                return
        else:
            stable_since = now if healthy else None
        previous = pids
        if now >= deadline:
            raise RuntimeError('System services did not become ready; restoring user agents')
        time.sleep(1)


def launchctl(*args):
    subprocess.run(['/bin/launchctl', *args], check=True)


def support(user, name):
    return Path(user.pw_dir) / 'Library/Application Support' / name


def daemon_payload(agent, user):
    if user.pw_uid == 0 or agent.get('Label') not in (*LABELS, NETWORK_LABEL):
        raise ValueError('Only the monitor services under a non-root user are supported')
    result = copy.deepcopy(agent)
    result.update(UserName=user.pw_name, GroupName=grp.getgrgid(user.pw_gid).gr_name,
                  RunAtLoad=True, KeepAlive=True)
    result.pop('LimitLoadToSessionType', None)
    env = result.setdefault('EnvironmentVariables', {})
    env.update(HOME=user.pw_dir, USER=user.pw_name, LOGNAME=user.pw_name)
    return result


def network_payload(user):
    root = support(user, 'CharlesMonitorNetwork')
    return daemon_payload({
        'Label': NETWORK_LABEL,
        'ProgramArguments': [str(root / 'bin/tailscaled'), '--tun=userspace-networking',
                             '--state=' + str(root / 'tailscaled.state'), '--statedir=' + str(root),
                             '--socket=' + str(root / 'tailscaled.sock'), '--port=0'],
        'WorkingDirectory': str(root), 'ThrottleInterval': 10, 'ExitTimeOut': 20,
        'ProcessType': 'Background', 'Umask': 0o077,
        'StandardOutPath': str(root / 'network.out.log'),
        'StandardErrorPath': str(root / 'network.err.log'),
    }, user)


def collect_plans(user):
    agents = Path(user.pw_dir) / 'Library/LaunchAgents'
    plans = {}
    for label in LABELS:
        path = agents / (label + '.plist')
        if not path.exists():
            if label in LABELS[:2]:
                raise ValueError('Required installed LaunchAgent missing: ' + label)
            continue
        agent = plistlib.loads(path.read_bytes())
        if agent.get('Label') != label:
            raise ValueError('LaunchAgent label does not match filename: ' + label)
        plans[label] = daemon_payload(agent, user)
    plans[NETWORK_LABEL] = network_payload(user)
    return plans


def check_paths(plans):
    for label, payload in plans.items():
        args = payload.get('ProgramArguments', [])
        if not args or not Path(args[0]).is_file() or not os.access(args[0], os.X_OK):
            raise ValueError('Executable missing or not executable: ' + label)
        if not Path(payload['WorkingDirectory']).is_dir():
            raise ValueError('Working directory missing: ' + label)
        if any('/Volumes/' in str(value) for value in args):
            raise ValueError('Boot services must use the internal disk: ' + label)


def write_plist(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(plistlib.dumps(payload, sort_keys=False))
    temporary.chmod(0o644)
    if os.geteuid() == 0:
        os.chown(temporary, 0, 0)
    temporary.replace(path)


def migrate(plans, user, system=SYSTEM):
    """All conflicts are checked before stopping any process. Failure restores agents."""
    root = support(user, 'CharlesMonitorBoot')
    manifest = root / 'migration.json'
    if manifest.exists():
        raise ValueError('A migration already exists; use status or rollback first')
    for label in plans:
        if (system / (label + '.plist')).exists() or loaded('system/' + label):
            raise ValueError('System service already exists: ' + label)
    agents = Path(user.pw_dir) / 'Library/LaunchAgents'
    record = {'user': user.pw_name, 'uid': user.pw_uid, 'labels': list(plans),
              'agents': [], 'loaded_agents': []}
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = root / 'agents'
    backup.mkdir(exist_ok=True, mode=0o700)
    # Copy every original before changing any launchd job or plist.
    for label in plans:
        path = agents / (label + '.plist')
        if path.exists():
            shutil.copy2(path, backup / path.name)
            record['agents'].append(label)
        if loaded('gui/%d/%s' % (user.pw_uid, label)):
            record['loaded_agents'].append(label)
    manifest.write_text(json.dumps(record, indent=2) + '\n')
    manifest.chmod(0o600)
    try:
        for label in record['loaded_agents']:
            launchctl('bootout', 'gui/%d/%s' % (user.pw_uid, label))
        for label in record['agents']:
            (agents / (label + '.plist')).unlink()
        for label, payload in plans.items():
            path = system / (label + '.plist')
            write_plist(path, payload)
            launchctl('enable', 'system/' + label)
            launchctl('bootstrap', 'system', str(path))
        wait_ready(plans)
    except BaseException:
        restore(user, system)
        raise
    print('System services installed; original LaunchAgents are backed up in ' + str(backup))


def restore(user, system=SYSTEM):
    root = support(user, 'CharlesMonitorBoot')
    manifest = root / 'migration.json'
    record = json.loads(manifest.read_text())
    if record['user'] != user.pw_name or record['uid'] != user.pw_uid:
        raise ValueError('Migration belongs to another user')
    if any(label not in (*LABELS, NETWORK_LABEL) for label in record['labels']):
        raise ValueError('Unknown service in migration record')
    for label in reversed(record['labels']):
        if loaded('system/' + label):
            launchctl('bootout', 'system/' + label)
        (system / (label + '.plist')).unlink(missing_ok=True)
    agents = Path(user.pw_dir) / 'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    for label in record['agents']:
        dest = agents / (label + '.plist')
        shutil.copy2(root / 'agents' / dest.name, dest)
        if os.geteuid() == 0:
            os.chown(dest, user.pw_uid, user.pw_gid)
    gui = 'gui/%d' % user.pw_uid
    if loaded(gui):
        for label in record['loaded_agents']:
            if not loaded(gui + '/' + label):
                launchctl('bootstrap', gui, str(agents / (label + '.plist')))
    elif record['loaded_agents']:
        print('No GUI session; restored agents will start at next login.')
    manifest.rename(root / 'migration.restored.json')
    print('Restored original user LaunchAgents; network credentials and monitor data retained.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'install', 'rollback', 'status'])
    parser.add_argument('--user', default=os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name)
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('This installer is for macOS')
    user = pwd.getpwnam(args.user)
    if user.pw_uid == 0:
        parser.error('Specify the regular monitoring user with --user')
    if args.action == 'status':
        for label in (*LABELS, NETWORK_LABEL):
            print(label + ': ' + ('system loaded' if loaded('system/' + label) else 'system not loaded'))
        return
    if args.action in ('install', 'rollback') and os.geteuid() != 0:
        parser.error('Administrator installation required: sudo /usr/bin/python3 scripts/boot_services.py ' + args.action + ' --user ' + user.pw_name)
    if args.action == 'rollback':
        restore(user)
        return
    plans = collect_plans(user)
    check_paths(plans)
    if args.action == 'plan':
        for label, payload in plans.items():
            print(label + ': system boot, user=' + payload['UserName'] + ', executable=' + payload['ProgramArguments'][0])
        return
    migrate(plans, user)


if __name__ == '__main__':
    main()
