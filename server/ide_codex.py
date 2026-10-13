"""Container-local Codex setup; never read or copy credentials into the monitor."""
import hashlib
import json
import platform
import shlex
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


def full_access_config(text):
    try:
        import tomllib as toml
    except ImportError:
        import tomli as toml
    original = toml.loads(text)
    desired = {'approval_policy': 'never', 'sandbox_mode': 'danger-full-access'}
    if all(original.get(key) == value for key, value in desired.items()):
        return text
    edits, found = [], set()
    i, start, depth, quote, comment = 0, 0, 0, None, None
    root_end = len(text)
    while i < len(text):
        if quote:
            if text.startswith(quote, i):
                end = i + len(quote)
                if len(quote) == 3:
                    while end < len(text) and text[end] == quote[0]: end += 1
                i, quote = end, None
                continue
            if quote[0] == '"' and text[i] == '\\': i += 2; continue
            i += 1
            continue
        char = text[i]
        if char == '#':
            if comment is None and depth == 0: comment = i
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif char in ('"', "'"):
            quote = char * 3 if text.startswith(char * 3, i) else char
            i += len(quote)
            continue
        elif char in '[{':
            if depth == 0 and not text[start:i].strip():
                root_end = start
                break
            depth += 1
        elif char in ']}': depth -= 1
        if i == len(text) or (text[i] == '\n' and depth == 0):
            end = i + (i < len(text))
            statement = text[start:end]
            values = toml.loads(statement)
            for key in desired.keys() & values.keys():
                if not isinstance(values[key], str): raise ValueError('Permission must be a scalar string')
                value_start = text.index('=', start, end) + 1
                while value_start < end and text[value_start] in ' \t': value_start += 1
                value_end = comment if comment is not None else end
                while value_end > value_start and text[value_end-1] in ' \t\r\n': value_end -= 1
                edits.append((value_start, value_end, json.dumps(desired[key])))
                found.add(key)
            start, comment = end, None
        i += 1
    if start < len(text) and root_end == len(text) and text[start:].strip() and not text.endswith('\n'):
        return full_access_config(text + '\n')
    missing = ''.join(key + ' = ' + json.dumps(value) + '\n' for key, value in desired.items() if key not in found)
    if missing:
        prefix = '\n' if root_end and text[root_end-1] != '\n' else ''
        edits.append((root_end, root_end, prefix + missing))
    updated = text
    for start, end, replacement in sorted(edits, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    expected = {**original, **desired}
    if toml.loads(updated) != expected: raise ValueError('Unexpected TOML change')
    return updated


def ensure_full_access_defaults(codex_home):
    import fcntl
    import os
    import stat
    temporary = None
    try:
        path = (Path(codex_home) / 'config.toml').resolve()
        with (Path(codex_home) / '.ilab-permissions.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            exists = path.exists()
            info = path.stat() if exists else None
            if info and not stat.S_ISREG(info.st_mode): return False
            raw = path.read_bytes() if exists else b''
            updated = full_access_config(raw.decode('utf-8')).encode('utf-8')
            if updated == raw: return True
            fd, temporary = tempfile.mkstemp(prefix='.ilab-config-', dir=str(path.parent))
            with os.fdopen(fd, 'wb') as output:
                os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode) if info else 0o600)
                if info and (info.st_uid != os.getuid() or info.st_gid != os.getgid()):
                    os.fchown(output.fileno(), info.st_uid, info.st_gid)
                output.write(updated); output.flush(); os.fsync(output.fileno())
            if exists and path.read_bytes() != raw: return False
            if not exists and path.exists(): return False
            os.replace(temporary, path)
            temporary = None
            return True
    except (OSError, ValueError, UnicodeError, ImportError, RuntimeError):
        return False
    finally:
        if temporary:
            try: os.unlink(temporary)
            except OSError: pass


def codex_installed(extensions):
    return next(codex_extensions(extensions), None) is not None


def codex_extensions(extensions):
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
                yield manifest.parent
        except (OSError, ValueError):
            continue


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
        arm64 = platform.machine().lower() in ('aarch64', 'arm64')
        url = CODEX_URL.replace('linux-x64', 'linux-arm64') if arm64 else CODEX_URL
        checksum = 'ef38b36a93f7f3d9b8559d7add5c895cb38854f07b55806630ae150c85d7f198' if arm64 else CODEX_SHA256
        with urllib.request.urlopen(url, timeout=45) as response, archive.open('wb') as output:
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
        if digest.hexdigest() != checksum:
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


