"""Deploy this local app outside macOS protected Desktop/Documents folders."""
import os
import json
import argparse
import hashlib
from pathlib import Path
import plistlib
import shutil
import socket
import sqlite3
import subprocess
import time
from urllib.request import urlopen
import sys
try:
    from .boot_services import require_agent_mode
except ImportError:
    from boot_services import require_agent_mode

SOURCE = Path(__file__).resolve().parent.parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
RUNTIME = Path.home()/'Library/Application Support/CharlesCloudMonitor'
LABEL = 'charles.server-monitor'
DOMAIN = 'gui/'+str(os.getuid())
PLIST = Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')


def wait_for_free_port():
    for _ in range(50):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',8766))!=0:
                return
        time.sleep(.1)
    raise SystemExit('端口 8766 仍被占用；请先用 ./stop.sh 停止普通后台服务，前台服务请按 Ctrl+C，然后重新部署。')


def select_runtime_profile(runtime, profile_file=None):
    from server.runtime_profile import choose_profile, detect_hardware, _valid_profile
    profile = choose_profile(detect_hardware())
    selected = None
    if profile_file is not None and str(profile_file) != 'auto':
        selected = json.loads(Path(profile_file).read_text())
        if not _valid_profile(selected):
            raise ValueError('Invalid runtime profile: ' + str(profile_file))
    elif profile_file is None:
        try:
            previous = json.loads((runtime/'runtime-profile.json').read_text())
            if (_valid_profile(previous) and previous.get('selection') == 'manual'
                    and previous.get('hardware') == profile['hardware']):
                selected = previous
        except (OSError, ValueError):
            pass
    if selected is not None:
        return {**selected, 'hardware':profile['hardware'], 'selection':'manual'}
    return {**profile, 'selection':'auto'}


def write_runtime_profile(runtime, profile_file=None, *, selected=None):
    profile = selected if selected is not None else select_runtime_profile(runtime, profile_file)
    path = runtime / 'runtime-profile.json'
    content = (json.dumps(profile, indent=2, sort_keys=True) + '\n').encode()
    previous = path.read_bytes() if path.exists() else None
    if previous is not None and previous != content:
        history = runtime/'runtime-profile-history'
        history.mkdir(mode=0o700, exist_ok=True)
        backup = history/(hashlib.sha256(previous).hexdigest()+'.json')
        try:
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream: stream.write(previous)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(content)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return profile


def launchagent_payload(python, profile):
    data = RUNTIME/'data'
    return {
        'Label':LABEL,
        'ProgramArguments':[str(python),'-m','uvicorn','server.main:app','--app-dir',str(RUNTIME),'--host','127.0.0.1','--port','8766','--no-access-log'],
        'WorkingDirectory':str(RUNTIME),'RunAtLoad':True,'KeepAlive':True,
        'ThrottleInterval':10,'ExitTimeOut':20,'ProcessType':profile.get('web_process_type','Background'),
        'EnvironmentVariables':{'PYTHONUNBUFFERED':'1'},
        'StandardOutPath':str(data/'launchagent.out.log'),
        'StandardErrorPath':str(data/'launchagent.err.log'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-file', help='Restore a saved JSON profile; use auto to resume hardware detection')
    args = parser.parse_args(argv)
    if sys.platform!='darwin':
        raise SystemExit('LaunchAgent 仅支持 macOS；Linux 请使用 ./start.sh 或 ./run.sh。')
    require_agent_mode(LABEL)
    # Validate overrides before stopping any service or copying code.
    selected_profile = select_runtime_profile(RUNTIME, args.profile_file)
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(RUNTIME,0o700)
    data=RUNTIME/'data'
    data.mkdir(exist_ok=True,mode=0o700)
    python=RUNTIME/'.venv/bin/python'
    if not python.exists():
        subprocess.run([str(Path(sys._base_executable).resolve()),'-m','venv',str(RUNTIME/'.venv')],check=True)
    subprocess.run([str(python),'-m','pip','install','-r',str(SOURCE/'requirements.lock')],check=True)
    subprocess.run([str(python),'-m','scripts.setup_browser'],cwd=SOURCE,check=True)
    target=DOMAIN+'/'+LABEL
    loaded=subprocess.run(['/bin/launchctl','print',target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    if loaded:
        subprocess.run(['/bin/launchctl','bootout',target],check=True)
        # Wait for the collector's advisory lock to become available before copying code.
        import fcntl
        lock_path=data/'collector.lock'
        with lock_path.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
    wait_for_free_port()
    for directory in ('server','static'):
        shutil.copytree(SOURCE/directory,RUNTIME/directory,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(SOURCE/'requirements.lock',RUNTIME/'requirements.lock')
    profile = write_runtime_profile(RUNTIME, selected=selected_profile)
    if (SOURCE/'.env').exists():
        shutil.copy2(SOURCE/'.env',RUNTIME/'.env')
        os.chmod(RUNTIME/'.env',0o600)
    # Import initial state per site. Redeployments preserve every live session/database.
    for suffix in ('', 'sensecore'):
        destination_dir=data/suffix
        source_dir=SOURCE/'data'/suffix
        destination_dir.mkdir(exist_ok=True,mode=0o700)
        os.chmod(destination_dir,0o700)
        if not (destination_dir/'session.json').exists() and (source_dir/'session.json').exists():
            shutil.copy2(source_dir/'session.json',destination_dir/'session.json')
            os.chmod(destination_dir/'session.json',0o600)
        if not (destination_dir/'monitor.sqlite3').exists() and (source_dir/'monitor.sqlite3').exists():
            with sqlite3.connect(str(source_dir/'monitor.sqlite3')) as source:
                with sqlite3.connect(str(destination_dir/'monitor.sqlite3')) as destination:
                    source.backup(destination)
            os.chmod(destination_dir/'monitor.sqlite3',0o600)
    payload=launchagent_payload(python, profile)
    for path in (SOURCE/'config'/PLIST.name,PLIST):
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        temporary.write_bytes(plistlib.dumps(payload,sort_keys=False))
        os.chmod(temporary,0o644)
        temporary.replace(path)
    subprocess.run(['/usr/bin/plutil','-lint',str(PLIST)],check=True)
    subprocess.run(['/bin/launchctl','bootstrap',DOMAIN,str(PLIST)],check=True)
    for _ in range(50):
        try:
            with urlopen('http://127.0.0.1:8766/api/state',timeout=1) as response:
                if response.status==200:
                    print('自动启动部署成功：http://127.0.0.1:8766')
                    print('运行目录：'+str(RUNTIME))
                    print('运行 profile：'+profile['name']+' ('+str(profile['hardware'])+')')
                    return
        except OSError:
            pass
        time.sleep(.2)
    raise SystemExit('服务尚未就绪，请检查 '+str(data/'launchagent.err.log'))

if __name__=='__main__':
    import fcntl
    (SOURCE/'data').mkdir(exist_ok=True,mode=0o700)
    with (SOURCE/'data/controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        main()
