"""Optional Mac gateway service; never restarts or reconfigures the monitor itself."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
try:
    from .boot_services import require_agent_mode
except ImportError:
    from boot_services import require_agent_mode

SOURCE=Path(__file__).resolve().parents[1]
RUNTIME=Path.home()/'Library/Application Support/CharlesCloudMonitorMobile'
MONITOR=Path.home()/'Library/Application Support/CharlesCloudMonitor'
LABEL='charles.server-monitor-mobile'


def payload(runtime,python):
    data=runtime/'data'
    return {'Label':LABEL,'ProgramArguments':[str(python),'-m','uvicorn','server.mobile_gateway:create_gateway','--factory',
        '--app-dir',str(runtime),'--host','127.0.0.1','--port','8767','--no-access-log'],
        'WorkingDirectory':str(runtime),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
        'ExitTimeOut':20,'ProcessType':'Background','EnvironmentVariables':{'MONITOR_MOBILE_DATA':str(data),'PYTHONUNBUFFERED':'1'},
        'StandardOutPath':str(data/'gateway.out.log'),'StandardErrorPath':str(data/'gateway.err.log')}


def main():
    parser=argparse.ArgumentParser(description='部署独立手机网关；现有监控保持不变')
    parser.add_argument('action',choices=['install','status','stop','uninstall','admin'])
    parser.add_argument('args',nargs=argparse.REMAINDER)
    options=parser.parse_args()
    if sys.platform!='darwin':parser.error('此部署脚本面向 macOS。')
    if options.action=='admin':
        env=dict(os.environ,MONITOR_MOBILE_DATA=str(RUNTIME/'data'))
        raise SystemExit(subprocess.call([sys.executable,'-m','server.mobile_admin']+options.args,cwd=SOURCE,env=env))
    system_plist=Path('/Library/LaunchDaemons')/(LABEL+'.plist')
    system_mode=system_plist.exists()
    domain='gui/'+str(os.getuid());target=domain+'/'+LABEL
    if system_mode:domain='system';target=domain+'/'+LABEL
    plist=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
    loaded=lambda:subprocess.run(['launchctl','print',target],capture_output=True).returncode==0
    if options.action=='status':
        print(('LaunchDaemon: ' if system_mode else 'LaunchAgent: ')+('loaded' if loaded() else 'not loaded'))
        try:
            with urlopen('http://127.0.0.1:8767/health',timeout=3) as response:print(response.read().decode())
        except OSError:print('网关未运行。')
        return
    require_agent_mode(LABEL)
    if options.action in ('stop','uninstall'):
        if loaded():subprocess.run(['launchctl','bootout',target],check=True)
        if options.action=='uninstall' and plist.exists():plist.unlink()
        print('已停止手机网关；配对数据库保留。'+('下次登录仍可自动启动。' if options.action=='stop' else '自动启动已移除。'))
        return
    python=MONITOR/'.venv/bin/python'
    if not python.exists():parser.error('先部署主监控：.venv/bin/python scripts/deploy_launchagent.py')
    with urlopen('http://127.0.0.1:8766/api/state',timeout=5) as response:
        if response.status!=200:parser.error('主监控 8766 尚未正常启动。')
    with urlopen('http://127.0.0.1:8766/static/acp.js',timeout=5) as response:
        if b'mobile_auth_required' not in response.read():
            parser.error('主监控页面尚未更新到手机请求恢复版本。请先运行 scripts/deploy_launchagent.py。')
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700)
    (RUNTIME/'data').mkdir(exist_ok=True,mode=0o700)
    with (RUNTIME/'data/deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if loaded():subprocess.run(['launchctl','bootout',target],check=True)
        for _ in range(100):
            with socket.socket() as sock:free=sock.connect_ex(('127.0.0.1',8767))!=0
            if free and not loaded():break
            time.sleep(.1)
        else:parser.error('8767 仍被占用，请先确认占用进程。')
        package=RUNTIME/'server';package.mkdir(exist_ok=True);(package/'__init__.py').touch()
        for name in ('mobile_access.py','mobile_gateway.py','mobile_admin.py','store.py'):shutil.copy2(SOURCE/'server'/name,package/name)
        plist.parent.mkdir(exist_ok=True,parents=True)
        temp=plist.with_suffix('.tmp');temp.write_bytes(plistlib.dumps(payload(RUNTIME,python)));temp.chmod(0o600);temp.replace(plist)
        subprocess.run(['plutil','-lint',str(plist)],check=True)
        for _ in range(5):
            result=subprocess.run(['launchctl','bootstrap',domain,str(plist)],capture_output=True)
            if result.returncode==0:break
            time.sleep(1)
        else:parser.error('网关启动失败，请检查 LaunchAgent 配置和 data/gateway.err.log。')
        for _ in range(40):
            try:
                with urlopen('http://127.0.0.1:8767/health',timeout=1) as response:
                    if json.load(response).get('service')=='charles-mobile-gateway':
                        print('网关就绪：http://127.0.0.1:8767；下一步在目标 Mac 配置 Tailscale Serve。');return
            except (OSError,ValueError):pass
            time.sleep(.25)
        parser.error('网关尚未就绪，请检查 '+str(RUNTIME/'data/gateway.err.log'))

if __name__=='__main__':main()
