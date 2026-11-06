"""Pinned, user-local editor installation executed only in a verified CCI shell."""
import ast
import json
import io
import tokenize
from functools import lru_cache
from pathlib import Path
from . import ide_codex, ide_appearance, ide_extensions

VERSION = '4.139.1'
SHA256 = '53029be6c5781b7bca49b815fcc9a2a3fc111813ad8c9965b2c0f0d2985a0674'
ARM64_SHA256 = '0edb4b60d9c4744b2dd14b0911e3c2e6dd8c6f3c13bd58bda23ae744e59e7df1'


@lru_cache(maxsize=1)
def _packed_modules():
    source = (Path(ide_codex.__file__).read_bytes() + b'\n'
              + Path(ide_appearance.__file__).read_bytes() + b'\n'
              + Path(ide_extensions.__file__).read_bytes())
    # Keep documentation locally without sending it over the control shell.
    lines = source.decode().splitlines(keepends=True)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for line in range(node.lineno - 1, node.end_lineno): lines[line] = '\n'
    source = ''.join(lines).encode()
    source = tokenize.untokenize(token for token in tokenize.tokenize(io.BytesIO(source).readline)
                                if token.type != tokenize.COMMENT)
    # run_python compresses the entire payload once. Nesting an already
    # compressed module here makes the control-shell transfer larger.
    return repr(source.decode())


@lru_cache(maxsize=1)
def _appearance_bundle():
    return ide_appearance.appearance_bundle()


def bootstrap_script(instance, address, *, install_optional=True, download_proxy=None, activate_proxy=False):
    settings = json.dumps({'instance': instance, 'address': address, 'version': VERSION,
                          'sha256': {'amd64': SHA256, 'arm64': ARM64_SHA256},
                          'install_optional': install_optional, 'download_proxy': download_proxy,
                          'activate_proxy': activate_proxy})
    bundle = _appearance_bundle()
    if not install_optional:
        bundle = {key: value for key, value in bundle.items() if key != 'payload'}
    packed = _packed_modules()
    return ('exec(compile(' + packed + ", '<bootstrap-modules>', 'exec'))\n"
            + 'APPEARANCE_BUNDLE = ' + repr(bundle)
            + '\nSETTINGS = ' + repr(settings) + '\n' + _SETUP_CHECKS + _SCRIPT)


def setup_status_script(instance):
    bundle = {key: value for key, value in _appearance_bundle().items() if key != 'payload'}
    return ('exec(compile(' + _packed_modules() + ", '<bootstrap-modules>', 'exec'))\n"
            + 'APPEARANCE_BUNDLE = ' + repr(bundle) + '\nINSTANCE = ' + repr(instance)
            + '\nEDITOR_VERSION = ' + repr(VERSION) + '\n' + _SETUP_CHECKS + r'''
import socket
if INSTANCE is not None and socket.gethostname() != INSTANCE:
    raise RuntimeError('容器身份已变化，请刷新任务后重试。')
arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine().lower())
if platform.system() != 'Linux' or arch is None:
    raise RuntimeError('当前编辑器仅支持 Linux x86_64 或 ARM64。')
base = pathlib.Path.home() / '.local/share/ilab-cci-editor'
if base.is_symlink() or (base.exists() and base.stat().st_uid != os.getuid()):
    raise RuntimeError('编辑器目录权限异常。')
RESULT = _ilab_setup_status(base, APPEARANCE_BUNDLE, base / ('code-server-' + EDITOR_VERSION + '-linux-' + arch))
''')


_SETUP_CHECKS = r'''
import fcntl, json, os, pathlib, platform, time
def _ilab_setup_status(base, bundle, folder, check_lock=True):
    extensions = base / 'extensions'
    codex_home = pathlib.Path(os.environ.get('CODEX_HOME') or str(pathlib.Path.home() / '.codex')).expanduser()
    if not codex_home.is_absolute(): raise RuntimeError('CODEX_HOME 必须为绝对路径。')
    codex = {'configured': any((codex_home / name).is_file() for name in ('config.toml', 'auth.json')),
             'installed': codex_installed(extensions)}
    appearance = {'version': bundle['version'], 'installed': appearance_installed(base, bundle)}
    found = {spec['id']: extension_installed(extensions, spec) for spec in extension_specs()}
    configured = extension_setup_status(base)
    tools = {'version': EXTENSIONS_VERSION, 'installed': all(found.values()) and configured is not None,
             'extensions': found, 'locale': configured['locale'] if configured else None}
    roots = [str(folder / 'lib/vscode/extensions')]
    roots.extend(str(extension / 'webview/assets') for extension in codex_extensions(extensions)
                 if (extension / 'webview/assets').is_dir())
    if appearance['installed']:
        roots.append(str(extensions / ('ilab.workspace-appearance-' + bundle['version'])))
    running = False
    if check_lock:
        try:
            with (base / 'install.lock').open('r') as lock:
                try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: running = True
        except FileNotFoundError: pass
    result = {'codex': codex, 'appearance': appearance, 'extensions': tools,
              'asset_roots': roots, 'install_running': running}
    if running:
        try:
            path = base / 'setup-progress.json'
            if path.stat().st_size <= 16384 and time.time() - path.stat().st_mtime < 1800:
                record = json.loads(path.read_text())
                result['progress'] = {key: record[key] for key in ('stage', 'downloaded_bytes', 'total_bytes') if key in record}
        except (OSError, ValueError, TypeError): pass
    return result
'''