SHARED_CODEX_BRIDGE = r"""'use strict';
const { spawnSync } = require('node:child_process');
const cli = __CODEX_CLI__;
const args = process.argv.slice(2);
const server = args.indexOf('app-server');
if (server < 0 || args.slice(server + 1).some(arg => !arg.startsWith('-'))) {
  const child = spawnSync(cli, args, {stdio:'inherit'});
  process.exit(child.status ?? 1);
}
const started = spawnSync(cli, ['app-server','daemon','start'], {stdio:['ignore',2,2], timeout:30000});
if (started.status !== 0) process.exit(started.status ?? 1);
const WebSocket = require(__WS_MODULE__);
const home = process.env.CODEX_HOME || require('node:path').join(require('node:os').homedir(), '.codex');
const socket = require('node:path').join(home, 'app-server-control', 'app-server-control.sock');
const ws = new WebSocket('ws://localhost/', {
  createConnection: () => require('node:net').connect(socket),
  handshakeTimeout: 10000,
  maxPayload: 64 * 1024 * 1024,
});
ws.on('error', error => { console.error('Codex shared server connection failed:', error.code || error.name); process.exit(1); });
let inputEnded = false;
ws.on('close', code => process.exit(inputEnded || code === 1000 ? 0 : 1));
ws.on('message', data => { if (!process.stdout.write(data.toString() + '\n')) ws.pause(); });
process.stdout.on('drain', () => ws.resume());
process.stdout.on('error', () => ws.close(1000));
ws.on('open', async () => {
  const lines = require('node:readline').createInterface({input:process.stdin, crlfDelay:Infinity});
  try {
    for await (const line of lines) {
      if (line.trim()) await new Promise((resolve, reject) => ws.send(line, error => error ? reject(error) : resolve()));
    }
    inputEnded = true;
    ws.close(1000);
  } catch (_) { ws.terminate(); process.exit(1); }
});
"""


def configure_shared_codex(base, env, binary):
    """Connect the IDE to the CLI daemon so both clients share thread ownership."""
    cli = shutil.which('codex', path=env.get('PATH', ''))
    root = Path(binary).resolve().parent.parent
    node, ws = root / 'lib/node', root / 'node_modules/ws'
    if not cli or not node.is_file() or not ws.is_dir(): return False
    settings = base / 'user-data/User/settings.json'
    launcher = base / 'shared-codex'
    try:
        exists = settings.exists()
        original = settings.read_text() if exists else '{}'
        current = json.loads(original)
        if not isinstance(current, dict): return False
        if current.get('chatgpt.cliExecutable') not in (None, '', str(launcher)): return False
        supported = subprocess.run([cli, 'app-server', 'daemon', 'start', '--help'],
                                   env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10)
        if supported.returncode: return False
        bridge = base / 'shared-codex.cjs'
        source = SHARED_CODEX_BRIDGE.replace('__CODEX_CLI__', json.dumps(cli)).replace('__WS_MODULE__', json.dumps(str(ws)))
        shell = '#!/bin/sh\nexec ' + shlex.quote(str(node)) + ' ' + shlex.quote(str(bridge)) + ' "$@"\n'
        for path, content in ((bridge, source), (launcher, shell)):
            if path.exists() and path.read_text() == content: continue
            temporary = path.with_suffix(path.suffix + '.tmp')
            temporary.write_text(content); temporary.chmod(0o700); temporary.replace(path)
        if current.get('chatgpt.cliExecutable') == str(launcher): return True
        settings.parent.mkdir(parents=True, exist_ok=True)
        backup = settings.with_name('settings.before-shared-codex.json')
        if settings.exists() and not backup.exists(): backup.write_text(original)
        updated = {**current, 'chatgpt.cliExecutable': str(launcher)}
        temporary = settings.with_suffix('.tmp')
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n')
        if settings.exists() != exists or (exists and settings.read_text() != original):
            temporary.unlink()
            return False
        temporary.replace(settings)
        return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def prepare_codex(base, env, binary, progress=None, *, install=True, configure_permissions=True):
    home = Path(env.get('HOME') or str(Path.home()))
    configured = env.get('CODEX_HOME')
    codex_home = Path(configured).expanduser() if configured else home / '.codex'
    if not codex_home.is_absolute():
        raise RuntimeError('CODEX_HOME 必须为绝对路径。')
    if not any((codex_home / filename).is_file() for filename in ('config.toml', 'auth.json')):
        return {'configured': False, 'installed': False}
    # Reuse the container home; only the explicitly requested permission defaults
    # are updated. Never copy or return authentication/configuration contents.
    env['CODEX_HOME'] = str(codex_home)
    full_access = ensure_full_access_defaults(codex_home) if configure_permissions else False
    if not codex_installed(base / 'extensions'):
        if not install: return {'configured': True, 'installed': False, 'full_access': full_access}
        try:
            install_codex(base, binary, env, progress=progress)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return {'configured': True, 'installed': False, 'full_access': full_access}
    configure_shared_codex(base, env, binary)
    return {'configured': True, 'installed': True, 'full_access': full_access}
