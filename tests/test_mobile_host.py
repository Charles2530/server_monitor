from pathlib import Path
from scripts.mobile_host import payload


def test_optional_gateway_agent_is_loopback_and_independent():
    root=Path('/Users/test/Library/Application Support/CharlesCloudMonitorMobile')
    data=payload(root,Path('/private/monitor/.venv/bin/python'))
    args=data['ProgramArguments']
    assert data['Label']=='charles.server-monitor-mobile'
    assert args[args.index('--host')+1]=='127.0.0.1'
    assert args[args.index('--port')+1]=='8767'
    assert '--factory' in args and '--no-access-log' in args
    assert data['EnvironmentVariables']['MONITOR_MOBILE_DATA']==str(root/'data')
    assert data['RunAtLoad'] and data['KeepAlive']
