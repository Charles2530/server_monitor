#!/usr/bin/env python3
"""Install the authorized Feishu table synchronizer as a private macOS LaunchAgent."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
try:
    from .boot_services import require_agent_mode
except ImportError:
    from boot_services import require_agent_mode

SOURCE=Path(__file__).resolve().parent
RUNTIME=Path.home()/'Library/Application Support/CharlesMonitorBitable'
LABEL='charles.monitor-bitable'
PLIST=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')


def main():
    if sys.platform!='darwin':raise SystemExit('macOS is required')
    require_agent_mode(LABEL)
    os.umask(0o077)
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(RUNTIME,0o700)
    sys.path.insert(0,str(SOURCE))
    from feishu_bitable import read_config
    read_config(RUNTIME/'config.json')
    target='gui/%d/%s'%(os.getuid(),LABEL)
    loaded=subprocess.run(['/bin/launchctl','print',target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    if loaded:subprocess.run(['/bin/launchctl','bootout',target],check=True)
    import fcntl
    with (RUNTIME/'sync.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        for name in ('feishu_bitable.py','feishu_resources.py'):
            temporary=RUNTIME/(name+'.tmp')
            shutil.copyfile(SOURCE/name,temporary)
            os.chmod(temporary,0o600)
            temporary.replace(RUNTIME/name)
    # Validate real permissions/schema and complete one sync before enabling autostart.
    subprocess.run(['/usr/bin/python3',str(RUNTIME/'feishu_bitable.py'),'--init'],check=True)
    payload={
        'Label':LABEL,
        'ProgramArguments':['/usr/bin/python3',str(RUNTIME/'feishu_bitable.py')],
        'WorkingDirectory':str(RUNTIME),'RunAtLoad':True,'KeepAlive':True,
        'ThrottleInterval':60,'ExitTimeOut':20,'ProcessType':'Background',
        'EnvironmentVariables':{'PYTHONUNBUFFERED':'1'},
        'StandardOutPath':str(RUNTIME/'sync.out.log'),
        'StandardErrorPath':str(RUNTIME/'sync.err.log')}
    PLIST.parent.mkdir(parents=True,exist_ok=True)
    temporary=PLIST.with_suffix('.tmp')
    temporary.write_bytes(plistlib.dumps(payload,sort_keys=False))
    os.chmod(temporary,0o600);temporary.replace(PLIST)
    subprocess.run(['/usr/bin/plutil','-lint',str(PLIST)],check=True)
    subprocess.run(['/bin/launchctl','bootstrap','gui/%d'%os.getuid(),str(PLIST)],check=True)
    print('Feishu table sync installed: '+str(RUNTIME))


if __name__=='__main__':main()
