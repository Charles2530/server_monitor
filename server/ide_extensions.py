"""Pinned editor extensions installed once in the CCI container."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request


EXTENSION_SPECS = (
    {
        'id': 'ms-python.python',
        'publisher': 'ms-python',
        'name': 'python',
        'version': '2026.4.0',
        # The Open VSX build can omit the bundled environment discovery
        # executable. Ask the Marketplace for the Linux x64 variant so the
        # bundled python-env-tools binary matches the CCI container.
        'url': 'https://marketplace.visualstudio.com/_apis/public/gallery/publishers/ms-python/vsextensions/python/2026.4.0/vspackage?targetPlatform=linux-x64',
        'sha256': '1d07e60d5eab257ea5d691b26def3fff642b4d2c04fab94424e8b8fe25baf656',
        'required_paths': ('python-env-tools/bin/pet',),
        'required_x86_64_elf_paths': ('python-env-tools/bin/pet',),
    },
    {
        'id': 'detachhead.basedpyright',
        'publisher': 'detachhead',
        'name': 'basedpyright',
        'version': '1.40.2',
        # Pylance's activation checks reject code-server. Use a language
        # server that supports it, including definitions and semantic tokens.
        'url': 'https://open-vsx.org/api/detachhead/basedpyright/1.40.2/file/detachhead.basedpyright-1.40.2.vsix',
        'sha256': '9c87b0b992714ca2f9298cb7e98bda621612f0ee96121a8e77ee7fcccce2f351',
        'required_paths': ('dist/extension.js', 'dist/server.js'),
    },
    {
        'id': 'MS-CEINTL.vscode-language-pack-zh-hans',
        'publisher': 'MS-CEINTL',
        'name': 'vscode-language-pack-zh-hans',
        'version': '1.131.0',
        'url': 'https://open-vsx.org/api/MS-CEINTL/vscode-language-pack-zh-hans/1.131.0/file/MS-CEINTL.vscode-language-pack-zh-hans-1.131.0.vsix',
        'sha256': '7ffc9dbe980f95942ed56752c1d6ca5f40855013aedd34cf40257929be4d1895',
    },
    {
        'id': 'charliermarsh.ruff',
        'publisher': 'charliermarsh',
        'name': 'ruff',
        'version': '2026.86.0',
        'url': 'https://open-vsx.org/api/charliermarsh/ruff/linux-x64/2026.86.0/file/charliermarsh.ruff-2026.86.0@linux-x64.vsix',
        'sha256': 'e732e2185521fb525adabcbc748d76687c2c80dcc8a74cdcdaaa896fbab42c77',
        'required_paths': ('dist/extension.js', 'bundled/libs/bin/ruff'),
        'required_x86_64_elf_paths': ('bundled/libs/bin/ruff',),
    },
    {
        'id': 'ms-python.debugpy',
        'publisher': 'ms-python',
        'name': 'debugpy',
        'version': '2026.6.0',
        'url': 'https://marketplace.visualstudio.com/_apis/public/gallery/publishers/ms-python/vsextensions/debugpy/2026.6.0/vspackage?targetPlatform=linux-x64',
        'sha256': '3ae417f78bce17992b9e0efba160a43a85a8b695bd387e150d4df24e366c7b01',
        'required_paths': ('dist/extension.js', 'bundled/libs/debugpy/adapter/__main__.py'),
    },
    {
        'id': 'ms-python.vscode-python-envs',
        'publisher': 'ms-python',
        'name': 'vscode-python-envs',
        'version': '1.38.0',
        'url': 'https://marketplace.visualstudio.com/_apis/public/gallery/publishers/ms-python/vsextensions/vscode-python-envs/1.38.0/vspackage?targetPlatform=linux-x64',
        'sha256': '54aa8aed887b2c2b5b4bb511d0fe44beb83e2cc047c3c2c196521083dd95a9d2',
        'required_paths': ('dist/extension.js', 'python-env-tools/bin/pet'),
        'required_x86_64_elf_paths': ('python-env-tools/bin/pet',),
    },
)
EXTENSIONS_VERSION = '8|' + '|'.join(spec['id'] + '@' + spec['version'] for spec in EXTENSION_SPECS)
UPDATE_DEFAULTS = {'extensions.autoUpdate': False, 'extensions.autoCheckUpdates': False}
ATTEMPT_COOLDOWN = 300
DOWNLOAD_ATTEMPTS = 2


def _obsolete(extensions):
    try:
        value = json.loads((extensions / '.obsolete').read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_linux_x86_64_elf(path):
    try:
        with path.open('rb') as stream:
            header = stream.read(20)
    except OSError:
        return False
    return (len(header) >= 20 and header[:4] == b'\x7fELF'
            and header[4:6] == b'\x02\x01' and header[18:20] == b'\x3e\x00')


def extension_installed(extensions, spec):
    obsolete = _obsolete(extensions)
    prefix = (spec['publisher'] + '.' + spec['name'] + '-').lower()
    for manifest in extensions.glob('*/package.json'):
        if not manifest.parent.name.lower().startswith(prefix):
            continue
        if obsolete.get(manifest.parent.name) or any(
                isinstance(key, str) and key.lower() == manifest.parent.name.lower() and value
                for key, value in obsolete.items()):
            continue
        try:
            package = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        if (str(package.get('publisher', '')).lower() == spec['publisher'].lower()
                and str(package.get('name', '')).lower() == spec['name'].lower()
                and package.get('version') == spec['version']
                and all(
                    (manifest.parent / relative).is_file()
                    for relative in spec.get('required_paths', ())
                )
                and all(
                    _is_linux_x86_64_elf(manifest.parent / relative)
                    for relative in spec.get('required_x86_64_elf_paths', ())
                )):
            return True
    return False


def _download_size(response):
    try:
        value = int(response.headers.get('Content-Length', ''))
        return value if 0 < value <= 250 * 1024 * 1024 else None
    except (AttributeError, TypeError, ValueError):
        return None


def _download_archive_once(spec, temporary, progress):
    archive = temporary / (spec['publisher'] + '.' + spec['name'] + '-' + spec['version'] + '.vsix')
    digest = hashlib.sha256()
    chunks = []
    total = 0
    started = time.monotonic()
    progress('download_' + spec['name'], 0)
    with urllib.request.urlopen(spec['url'], timeout=60) as response:
        expected = _download_size(response)
        progress('download_' + spec['name'], 0, expected)
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 250 * 1024 * 1024 or time.monotonic() - started > 300:
                raise RuntimeError('扩展下载超出限制。')
            chunks.append(chunk)
            progress('download_' + spec['name'], total, expected)
    raw = b''.join(chunks)
    if response.headers.get('Content-Encoding', '').lower() == 'gzip':
        raw = gzip.decompress(raw)
    if len(raw) > 250 * 1024 * 1024:
        raise RuntimeError('扩展解压后大小异常。')
    digest.update(raw)
    progress('verify_' + spec['name'])
    if digest.hexdigest() != spec['sha256']:
        raise RuntimeError('扩展下载校验失败。')
    archive.write_bytes(raw)
    return archive


def _download_archive(spec, temporary, progress):
    last_error = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            return _download_archive_once(spec, temporary, progress)
        except (OSError, RuntimeError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                time.sleep(0.5)
    raise last_error


def _append_install_log(base, message):
    try:
        with (base / 'extension-install.log').open('ab') as log:
            log.write((message.rstrip() + '\n').encode('utf-8', 'replace'))
    except OSError:
        pass


def install_extension(base, env, binary, spec, progress):
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix='extension-install-', dir=str(base)))
    try:
        try:
            archive = _download_archive(spec, temporary, progress)
            config = temporary / 'config.json'
            config.write_text('{}')
            progress('install_' + spec['name'])
            with (base / 'extension-install.log').open('ab') as log:
                result = subprocess.run([
                    str(binary), '--config', str(config), '--user-data-dir', str(base / 'user-data'),
                    '--extensions-dir', str(base / 'extensions'), '--install-extension', str(archive), '--force',
                ], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, timeout=180)
            if result.returncode or not extension_installed(base / 'extensions', spec):
                raise RuntimeError('扩展安装失败，请检查容器专用 extension-install.log。')
        except Exception as error:
            _append_install_log(base, '[%s] %s: %s' % (spec['id'], type(error).__name__, error))
            raise
    finally:
        shutil.rmtree(str(temporary), ignore_errors=True)


def configure_python_service(base, env, binary):
    """Migrate our code-server to basedpyright, retaining rollback copies."""
    settings = base / 'user-data/User/settings.json'
    settings.parent.mkdir(parents=True, exist_ok=True)
    original = settings.read_text() if settings.exists() else json.dumps({
        'workbench.colorTheme': 'Default Dark Modern',
        'workbench.startupEditor': 'none', 'telemetry.telemetryLevel': 'off',
        'update.mode': 'none', **UPDATE_DEFAULTS,
    })
    current = json.loads(original)  # Do not silently overwrite JSONC or broken JSON.
    if not isinstance(current, dict):
        raise ValueError('编辑器设置不是 JSON 对象。')
    updated = {**current, 'python.languageServer': 'None',
               'editor.semanticHighlighting.enabled': True}
    for key, value in {
        'python.useEnvironmentsExtension': True,
        'basedpyright.analysis.typeCheckingMode': 'off',
        'basedpyright.analysis.diagnosticMode': 'openFilesOnly',
        'basedpyright.analysis.inlayHints.variableTypes': False,
        'basedpyright.analysis.inlayHints.callArgumentNames': False,
        'basedpyright.analysis.inlayHints.functionReturnTypes': False,
        'basedpyright.analysis.inlayHints.genericTypes': False,
    }.items():
        updated.setdefault(key, value)
    if 'python.analysis.extraPaths' in current:
        updated.setdefault('basedpyright.analysis.extraPaths', current['python.analysis.extraPaths'])
    backup = settings.with_name('settings.before-basedpyright.json')
    if updated != current and not backup.exists():
        backup.write_text(original)
    obsolete = _obsolete(base / 'extensions')
    old_extensions = [p for p in (base / 'extensions').glob('ms-python.vscode-pylance-*')
                      if p.is_dir() and not obsolete.get(p.name)]
    registry = base / 'extensions/extensions.json'
    if registry.exists():
        registered = json.loads(registry.read_text())
        if not any(row.get('identifier', {}).get('id', '').lower() == 'ms-python.vscode-pylance'
                   for row in registered):
            old_extensions = []  # Uninstall can leave files used by an older window.
    if old_extensions:
        for folder in old_extensions:
            saved = base / 'extension-backups' / folder.name
            if not saved.exists():
                shutil.copytree(folder, saved)
        with (base / 'extension-install.log').open('ab') as log:
            result = subprocess.run([
                str(binary), '--user-data-dir', str(base / 'user-data'),
                '--extensions-dir', str(base / 'extensions'),
                '--uninstall-extension', 'ms-python.vscode-pylance',
            ], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, timeout=60)
        if result.returncode:
            raise RuntimeError('旧 Pylance 插件卸载失败，已保留备份。')
    if updated != current:
        temporary = settings.with_suffix('.tmp')
        temporary.write_text(json.dumps(updated, ensure_ascii=False))
        temporary.replace(settings)


def configure_python_formatter(base):
    """Enable Python save formatting with bundled Ruff, retaining rollback settings."""
    settings = base / 'user-data/User/settings.json'
    original = settings.read_text() if settings.exists() else '{}'
    current = json.loads(original)
    if not isinstance(current, dict):
        raise ValueError('编辑器设置不是 JSON 对象。')
    python = current.get('[python]', {})
    if not isinstance(python, dict):
        raise ValueError('编辑器 Python 设置不是 JSON 对象。')
    updated = {
        **current,
        'diffEditor.ignoreTrimWhitespace': False,
        'ruff.enable': True,
        'ruff.lint.enable': False,
        'ruff.importStrategy': 'useBundled',
        '[python]': {**python, 'editor.defaultFormatter': 'charliermarsh.ruff',
                     'editor.formatOnSave': True, 'editor.detectIndentation': False},
    }
    if updated == current:
        return
    settings.parent.mkdir(parents=True, exist_ok=True)
    backup = settings.with_name('settings.before-ruff.json')
    if settings.exists() and not backup.exists():
        backup.write_text(original)
    temporary = settings.with_suffix('.tmp')
    temporary.write_text(json.dumps(updated, ensure_ascii=False))
    temporary.replace(settings)


def configure_editor_performance(base):
    """Keep warm launches quiet without disabling language features or user choices."""
    settings = base / 'user-data/User/settings.json'
    original = settings.read_text() if settings.exists() else '{}'
    current = json.loads(original)
    if not isinstance(current, dict):
        raise ValueError('编辑器设置不是 JSON 对象。')
    watchers = current.get('files.watcherExclude', {})
    if not isinstance(watchers, dict):
        raise ValueError('文件监听排除设置不是 JSON 对象。')
    updated = {**current}
    for key, value in UPDATE_DEFAULTS.items():
        updated.setdefault(key, value)
    watchers = {**watchers}
    for directory in ('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'):
        watchers.setdefault('**/' + directory + '/**', True)
    updated['files.watcherExclude'] = watchers
    if updated == current:
        return
    settings.parent.mkdir(parents=True, exist_ok=True)
    backup = settings.with_name('settings.before-performance.json')
    if settings.exists() and not backup.exists():
        backup.write_text(original)
    temporary = settings.with_suffix('.tmp')
    temporary.write_text(json.dumps(updated, ensure_ascii=False))
    temporary.replace(settings)


def _write_locale(base):
    """Register installed translations and use code-server's actual locale file."""
    spec = next(s for s in EXTENSION_SPECS if s['name'] == 'vscode-language-pack-zh-hans')
    extensions = base / 'extensions'
    identifier = {'id': spec['id'].lower()}
    registry = extensions / 'extensions.json'
    if registry.exists():
        identifier = next((row['identifier'] for row in json.loads(registry.read_text())
                           if row.get('identifier', {}).get('id', '').lower() == spec['id'].lower()
                           and row.get('version') == spec['version']), None)
        if identifier is None:
            raise ValueError('简体中文插件未注册。')
    obsolete = _obsolete(extensions)
    language = None
    for manifest in extensions.glob('*/package.json'):
        if (not manifest.parent.name.lower().startswith(spec['id'].lower() + '-')
                or obsolete.get(manifest.parent.name)):
            continue
        package = json.loads(manifest.read_text())
        if (package.get('publisher', '').lower() != spec['publisher'].lower()
                or package.get('name') != spec['name'] or package.get('version') != spec['version']):
            continue
        for item in package.get('contributes', {}).get('localizations', []):
            if item.get('languageId') != 'zh-cn':
                continue
            translations = {t['id']: str((manifest.parent / t['path']).resolve())
                            for t in item['translations']}
            if 'vscode' not in translations or not all(Path(p).is_file() for p in translations.values()):
                raise ValueError('简体中文翻译文件不完整。')
            # Same cache schema and version hash as VS Code's LanguagePacksCache.
            language = {
                'hash': hashlib.md5(((identifier.get('uuid') or identifier['id']) + spec['version']).encode()).hexdigest(),
                'extensions': [{'extensionIdentifier': identifier, 'version': spec['version']}],
                'translations': translations,
                'label': item.get('localizedLanguageName') or item.get('languageName'),
            }
            break
        if language:
            break
    if language is None:
        raise ValueError('简体中文翻译配置缺失。')

    argv = base / 'user-data/User/argv.json'
    cache = base / 'user-data/languagepacks.json'
    current_argv = json.loads(argv.read_text()) if argv.exists() else {}
    current_cache = json.loads(cache.read_text()) if cache.exists() else {}
    if not isinstance(current_argv, dict) or not isinstance(current_cache, dict):
        raise ValueError('编辑器语言配置不是 JSON 对象。')
    updated_argv = {**current_argv}
    updated_argv.setdefault('locale', 'zh-cn')
    for path, current, updated in (
        (cache, current_cache, {**current_cache, 'zh-cn': language}),
        (argv, current_argv, updated_argv),
    ):
        if current == updated:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = path.with_name(path.stem + '.before-locale-fix.json')
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(updated, ensure_ascii=False))
        temporary.replace(path)
    return updated_argv['locale']


