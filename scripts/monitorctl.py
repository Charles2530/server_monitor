"""Start/stop only this project's local detached server."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
PID_FILE = DATA / 'server.pid'
PORT = 8766
AGENT_LABEL = 'charles.server-monitor'
AGENT_PLIST = Path.home() / 'Library/LaunchAgents' / (AGENT_LABEL+'.plist')
AGENT_DOMAIN = 'gui/'+str(os.getuid())
AGENT_TARGET = AGENT_DOMAIN+'/'+AGENT_LABEL
COMMAND = [str(ROOT/'.venv/bin/python'), '-m', 'uvicorn', 'server.main:app', '--app-dir', str(ROOT), '--host', '127.0.0.1', '--port', str(PORT), '--no-access-log']

def agent_loaded():
    if sys.platform!='darwin':return False
    return subprocess.run(['/bin/launchctl','print',AGENT_TARGET],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0


def launchctl(*args):
    result=subprocess.run(['/bin/launchctl',*args],capture_output=True,text=True)
    if result.returncode:
        raise SystemExit('LaunchAgent 操作失败：'+result.stderr.strip())


def wait_for_api():
    for _ in range(50):
        try:
            with urlopen('http://127.0.0.1:8766/api/state',timeout=1) as response:
                if response.status==200:
                    return
        except OSError:
            pass
        time.sleep(.2)
    raise SystemExit('LaunchAgent 已加载，但网站未就绪；请检查 ~/Library/Application Support/CharlesCloudMonitor/data/launchagent.err.log。')


def agent_action(action):
    if sys.platform!='darwin':return False
    loaded=agent_loaded()
    if not loaded and not AGENT_PLIST.exists():
        return False
    if action=='start':
        if not loaded:
            launchctl('bootstrap',AGENT_DOMAIN,str(AGENT_PLIST))
        wait_for_api()
        print('LaunchAgent 已运行：http://127.0.0.1:8766（登录自动启动）')
    elif action=='stop':
        if loaded:
            launchctl('bootout',AGENT_TARGET)
        print('LaunchAgent 已停止；下次登录仍会自动启动。')
    elif action=='status':
        if not loaded:
            print('LaunchAgent 当前未加载；下次登录自动启动。')
        else:
            try:
                with urlopen('http://127.0.0.1:8766/api/state',timeout=3) as response:
                    state=json.load(response)
                print('LaunchAgent 已加载；状态:',state['status'],'最近快照:',state['updated_at'],'任务数:',len(state['tasks']))
            except OSError:
                print('LaunchAgent 已加载，网站暂未就绪；请检查 ~/Library/Application Support/CharlesCloudMonitor/data/launchagent.err.log。')
    else:
        raise SystemExit('用法：monitorctl.py start|stop|status')
    return True


def running_pid():
    try:
        pid=int(PID_FILE.read_text())
        if pid<=0:return None
        if sys.platform=='linux':
            argv=Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')
            app_index=argv.index(b'--app-dir')
            if argv[1:4]==[b'-m',b'uvicorn',b'server.main:app'] and argv[app_index+1]==os.fsencode(ROOT):
                return pid
            return None
        command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True).strip()
        if 'uvicorn server.main:app' in command and '--app-dir '+str(ROOT) in command:
            return pid
    except (OSError,ValueError,IndexError,subprocess.CalledProcessError):
        pass
    return None


def main():
    action=sys.argv[1] if len(sys.argv)>1 else 'status'
    DATA.mkdir(mode=0o700,exist_ok=True)
    if agent_action(action):
        return
    pid=running_pid()
    if action=='start':
        if pid:
            print('已运行：http://127.0.0.1:8766');return
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',PORT))==0:
                raise SystemExit('端口 8766 已被占用；未启动或终止其他进程。')
        with open(DATA/'server.log','ab') as log:
            child=subprocess.Popen(COMMAND,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        PID_FILE.write_text(str(child.pid));os.chmod(PID_FILE,0o600)
        for _ in range(30):
            if child.poll() is not None:
                raise SystemExit('启动失败，请查看 data/server.log。')
            try:
                with urlopen('http://127.0.0.1:8766/api/state',timeout=1) as response:
                    if response.status==200:
                        print('后台采集已启动：http://127.0.0.1:8766');return
            except OSError:
                time.sleep(.2)
        raise SystemExit('服务仍在启动，请检查 data/server.log。')
    elif action=='stop':
        if not pid:
            print('本项目后台服务未运行。');return
        os.kill(pid,signal.SIGTERM)
        for _ in range(50):
            if not running_pid():
                PID_FILE.unlink(missing_ok=True)
                print('后台采集已停止；会话和历史保留。');return
            time.sleep(.1)
        raise SystemExit('已发送停止信号，进程仍在退出中。')
    elif action=='status':
        if not pid:
            print('本项目后台服务未运行。');return
        with urlopen('http://127.0.0.1:8766/api/state',timeout=3) as response:
            state=json.load(response)
        print('PID:',pid,'状态:',state['status'],'最近快照:',state['updated_at'],'任务数:',len(state['tasks']))
    else:
        raise SystemExit('用法：monitorctl.py start|stop|status')

if __name__=='__main__':
    DATA.mkdir(mode=0o700,exist_ok=True)
    with (DATA/'controller.lock').open('w') as controller_lock:
        fcntl.flock(controller_lock,fcntl.LOCK_EX)
        main()
