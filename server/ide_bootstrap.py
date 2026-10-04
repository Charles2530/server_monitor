"""Pinned, user-local editor installation executed only in a verified CCI shell."""
import json
from pathlib import Path
from . import ide_codex, ide_appearance

VERSION = '4.139.1'
SHA256 = '53029be6c5781b7bca49b815fcc9a2a3fc111813ad8c9965b2c0f0d2985a0674'


def bootstrap_script(instance, address, *, install_optional=True):
    settings = json.dumps({'instance': instance, 'address': address, 'version': VERSION, 'sha256': SHA256, 'install_optional': install_optional})
    bundle = ide_appearance.appearance_bundle()
    if not install_optional:
        bundle = {key: value for key, value in bundle.items() if key != 'payload'}
    return (Path(ide_codex.__file__).read_text() + '\n' + Path(ide_appearance.__file__).read_text()
            + '\nAPPEARANCE_BUNDLE = ' + repr(bundle)
            + '\nSETTINGS = ' + repr(settings) + '\n' + _SCRIPT)


_SCRIPT = r'''
import fcntl, hashlib, json, os, pathlib, platform, secrets, shutil, socket, subprocess, tarfile, tempfile, time, urllib.request
s = json.loads(SETTINGS)
progress = globals().get('_ilab_report_progress', lambda *args: None)
if socket.gethostname() != s['instance']:
    raise RuntimeError('容器身份已变化，请刷新任务后重试。')
if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
    raise RuntimeError('当前编辑器仅支持 Linux x86_64 容器。')
os.umask(0o077)
base = pathlib.Path.home() / '.local/share/ilab-cci-editor'
base.mkdir(parents=True, exist_ok=True, mode=0o700)
if base.is_symlink() or base.stat().st_uid != os.getuid():
    raise RuntimeError('编辑器目录权限异常。')
os.chmod(base, 0o700)
lock = (base / 'install.lock').open('a')
progress('waiting_lock')
fcntl.flock(lock, fcntl.LOCK_EX)
progress('checking')
version = s['version']
folder = base / ('code-server-' + version + '-linux-amd64')
binary = folder / 'bin/code-server'
state_file = base / 'state.json'
env = dict(os.environ)
for key in ('PASSWORD', 'HASHED_PASSWORD', 'CODE_SERVER_CONFIG', 'NODE_OPTIONS'):
    env.pop(key, None)
env['SHELL'] = '/bin/bash'
def optional_setup():
    codex = prepare_codex(base, env, binary, progress=progress, install=s['install_optional'])
    appearance = (prepare_appearance(base, env, binary, APPEARANCE_BUNDLE, progress=progress)
                  if s['install_optional'] else {'version': APPEARANCE_BUNDLE['version'], 'installed': appearance_installed(base, APPEARANCE_BUNDLE)})
    return codex, appearance
codex, appearance = optional_setup() if binary.is_file() else (None, None)

def owned_process(state):
    try:
        pid = int(state['pid'])
        cmdline = pathlib.Path('/proc/%d/cmdline' % pid).read_bytes()
        return str(folder).encode() in cmdline and state['address'] == s['address']
    except Exception:
        return False

def healthy(state):
    try:
        if not owned_process(state): return False
        with socket.create_connection((s['address'], int(state['port'])), 2):
            return True
    except Exception:
        return False

try:
    state = json.loads(state_file.read_text())
except Exception:
    state = {}
if owned_process(state):
    for attempt in range(10):
        if healthy(state): break
        time.sleep(1)
    else: raise RuntimeError('编辑器进程仍在运行但暂不可用，请稍后重试。')
if healthy(state):
    RESULT = {k: state[k] for k in ('port', 'password', 'workspace')}
else:
    if not binary.is_file():
        temporary = pathlib.Path(tempfile.mkdtemp(prefix='install-', dir=str(base)))
        try:
            archive = temporary / 'release.tar.gz'
            url = 'https://github.com/coder/code-server/releases/download/v' + version + '/code-server-' + version + '-linux-amd64.tar.gz'
            digest = hashlib.sha256()
            total = 0
            progress('download_editor', 0)
            with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as output:
                expected = download_size(response)
                progress('download_editor', 0, expected)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk: break
                    total += len(chunk)
                    if total > 250 * 1024 * 1024: raise RuntimeError('编辑器下载大小异常。')
                    digest.update(chunk)
                    output.write(chunk)
                    progress('download_editor', total, expected)
            progress('verify_editor')
            if digest.hexdigest() != s['sha256']:
                raise RuntimeError('编辑器下载校验失败，请重试。')
            progress('extract_editor')
            with tarfile.open(str(archive)) as bundle:
                root = temporary.resolve()
                for member in bundle.getmembers():
                    destination = (temporary / member.name).resolve()
                    if root not in destination.parents or member.isdev() or member.isfifo():
                        raise RuntimeError('编辑器安装包路径异常。')
                    if member.issym() or member.islnk():
                        link = (destination.parent / member.linkname).resolve() if member.issym() else (temporary / member.linkname).resolve()
                        if root not in link.parents: raise RuntimeError('编辑器安装包链接异常。')
                bundle.extractall(str(temporary))
            (temporary / folder.name).rename(folder)
        finally:
            shutil.rmtree(str(temporary), ignore_errors=True)
    if codex is None: codex, appearance = optional_setup()
    progress('start_editor')
    with socket.socket() as probe:
        probe.bind((s['address'], 0))
        port = probe.getsockname()[1]
    password = secrets.token_urlsafe(32)
    workspace = '/workspace' if pathlib.Path('/workspace').is_dir() else str(pathlib.Path.home())
    config = base / 'config.json'
    config.write_text(json.dumps({'bind-addr': s['address'] + ':' + str(port), 'auth': 'password', 'password': password, 'cert': False}))
    os.chmod(config, 0o600)
    user = base / 'user-data'
    (user / 'User').mkdir(parents=True, exist_ok=True)
    settings = user / 'User/settings.json'
    if not settings.exists():
        settings.write_text(json.dumps({'workbench.colorTheme': 'Default Dark Modern', 'workbench.startupEditor': 'none', 'telemetry.telemetryLevel': 'off', 'update.mode': 'none'}))
    with (base / 'editor.log').open('ab') as log:
        proc = subprocess.Popen([str(binary), '--config', str(config), '--user-data-dir', str(user), '--extensions-dir', str(base / 'extensions'), '--disable-telemetry', '--disable-update-check', '--disable-workspace-trust', workspace], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, start_new_session=True)
    state = {'pid': proc.pid, 'address': s['address'], 'port': port, 'password': password, 'workspace': workspace}
    pending_state = base / 'state.tmp'
    pending_state.write_text(json.dumps(state))
    os.chmod(pending_state, 0o600)
    pending_state.replace(state_file)
    for attempt in range(60):
        if healthy(state): break
        if proc.poll() is not None: raise RuntimeError('容器内编辑器启动失败，请检查专用 editor.log。')
        time.sleep(1)
    else: raise RuntimeError('容器内编辑器启动超时。')
    RESULT = {k: state[k] for k in ('port', 'password', 'workspace')}
RESULT['codex'] = codex
RESULT['appearance'] = appearance
'''
