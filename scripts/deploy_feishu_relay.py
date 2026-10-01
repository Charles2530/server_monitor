#!/usr/bin/env python3
"""Install/update the independent Feishu relay; preserve private config and state."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
RUNTIME = Path.home() / 'Library/Application Support/CharlesMonitorFeishu'
LABEL = 'charles.monitor-feishu'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This installer requires macOS.')
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(RUNTIME, 0o700)
    sys.path.insert(0, str(SOURCE))
    from feishu_relay import read_config
    read_config(RUNTIME / 'config.json')
    python = Path('/usr/bin/python3')
    subprocess.run([str(python), '-c', 'import sqlite3,ssl,fcntl'], check=True)
    target = 'gui/%d/%s' % (os.getuid(), LABEL)
    loaded = subprocess.run(['/bin/launchctl', 'print', target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if loaded:
        subprocess.run(['/bin/launchctl', 'bootout', target], check=True)
    # Wait for an earlier instance to exit before replacing its source.
    import fcntl
    with (RUNTIME / 'relay.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        temporary = RUNTIME / 'feishu_relay.py.tmp'
        shutil.copyfile(SOURCE / 'feishu_relay.py', temporary)
        os.chmod(temporary, 0o600)
        temporary.replace(RUNTIME / 'feishu_relay.py')
    payload = {
        'Label': LABEL,
        'ProgramArguments': [str(python), str(RUNTIME / 'feishu_relay.py'), '--config', str(RUNTIME / 'config.json')],
        'WorkingDirectory': str(RUNTIME), 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 30, 'ExitTimeOut': 20, 'ProcessType': 'Background',
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'},
        'StandardOutPath': str(RUNTIME / 'relay.out.log'),
        'StandardErrorPath': str(RUNTIME / 'relay.err.log'),
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    temporary = PLIST.with_suffix('.tmp')
    temporary.write_bytes(plistlib.dumps(payload, sort_keys=False))
    os.chmod(temporary, 0o600)
    temporary.replace(PLIST)
    subprocess.run(['/usr/bin/plutil', '-lint', str(PLIST)], check=True)
    subprocess.run(['/bin/launchctl', 'bootstrap', 'gui/%d' % os.getuid(), str(PLIST)], check=True)
    print('Feishu relay installed: ' + str(RUNTIME))


if __name__ == '__main__':
    main()