def ensure_update_defaults(settings):
    """Add performance defaults only to our plain JSON settings file."""
    settings = Path(settings)
    try:
        raw = settings.read_text()
        current = json.loads(raw)
    except (OSError, ValueError):
        return False
    if not isinstance(current, dict):
        return False
    updated = {**current}
    for key, value in UPDATE_DEFAULTS.items():
        updated.setdefault(key, value)
    if updated == current:
        return True
    temporary = settings.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(updated))
        temporary.replace(settings)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _attempt_recent(base):
    try:
        state = json.loads((base / 'extensions-state.json').read_text())
        return (state.get('version') == EXTENSIONS_VERSION
                and isinstance(state.get('attempted_at'), (int, float))
                and time.time() - state['attempted_at'] < ATTEMPT_COOLDOWN)
    except (OSError, ValueError, TypeError):
        return False


def _record_attempt(base):
    path = base / 'extensions-state.json'
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps({'version': EXTENSIONS_VERSION, 'attempted_at': time.time()}))
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def prepare_extensions(base, env, binary, progress=None, *, install=True):
    progress = progress or (lambda *args: None)
    extensions = base / 'extensions'
    result = {'version': EXTENSIONS_VERSION, 'installed': True, 'locale': 'zh-cn', 'extensions': {}}
    throttled = _attempt_recent(base) if install else False
    attempted = False
    for spec in EXTENSION_SPECS:
        installed = extension_installed(extensions, spec)
        if not installed and install and not throttled:
            attempted = True
            try:
                install_extension(base, env, binary, spec, progress)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                installed = False
            else:
                installed = extension_installed(extensions, spec)
        result['extensions'][spec['id']] = installed
        result['installed'] = result['installed'] and installed
    if install and result['installed']:
        try:
            configure_python_service(base, env, binary)
            configure_python_formatter(base)
            configure_editor_performance(base)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            _append_install_log(base, '[editor configuration] ' + str(error))
            result['installed'] = False
    if attempted and not result['installed']:
        _record_attempt(base)
    try:
        result['locale'] = _write_locale(base)
    except (OSError, ValueError) as error:
        _append_install_log(base, '[language configuration] ' + str(error))
        result['installed'] = False
    return result
