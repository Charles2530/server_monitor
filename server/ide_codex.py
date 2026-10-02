"""Container-local Codex setup; never read or copy credentials into the monitor."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

CODEX_VERSION = '26.5908.31748'
CODEX_SHA256 = '7a359b93b200e4406eb19858a48fa7e2da6ed557d3579ad8ec6b63ee4d354b45'
CODEX_URL = ('https://open-vsx.org/api/openai/chatgpt/linux-x64/' + CODEX_VERSION
             + '/file/openai.chatgpt-' + CODEX_VERSION + '@linux-x64.vsix')


def codex_installed(extensions):
    try:
        obsolete = json.loads((extensions / '.obsolete').read_text())
    except (OSError, ValueError):
        obsolete = {}
    if not isinstance(obsolete, dict): obsolete = {}
    for manifest in extensions.glob('openai.chatgpt-*/package.json'):
        if obsolete.get(manifest.parent.name): continue
        try:
            package = json.loads(manifest.read_text())
            main = package.get('main')
            if (package.get('publisher') == 'openai' and package.get('name') == 'chatgpt'
                    and isinstance(main, str) and (manifest.parent / main).is_file()):
                return True
        except (OSError, ValueError):
            continue
    return False


def download_size(response):
    try:
        value = int(response.headers.get('Content-Length', ''))
        return value if 0 < value <= 250 * 1024 * 1024 else None
    except (AttributeError, ValueError, TypeError):
        return None


def install_codex(base, binary, env, progress=None):
    progress = progress or (lambda *args: None)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix='codex-install-', dir=str(base)))
    try:
        archive = temporary / ('openai.chatgpt-' + CODEX_VERSION + '.vsix')
        digest = hashlib.sha256(); total = 0; started = time.monotonic()
        progress('download_codex', 0)
        with urllib.request.urlopen(CODEX_URL, timeout=45) as response, archive.open('wb') as output:
            expected = download_size(response)
            progress('download_codex', 0, expected)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk: break
                total += len(chunk)
                if total > 250 * 1024 * 1024 or time.monotonic() - started > 240:
                    raise RuntimeError('Codex 插件下载超出限制。')
                digest.update(chunk); output.write(chunk)
                progress('download_codex', total, expected)
        progress('verify_codex')
        if digest.hexdigest() != CODEX_SHA256:
            raise RuntimeError('Codex 插件下载校验失败。')
        progress('install_codex')
        install_config = temporary / 'config.json'
        install_config.write_text('{}')
        with (base / 'codex-install.log').open('ab') as log:
            result = subprocess.run([str(binary), '--config', str(install_config), '--user-data-dir', str(base / 'user-data'),
                                     '--extensions-dir', str(base / 'extensions'),
                                     '--install-extension', str(archive)],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    env=env, timeout=180)
        if result.returncode or not codex_installed(base / 'extensions'):
            raise RuntimeError('Codex 插件安装失败，请检查容器专用 codex-install.log。')
    finally:
        shutil.rmtree(str(temporary), ignore_errors=True)


def prepare_codex(base, env, binary, progress=None):
    home = Path(env.get('HOME') or str(Path.home()))
    configured = env.get('CODEX_HOME')
    codex_home = Path(configured).expanduser() if configured else home / '.codex'
    if not codex_home.is_absolute():
        raise RuntimeError('CODEX_HOME 必须为绝对路径。')
    if not any((codex_home / filename).is_file() for filename in ('config.toml', 'auth.json')):
        return {'configured': False, 'installed': False}
    # Use the original files and their future changes. Do not deserialize, duplicate,
    # chmod or return auth/config content, including symlinked AFS configurations.
    env['CODEX_HOME'] = str(codex_home)
    if not codex_installed(base / 'extensions'):
        try:
            install_codex(base, binary, env, progress=progress)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return {'configured': True, 'installed': False}
    return {'configured': True, 'installed': True}