_SCRIPT = r'''
import fcntl, hashlib, json, os, pathlib, platform, secrets, shutil, socket, subprocess, tarfile, tempfile, time, urllib.request
s = json.loads(SETTINGS)
auto_download_proxy = False
if (s.get('activate_proxy') and not s.get('download_proxy')
        and not any(os.environ.get(key) for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'))):
    # Only import download settings from the user's helper, without its mirror/SSH edits.
    try:
        proxy = subprocess.run(['/bin/bash', '-ic',
            'if command -v proxy_on >/dev/null 2>&1; then proxy_on >/dev/null 2>&1 && env -0; fi'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
            env={**os.environ, 'CHARLES_AUTO_MIRRORS': '0', 'CHARLES_SSH_PROXY': '0'})
        if proxy.returncode == 0:
            for entry in proxy.stdout.decode(errors='replace').split('\0'):
                key, separator, value = entry.partition('=')
                if separator and key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY', 'no_proxy', 'NO_PROXY'):
                    if not os.environ.get(key):
                        os.environ[key] = value
                        if key.lower() != 'no_proxy' and value: auto_download_proxy = True
    except (OSError, subprocess.TimeoutExpired):
        pass  # Hosts with no helper can still download directly.
if s.get('download_proxy'):
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler(
        {scheme: s['download_proxy'] for scheme in ('http', 'https')})))
progress = globals().get('_ilab_report_progress', lambda *args: None)
report_progress = progress
def progress(stage, *counts):
    downloaded_bytes = counts[0] if counts else None
    total_bytes = counts[1] if len(counts) > 1 else None
    if s['install_optional'] and globals().get('lock_acquired', False):
        try:
            pending = base / 'setup-progress.tmp'
            pending.write_text(json.dumps({'stage': stage,
                                          'downloaded_bytes': downloaded_bytes, 'total_bytes': total_bytes}))
            pending.replace(base / 'setup-progress.json')
        except OSError: pass
    try: report_progress(stage, *counts)
    except OSError: pass  # A lost control shell must not abort the installer.
if s['instance'] is not None and socket.gethostname() != s['instance']:
    raise RuntimeError('容器身份已变化，请刷新任务后重试。')
arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine().lower())
if platform.system() != 'Linux' or arch is None:
    raise RuntimeError('当前编辑器仅支持 Linux x86_64 或 ARM64。')
os.umask(0o077)
base = pathlib.Path.home() / '.local/share/ilab-cci-editor'
base.mkdir(parents=True, exist_ok=True, mode=0o700)
if base.is_symlink() or base.stat().st_uid != os.getuid():
    raise RuntimeError('编辑器目录权限异常。')
os.chmod(base, 0o700)
lock = (base / 'install.lock').open('a')
lock_acquired = False
progress('waiting_lock')
if s['install_optional']:
    fcntl.flock(lock, fcntl.LOCK_EX)
    lock_acquired = True
else:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_acquired = True
    except BlockingIOError: pass
progress('checking')
version = s['version']
folder = base / ('code-server-' + version + '-linux-' + arch)
binary = folder / 'bin/code-server'
state_file = base / 'state.json'
env = dict(os.environ)
for key in ('PASSWORD', 'HASHED_PASSWORD', 'CODE_SERVER_CONFIG', 'NODE_OPTIONS'):
    env.pop(key, None)
env['SHELL'] = '/bin/bash'
def optional_setup():
    codex = prepare_codex(base, env, binary, progress=progress, install=s['install_optional'],
                          configure_permissions=s['instance'] is not None,
                          allow_direct=auto_download_proxy)
    appearance = (prepare_appearance(base, env, binary, APPEARANCE_BUNDLE, progress=progress)
                  if s['install_optional'] else {'version': APPEARANCE_BUNDLE['version'], 'installed': appearance_installed(base, APPEARANCE_BUNDLE)})
    # The fast bootstrap only checks optional extensions; full setup installs them in the background.
    extensions = prepare_extensions(base, env, binary, progress=progress, install=s['install_optional']) if binary.is_file() else None
    return codex, appearance, extensions
if binary.is_file() and not lock_acquired:
    checked = _ilab_setup_status(base, APPEARANCE_BUNDLE, folder, check_lock=False)
    codex, appearance, extensions = (checked[key] for key in ('codex', 'appearance', 'extensions'))
else:
    codex, appearance, extensions = optional_setup() if binary.is_file() else (None, None, None)

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
    if not lock_acquired: raise RuntimeError('编辑器初始化仍在进行中，请稍后重试。')
    if not binary.is_file():
        temporary = pathlib.Path(tempfile.mkdtemp(prefix='install-', dir=str(base)))
        try:
            archive = temporary / 'release.tar.gz'
            url = 'https://github.com/coder/code-server/releases/download/v' + version + '/' + folder.name + '.tar.gz'
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
            if digest.hexdigest() != s['sha256'][arch]:
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
    if codex is None: codex, appearance, extensions = optional_setup()
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
        settings.write_text(json.dumps({
            'workbench.colorTheme': 'Default Dark Modern',
            'workbench.startupEditor': 'none',
            'telemetry.telemetryLevel': 'off',
            'update.mode': 'none',
            'extensions.autoUpdate': False,
            'extensions.autoCheckUpdates': False,
        }))
    ensure_update_defaults(settings)
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
RESULT['extensions'] = extensions
RESULT['asset_roots'] = [str(folder / 'lib/vscode/extensions')]
RESULT['asset_roots'].extend(str(extension / 'webview/assets')
                            for extension in codex_extensions(base / 'extensions')
                            if (extension / 'webview/assets').is_dir())
if appearance and appearance.get('installed'):
    RESULT['asset_roots'].append(str(base / 'extensions' / ('ilab.workspace-appearance-' + APPEARANCE_BUNDLE['version'])))
'''
