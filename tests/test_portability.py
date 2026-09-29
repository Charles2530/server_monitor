import importlib.util
import pytest
from pathlib import Path
from packaging.requirements import Requirement


def test_browser_selector_exists():
    assert importlib.util.find_spec('server.browser') is not None


@pytest.mark.parametrize('version,needed',[('3.9',True),('3.10',True),('3.11',False),('3.12',False)])
def test_asyncio_backport_is_only_installed_for_old_python(version,needed):
    lines=(Path(__file__).resolve().parents[1]/'requirements.lock').read_text().splitlines()
    requirement=next(Requirement(line) for line in lines if line.startswith('backports.asyncio.runner'))
    selected=requirement.marker is None or requirement.marker.evaluate({'python_version':version})
    assert selected is needed


@pytest.mark.parametrize('platform,chrome_exists,channel',[
    ('darwin',True,'chrome'),('darwin',False,None),('linux',False,None),('linux',True,None),
])
def test_auto_browser_matches_installed_environment(monkeypatch,platform,chrome_exists,channel):
    from server import browser
    monkeypatch.delenv('MONITOR_BROWSER_CHANNEL',raising=False)
    monkeypatch.setattr(browser.sys,'platform',platform)
    monkeypatch.setattr(browser.Path,'exists',lambda _:chrome_exists)
    options=browser.browser_launch_options()
    assert options.get('channel')==channel
    assert options['headless'] is True


@pytest.mark.parametrize('channel,expected',[('chrome','chrome'),('chromium',None)])
def test_browser_override(monkeypatch,channel,expected):
    from server.browser import browser_launch_options
    monkeypatch.setenv('MONITOR_BROWSER_CHANNEL',channel)
    assert browser_launch_options(headless=False)==({'headless':False,'channel':expected} if expected else {'headless':False})


def test_setup_browser_installs_bundled_browser_with_optional_system_deps(monkeypatch):
    from scripts import setup_browser
    monkeypatch.setattr(setup_browser,'browser_launch_options',lambda:{'headless':True})
    commands=[]
    monkeypatch.setattr(setup_browser.subprocess,'run',lambda argv,**kwargs:commands.append(argv))
    setup_browser.main(['--with-deps'])
    assert commands==[[setup_browser.sys.executable,'-m','playwright','install','--with-deps','chromium']]


def test_setup_browser_reuses_chrome_without_download(monkeypatch):
    from scripts import setup_browser
    monkeypatch.setattr(setup_browser,'browser_launch_options',lambda:{'headless':True,'channel':'chrome'})
    def unexpected(*args,**kwargs):raise AssertionError('Chrome does not need a bundled browser download')
    monkeypatch.setattr(setup_browser.subprocess,'run',unexpected)
    setup_browser.main([])


@pytest.mark.parametrize('app_dir,matches',[(None,True),('/other/checkout',False)])
def test_linux_pid_detection_without_ps(monkeypatch,tmp_path,app_dir,matches):
    from scripts import monitorctl as ctl
    pid_file=tmp_path/'server.pid';pid_file.write_text('321')
    monkeypatch.setattr(ctl,'PID_FILE',pid_file)
    monkeypatch.setattr(ctl.sys,'platform','linux')
    argv=['python','-m','uvicorn','server.main:app','--app-dir',app_dir or str(ctl.ROOT)]
    original=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda path:b'\0'.join(arg.encode() for arg in argv)+b'\0' if str(path)=='/proc/321/cmdline' else original(path))
    def no_ps(*args,**kwargs):raise FileNotFoundError('ps is not installed')
    monkeypatch.setattr(ctl.subprocess,'check_output',no_ps)
    assert ctl.running_pid()==(321 if matches else None)
