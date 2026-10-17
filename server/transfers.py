"""Remote CCI/SSH data transfer jobs backed by the installed ads-cli."""
import asyncio
import base64
import os
from pathlib import Path
import re
import secrets
import shlex
import time
import zlib

from .acp_actions import ActionError
from .cci_ide import run_python
from .ssh import stop_process
from .ssh_transfers import SSHTransfers, validate_path

SITES = ('sensecore', 'maoshanwang')
NETWORKS = ('internal', 'external')
EXECUTOR_TYPES = ('sensecore', 'maoshanwang', 'ssh-node')
BUCKET_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}')


def _text(value, label, limit=4096):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f'{label}格式无效。')
    return value


def normalize_endpoint(value):
    if not isinstance(value, dict) or set(value) - {'type', 'site', 'network', 'bucket', 'path', 'executor'}:
        raise ValueError('端点配置格式无效。')
    kind = value.get('type')
    if kind == 'folder':
        path = value.get('path')
        validate_path(path)
        result = {'type': 'folder', 'path': path}
        executor = value.get('executor')
        if executor is not None:
            if not isinstance(executor, dict) or executor.get('type') not in EXECUTOR_TYPES:
                raise ValueError('文件夹端点的节点配置无效。')
            executor_id = _text(executor.get('id'), '执行节点', 160)
            result['executor'] = {'type': executor['type'], 'id': executor_id}
        return result
    if kind != 'afs':
        raise ValueError('端点类型无效。')
    site = value.get('site')
    network = value.get('network')
    bucket = value.get('bucket', 'transfer')
    path = value.get('path')
    if site not in SITES or (network is not None and network not in NETWORKS):
        raise ValueError('请选择有效的 AFS 站点。')
    if not isinstance(bucket, str) or not BUCKET_RE.fullmatch(bucket):
        raise ValueError('bucket 名称无效。')
    path = _text(path, '存储路径', 4096).lstrip('/').rstrip('/')
    if path in ('', '.'):
        raise ValueError('存储路径不能是根目录。')
    if any(part in ('', '.', '..') for part in path.split('/')):
        raise ValueError('存储路径不能包含空目录段。')
    return {'type': 'afs', 'site': site, 'bucket': bucket,
            'path': path if path.endswith('/') else path + '/'}


def resolve_network(endpoint, executor):
    """Only a CCI in the endpoint's own site can use its internal entrance."""
    return 'internal' if executor['type'] == endpoint['site'] else 'external'


def normalize_job(value):
    if not isinstance(value, dict) or set(value) - {'executor', 'source', 'destination', 'mode'}:
        raise ValueError('传输任务格式无效。')
    executor = value.get('executor')
    source = normalize_endpoint(value.get('source'))
    destination = normalize_endpoint(value.get('destination'))
    endpoint_executors = [endpoint.get('executor') for endpoint in (source, destination) if endpoint.get('executor')]
    if executor is None and endpoint_executors:
        if any(item != endpoint_executors[0] for item in endpoint_executors[1:]) and not (source['type'] == destination['type'] == 'folder'):
            raise ValueError('源端和目标端必须使用同一个执行节点。')
        executor = endpoint_executors[0]
    if executor is not None:
        if not isinstance(executor, dict) or executor.get('type') not in EXECUTOR_TYPES:
            raise ValueError('请选择有效的执行节点。')
        executor_id = _text(executor.get('id'), '执行节点', 160)
        executor = {'type': executor['type'], 'id': executor_id}
        for endpoint in (source, destination):
            if endpoint['type'] == 'folder' and 'executor' not in endpoint:
                endpoint['executor'] = executor
            elif endpoint.get('executor') and endpoint['executor'] != executor and not (source['type'] == destination['type'] == 'folder'):
                raise ValueError('端点节点和执行节点不一致。')
    mode = value.get('mode', 'sync')
    if mode not in ('sync', 'copy'):
        raise ValueError('传输方式无效。')
    if any(endpoint['type'] == 'folder' and not endpoint.get('executor') for endpoint in (source, destination)):
        raise ValueError('节点文件夹需要选择执行节点。')
    if source == destination:
        raise ValueError('源端和目标端不能相同。')
    return {'executor': executor,
            'source': source, 'destination': destination, 'mode': mode}


def _safe_output(line):
    line = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]', '', line)
    line = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', line)
    line = re.sub(r'(?i)(access[_-]?key|secret[_-]?key|token|signature)=([^&\s]+)', r'\1=***', line)
    line = re.sub(r's3://[^\s]+@', 's3://***:***@', line)
    return line[:1000]


def _progress_from_output(line):
    match = re.search(r'(?<!\d)(\d{1,3})%(?!\d)', line)
    return None if not match else min(100, int(match.group(1)))


class TransferManager(SSHTransfers):
    _EXECUTOR_CACHE_SECONDS = 10
    _BROWSE_SESSION_IDLE_SECONDS = 90

    def __init__(self, catalog, runtimes=None, ads_script=None):
        super().__init__(catalog)
        self.runtimes = runtimes or {}
        self.ads_script = Path(ads_script or os.getenv('MONITOR_ADS_CLI_SOURCE', str(Path.home() / 'initial/tools/ads-cli.sh')))
        self.full_logs = {}
        self._executor_cache = {}
        self._browse_sessions = {}
        self._browse_locks = {}

    async def _expire_browse_session(self, key, session):
        lock = self._browse_locks.get(key)
        if lock is None:
            return
        async with lock:
            current = self._browse_sessions.get(key)
            if current is not session or time.monotonic() - session['used_at'] < self._BROWSE_SESSION_IDLE_SECONDS:
                return
            self._browse_sessions.pop(key, None)
            try:
                await session['bridge'].disconnect(session['id'], 'cci-editor-bootstrap')
            except ActionError:
                pass

    def _schedule_browse_expiry(self, key, session):
        if session.get('expiry'):
            session['expiry'].cancel()
        session['expiry'] = asyncio.get_running_loop().call_later(
            self._BROWSE_SESSION_IDLE_SECONDS,
            lambda: asyncio.create_task(self._expire_browse_session(key, session)))

    async def _aliases(self):
        hosts = await self.catalog.hosts()
        return {host['alias']: host for host in hosts}

    async def executors(self):
        hosts = await self._aliases()
        state = self.catalog.snapshot()
        verified = set(state.get('verified', []))
        records = []
        async def cci_records(site_id, runtime):
            if runtime is None:
                return []
            tasks = [task for task in (getattr(runtime.monitor, 'state', {}) or {}).get('tasks', [])
                     if task.get('kind') == 'CCI' and str(task.get('state', '')).lower() == 'running']
            async def task_records(task):
                try:
                    result = await runtime.terminal_bridge.targets(task['id'])
                except Exception:
                    return []
                rows = []
                for target in result.get('targets', []):
                    instance, container = target.get('instance'), target.get('container')
                    if not isinstance(instance, str) or not isinstance(container, str):
                        continue
                    executor_id = f"cci:{site_id}:{task['id']}:{instance}:{container}"
                    rows.append({'id': executor_id, 'type': site_id,
                                 'executor_kind': 'cci',
                                 'label': f"{task.get('name') or task['id']} · {instance} · {container}",
                                 'task_id': task['id'], 'instance': instance, 'container': container,
                                 'site': site_id, 'status': 'available',
                                 'capabilities': ['folder', 'ads-upload', 'ads-download', 'ads-sync', 'ads-copy']})
                return rows
            return [row for rows in await asyncio.gather(*(task_records(task) for task in tasks)) for row in rows]

        cci = await asyncio.gather(*(cci_records(site_id, self.runtimes.get(site_id))
                                     for site_id in ('sensecore', 'maoshanwang')))
        for rows in cci:
            records.extend(rows)
        # SSH transfer endpoints are intentionally limited to hosts that the
        # existing SSH probe has verified as reachable and authenticated.
        for alias in sorted(verified, key=str.lower):
            host = hosts.get(alias)
            if host is None:
                continue
            records.append({'id': f'ssh-node:{alias}', 'type': 'ssh-node',
                            'executor_kind': 'ssh', 'label': alias, 'alias': alias,
                            'status': 'available', 'capabilities': ['folder']})
        return {'executors': records, 'checked_at': state.get('checked_at'),
                'checking': state.get('checking', False)}

    @staticmethod
    def _parse_cci_id(executor_id):
        if not isinstance(executor_id, str):
            return None
        fields = executor_id.split(':')
        if len(fields) != 6 or fields[0] != 'cci' or fields[2] != 'CCI':
            return None
        site, name, instance, container = fields[1], fields[3], fields[4], fields[5]
        if (site not in SITES or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}', name)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}', instance)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}', container)):
            return None
        return site, f'CCI:{name}', instance, container

    async def _executor(self, executor):
        if not isinstance(executor, dict) or executor.get('type') not in EXECUTOR_TYPES:
            raise ActionError('请选择有效的执行节点。')
        executor_id = executor.get('id')
        cache_key = (executor.get('type'), executor_id)
        cached = self._executor_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self._EXECUTOR_CACHE_SECONDS:
            return cached[1]
        if executor['type'] == 'ssh-node':
            alias = executor_id.split(':', 1)[-1] if isinstance(executor_id, str) else ''
            hosts = await self._aliases()
            if alias not in set(self.catalog.snapshot().get('verified', [])) or alias not in hosts:
                raise ActionError('该 SSH 节点当前不可用，请刷新 SSH 节点列表后重试。')
            result = {'type': 'ssh-node', 'executor_kind': 'ssh', 'id': executor_id,
                      'alias': alias, 'host': hosts[alias], 'label': alias}
            self._executor_cache[cache_key] = (time.monotonic(), result)
            return result
        parsed = self._parse_cci_id(executor_id)
        if parsed is None or parsed[0] != executor['type']:
            raise ActionError('CCI 执行节点标识无效，请刷新运行任务列表后重试。')
        site, task_id, instance, container = parsed
        runtime = self.runtimes.get(site)
        if runtime is None:
            raise ActionError('该 CCI 站点当前不可用，请刷新运行任务列表后重试。')
        try:
            result = await runtime.terminal_bridge.targets(task_id)
        except Exception:
            raise ActionError('无法读取该 CCI 任务的可用容器，请刷新运行任务列表后重试。') from None
        if not any(target.get('instance') == instance and target.get('container') == container
                   for target in result.get('targets', [])):
            raise ActionError('该 CCI 容器当前不可用，请刷新运行任务列表后重试。')
        task_name = result.get('name') or task_id
        result = {'type': site, 'executor_kind': 'cci', 'id': executor_id,
                  'site': site, 'task_id': task_id, 'instance': instance, 'container': container,
                  'label': f'{task_name} · {instance} · {container}', 'runtime': runtime}
        self._executor_cache[cache_key] = (time.monotonic(), result)
        return result

    async def _job_executor(self, job):
        if job.get('executor'):
            return await self._executor(job['executor'])
        site = job['source'].get('site') if job['source']['type'] == 'afs' else job['destination'].get('site')
        available = (await self.executors())['executors']
        selected = next((item for item in available if item['type'] == site and item.get('executor_kind') == 'cci'), None)
        if selected:
            job['executor'] = {'type': site, 'id': selected['id']}
            return await self._executor(job['executor'])
        raise ActionError(f'未找到 {site} 当前可用的 CCI 运行任务。')

    async def browse(self, executor_id, path=''):
        if not isinstance(executor_id, str) or ':' not in executor_id:
            raise ActionError('执行节点标识无效。')
        kind, alias = executor_id.split(':', 1)
        if kind == 'cci':
            parsed = self._parse_cci_id(executor_id)
            if parsed is None:
                raise ActionError('CCI 执行节点标识无效。')
            executor = await self._executor({'type': parsed[0], 'id': executor_id})
        else:
            executor = await self._executor({'type': kind, 'id': executor_id})
        if executor['executor_kind'] == 'ssh':
            return await self.remote(alias, path)
        return await self._cci_browse(executor, path)

    async def _cci_browse(self, executor, path=''):
        validate_path(path, allow_home=True)
        script = """
import itertools, json, os, stat
path = os.path.realpath(os.path.expanduser(PATH or '~'))
info = os.stat(path)
if not stat.S_ISDIR(info.st_mode):
    raise ValueError('not_directory')
entries = []
truncated = False
with os.scandir(path) as items:
    for index, item in enumerate(itertools.islice(items, 2001)):
        if index >= 2000:
            truncated = True
            break
        try:
            kind = 'directory' if item.is_dir() else 'file' if item.is_file() else None
            if kind and not any(ord(char) < 32 for char in item.name):
                entries.append({'name': item.name, 'path': item.path, 'kind': kind, 'size': item.stat().st_size})
        except OSError:
            pass
entries.sort(key=lambda entry: (entry['kind'] != 'directory', entry['name'].lower()))
RESULT = {'path': path, 'parent': os.path.dirname(path), 'entries': entries[:50], 'truncated': truncated or len(entries) > 50}
""".replace('PATH', repr(path))
        key = executor['id']
        lock = self._browse_locks.setdefault(key, asyncio.Lock())
        async with lock:
            session = self._browse_sessions.get(key)
            bridge = executor['runtime'].terminal_bridge
            target = {'task_id': executor['task_id'], 'instance': executor['instance'],
                      'container': executor['container']}
            if session and time.monotonic() - session['used_at'] >= self._BROWSE_SESSION_IDLE_SECONDS:
                if session.get('expiry'):
                    session['expiry'].cancel()
                try:
                    await bridge.disconnect(session['id'], 'cci-editor-bootstrap')
                except ActionError:
                    pass
                session = self._browse_sessions.pop(key, None)
            if session is None:
                opened = await bridge.open({**target, 'cols': 240, 'rows': 40}, 'cci-editor-bootstrap')
                session = {'id': opened['session_id'], 'used_at': time.monotonic(), 'bridge': bridge, 'target': target, 'expiry': None}
                self._browse_sessions[key] = session
            try:
                result = await run_python(bridge, target, script, timeout=45, session_id=session['id'])
                session['used_at'] = time.monotonic()
                self._schedule_browse_expiry(key, session)
            except Exception:
                self._browse_sessions.pop(key, None)
                if session.get('expiry'):
                    session['expiry'].cancel()
                try:
                    await bridge.disconnect(session['id'], 'cci-editor-bootstrap')
                except ActionError:
                    pass
                raise
        if not isinstance(result, dict) or result.get('error'):
            raise ActionError('无法读取 CCI 目录，请检查路径和容器权限。')
        return result

    async def close(self):
        for key, session in list(self._browse_sessions.items()):
            if session.get('expiry'):
                session['expiry'].cancel()
            try:
                await session['bridge'].disconnect(session['id'], 'cci-editor-bootstrap')
            except ActionError:
                pass
            self._browse_sessions.pop(key, None)
        await super().close()
        self.full_logs.clear()

    def _prepare_command(self):
        """Ship the user's helper through the selected transport; reuse its installer."""
        try:
            payload = base64.b64encode(zlib.compress(self.ads_script.read_bytes())).decode()
        except OSError:
            raise ActionError('本机未找到 ads-cli.sh，请配置 MONITOR_ADS_CLI_SOURCE 指向工具脚本。') from None
        install = 'set -e; . "$1"; printf "已加载脚本：%s\\n" "$1"; '
        install += 'if [ ! -x "${ADS_CLI_HOME}/ads-cli" ] && ! command -v ads-cli >/dev/null 2>&1; then '
        install += 'printf "未检测到 ads-cli，开始安装\\n"; bash "$1"; else printf "检测到 ads-cli，复用现有安装\\n"; fi; '
        install += 'for name in ads-upload ads-download ads-copy ads-sync; do command -v "$name" >/dev/null; done; '
        install += '[ -x "${ADS_CLI_HOME}/ads-cli" ] || command -v ads-cli >/dev/null; printf "传输工具已就绪\\n"'
        script = f'''
import base64, fcntl, hashlib, os, subprocess, sys, tempfile, zlib
from pathlib import Path
target = Path(sys.argv[1]).expanduser().resolve()
content = zlib.decompress(base64.b64decode({payload!r}))
target.parent.mkdir(parents=True, exist_ok=True)
with os.fdopen(os.open(str(target) + '.monitor-lock', os.O_CREAT | os.O_RDWR, 0o600), 'w') as lock:
    print('检查远端工具，等待安装锁', flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    previous = target.read_bytes() if target.exists() else None
    if previous != content:
        if previous is not None:
            backup = target.with_name(target.name + '.monitor-backup-' + hashlib.sha256(previous).hexdigest()[:12])
            if not backup.exists():
                with backup.open('xb') as saved:
                    os.chmod(backup, 0o600)
                    saved.write(previous)
            print('旧脚本已备份：' + str(backup), flush=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary.write(content)
        os.replace(temporary.name, target)
        print('脚本已同步：' + str(target), flush=True)
    else:
        print('脚本版本一致，无需更新', flush=True)
    sys.exit(subprocess.call(['bash', '-lc', {install!r}, 'monitor-ads-prepare', sys.argv[1]]))
'''
        inner = 'set -e; script_path="${MONITOR_ADS_CLI_SCRIPT:-$HOME/initial/tools/ads-cli.sh}"; '
        inner += shlex.join(['python3', '-c', script]) + ' "$script_path"'
        return shlex.join(['bash', '-lc', inner])

    def _command(self, executor, job):
        source, destination = job['source'], job['destination']
        cross_site = source.get('site') != destination.get('site') and source['type'] == destination['type'] == 'afs'
        def network(endpoint):
            return 'external' if cross_site else resolve_network(endpoint, executor)
        if source['type'] == 'folder' and destination['type'] == 'afs':
            name = 'ads-upload'
            args = [name, '--site', destination['site'], '--network', network(destination),
                    '--', source['path'], destination['path']]
        elif source['type'] == 'afs' and destination['type'] == 'folder':
            name = 'ads-download'
            args = [name, '--site', source['site'], '--network', network(source),
                    '--', source['path'], destination['path']]
        elif source['type'] == destination['type'] == 'afs':
            if source['bucket'] != destination['bucket']:
                raise ActionError('跨站同步当前要求源端和目标端使用同一个 bucket。')
            name = 'ads-copy' if job['mode'] == 'copy' else 'ads-sync'
            args = [name, '--source-site', source['site'], '--source-network', network(source),
                    '--dest-site', destination['site'], '--dest-network', network(destination),
                    '--bucket', source['bucket'], '--', source['path'], destination['path']]
        else:
            raise ActionError('当前组合暂不支持，请选择 CCI/SSH 文件夹与 AFS/AOSS 存储路径。')
        # ads-cli.sh can be sourced from the standard initial checkout or from PATH.
        inner = 'script_path="${MONITOR_ADS_CLI_SCRIPT:-$HOME/initial/tools/ads-cli.sh}"; '
        inner += 'if [ -f "$script_path" ]; then . "$script_path"; fi; '
        inner += 'command -v ' + name + ' >/dev/null 2>&1 || { echo ADS_CLI_NOT_FOUND >&2; exit 127; }; '
        def describe(endpoint):
            if endpoint['type'] == 'folder':
                return '节点文件夹 ' + endpoint['path']
            return f"{endpoint['site']} / {endpoint['bucket']}/{endpoint['path']} · {network(endpoint)}"
        inner += shlex.join(['printf', '%s\n', '来源：' + describe(source),
                             '目标：' + describe(destination), '命令：' + name]) + '; '
        if source['type'] == 'folder' or destination['type'] == 'folder':
            bucket = destination.get('bucket') if destination['type'] == 'afs' else source.get('bucket')
            # These helpers are shell functions; env can only launch executables.
            inner += f'ADS_BUCKET={shlex.quote(bucket)} '
        inner += shlex.join(args)
        return shlex.join(['bash', '-lc', inner])

    def _staging_site(self, source, destination):
        for endpoint in (source, destination):
            if endpoint['type'] == 'afs':
                return endpoint['site']
            if endpoint['type'] == 'folder':
                executor = endpoint.get('executor') or {}
                if executor.get('type') in SITES:
                    return executor['type']
        return os.getenv('MONITOR_TRANSFER_DEFAULT_SITE', 'sensecore')

    def _commands(self, job):
        """Plan stages independently of SSH/CCI transport and UI choices."""
        source, destination = job['source'], job['destination']
        stage_path = f'.monitor-transfer/{job["id"]}/'
        def storage(endpoint, peer):
            if endpoint['type'] == 'afs':
                return endpoint
            site = self._staging_site(endpoint, peer)
            if site not in SITES:
                raise ActionError('中转存储站点配置无效。')
            if peer['type'] == 'afs' and peer['site'] == site:
                return peer
            return {'type': 'afs', 'site': site, 'bucket': peer.get('bucket', 'transfer'), 'path': stage_path}
        source_store, destination_store = storage(source, destination), storage(destination, source)
        commands = []
        def append(stage, executor, start, end, mode='sync'):
            commands.append((stage, executor, self._command(executor, {'source': start, 'destination': end, 'mode': mode})))
        if source['type'] == 'folder':
            append('upload', source['executor'], source, source_store)
        if source_store != destination_store:
            mode = job['mode'] if source['type'] == destination['type'] == 'afs' else 'copy'
            append(mode, job['executor'], source_store, destination_store, mode)
        if destination['type'] == 'folder':
            append('download', destination['executor'], destination_store, destination)
        return commands

    async def start(self, value):
        try:
            job_config = normalize_job(value)
        except ValueError as error:
            raise ActionError(str(error)) from None
        if job_config['source']['type'] == job_config['destination']['type'] == 'folder':
            source_executor = await self._executor(job_config['source'].get('executor'))
            await self._executor(job_config['destination'].get('executor'))
            executor = source_executor
        else:
            executor = await self._job_executor(job_config)
        async with self.lock:
            if self.closed:
                raise ActionError('服务正在关闭，请稍后重试。')
            active = [j for key, j in self.jobs.items() if key in self.tasks and not self.tasks[key].done()]
            if len(active) >= 3:
                raise ActionError('最多同时传输 3 项，请等待当前传输完成。')
            for key in list(self.jobs):
                if len(self.jobs) < 100:
                    break
                if self.tasks[key].done():
                    del self.jobs[key]
                    del self.tasks[key]
                    self.full_logs.pop(key, None)
            key = secrets.token_urlsafe(18)
            public_executor = {'type': executor['type'], 'id': executor['id'], 'label': executor['label']}
            job = {'id': key, **job_config, 'executor': public_executor,
                   'state': 'running', 'started_at': time.time(), 'finished_at': None,
                   'error': None, 'last_output': [], 'stage': 'starting', 'progress_percent': 0,
                   'current_file': None, 'speed_bps': None, 'eta_seconds': None}
            self.full_logs[key] = []
            commands = self._commands(job)
            self.jobs[key] = job
            self.tasks[key] = asyncio.create_task(self._run(job, commands))
            return self.get(key)

    async def _run(self, job, commands):
        stage, index, last_progress = 'prepare', 0, False
        failed_count, stage_error, forbidden = None, None, False
        labels = {'prepare': '准备工具', 'upload': '上传', 'copy': '复制', 'sync': '同步',
                  'download': '下载', 'completed': '完成'}
        def record(line):
            nonlocal last_progress, failed_count, stage_error, forbidden
            text = _safe_output(line.strip())
            if not text:
                return
            progress = _progress_from_output(text)
            bar = re.match(r'^\[[+=>.#\- ]*\]\s+.*?(\d{1,3})%\s*(.*)$', text)
            if bar:
                text = f'进度 {bar[1]}% · {bar[2]}'
            else:
                text = re.sub(r'^\d{4}/\d{2}/\d{2} [\d:.]+ ads-cli\[\d+\] <(\w+)>:\s*', r'\1 · ', text)
                if text.startswith(('ERROR · ', 'FATAL · ')):
                    stage_error = text
                    failed_count = None
                if re.match(r'^status code:\s*403\b', text):
                    forbidden = True
                if text.startswith('INFO · Found:'):
                    failures = re.search(r'\bfailed:\s*(\d+)\b', text)
                    if failures:
                        failed_count = int(failures[1])
                    names = {'Found': '发现', 'copied': '已复制', 'copiedSize': '复制大小', 'checked': '检查大小',
                             'deleted': '已删除', 'Skipped': '跳过', 'failed': '失败'}
                    text = '统计 · ' + ' · '.join(f'{names.get(key, key)} {value.strip()}'
                        for key, value in re.findall(r'(\w+):\s*([^,]+)', text))
            label = labels[stage]
            if index:
                label = f'{index}/{len(commands)} {label}'
            entry = f'{time.strftime("%H:%M:%S")} [{label}] {text}'
            full_log = self.full_logs.setdefault(job['id'], [])
            if bar and last_progress:
                job['last_output'][-1] = entry
                full_log[-1] = entry
            else:
                job['last_output'].append(entry)
                full_log.append(entry)
                if len(job['last_output']) > 2000:
                    del job['last_output'][0]
                    job['log_offset'] = job.get('log_offset', 0) + 1
            last_progress = bool(bar)
            if stage != 'prepare' and bar and progress is not None:
                job['progress_percent'] = max(job['progress_percent'], min(99, round(((index - 1) * 100 + progress) / len(commands))))
        try:
            job['stage_count'] = len(commands)
            job['stage'] = stage
            record(f'任务 {job["id"]} · 共 {len(commands)} 个传输阶段')
            # Prepare each node once, before uploading any data. Transports only
            # execute commands; script installation and routing stay here.
            prepare = self._prepare_command()
            executors = {}
            for _, selector, _ in commands:
                if selector['id'] in executors:
                    continue
                executor = await self._executor(selector)
                runner = self._run_cci if executor['executor_kind'] == 'cci' else self._run_ssh
                record(f'开始检查 · 节点 {executor["label"]}（{executor["type"]}）')
                code = await runner(executor, prepare, record)
                if code:
                    raise ActionError(f'传输工具准备失败（退出码 {code}），请查看日志中的安装结果。')
                executors[selector['id']] = executor
            for index, (stage, executor_selector, script) in enumerate(commands, 1):
                failed_count, stage_error, forbidden = None, None, False
                job.update(stage=stage, stage_index=index)
                executor = executors[executor_selector['id']]
                job['stage'] = f'{stage}:transferring'
                started = time.monotonic()
                record(f'开始 · 节点 {executor["label"]}（{executor["type"]}）')
                runner = self._run_cci if executor['executor_kind'] == 'cci' else self._run_ssh
                code = await runner(executor, script, record)
                if failed_count or (failed_count is None and stage_error):
                    detail = f'{failed_count} 个对象传输失败' if failed_count else '工具报告传输错误，未收到成功统计'
                    cause = '；存储访问被拒绝（HTTP 403），请检查凭据、对象读取权限和访问入口' if forbidden else ''
                    raise ActionError(f'{labels[stage]}阶段失败：{detail}{cause}（工具退出码 {code}）。')
                if code:
                    if code == 127 and any('ADS_CLI_NOT_FOUND' in line for line in job['last_output']):
                        raise ActionError('执行节点缺少所需 ads-cli 函数，请同步最新版 initial/tools/ads-cli.sh 或配置 MONITOR_ADS_CLI_SCRIPT。')
                    label = {'upload': '上传', 'copy': '复制', 'sync': '同步', 'download': '下载'}[stage]
                    raise ActionError(f'{label}阶段失败（退出码 {code}），请查看日志并检查节点网络、路径权限和存储配置。')
                job['progress_percent'] = round(index * 100 / len(commands))
                record(f'阶段完成 · 耗时 {time.monotonic() - started:.1f} 秒 · 退出码 0')
            job.update(state='completed', stage='completed', progress_percent=100)
            stage, index = 'completed', 0
            record(f'传输完成 · 总耗时 {time.time() - job["started_at"]:.1f} 秒')
        except asyncio.CancelledError:
            job.update(state='cancelled', stage='cancelled')
            record('传输已取消，已请求停止远端命令。')
            raise
        except Exception as error:
            job['state'] = 'failed'
            job['error'] = _safe_output(str(error)) if isinstance(error, ActionError) else '传输连接或执行异常，请检查节点状态后重试。'
            record(f'失败 · {job["error"]}（{type(error).__name__}）')
        finally:
            job['finished_at'] = time.time()

    async def _run_ssh(self, executor, script, on_output):
        process = None
        try:
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                '/usr/bin/ssh', '-T', *self.catalog.options(), executor['alias'], script,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True))
            try:
                process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                process = await spawn
                raise
            pending = b''
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                lines = re.split(rb'[\r\n]', pending + chunk)
                pending = lines.pop()
                for line in lines:
                    on_output(line.decode('utf-8', 'replace'))
                if len(pending) > 65536:
                    raise ActionError('传输输出单行过长。')
            if pending:
                on_output(pending.decode('utf-8', 'replace'))
            return await process.wait()
        finally:
            if process is not None:
                await stop_process(process)

    async def _run_cci(self, executor, script, on_output):
        # Use the existing terminal transport, without the editor bootstrap's
        # timeout or its buffered subprocess wrapper. Ctrl-C reaches ads-cli.
        bridge = executor['runtime'].terminal_bridge
        owner = 'data-transfer'
        opened = await bridge.open({key: executor[key] for key in ('task_id', 'instance', 'container')}
                                   | {'cols': 240, 'rows': 40}, owner)
        key = opened['session_id']
        tag = secrets.token_hex(16)
        encoded = base64.b64encode(zlib.compress(script.encode())).decode()
        command = f"{{ stty -echo; printf '\\036{tag}:start\\037\\n'; python3 - <<'MONITOR_TRANSFER_PY'\n"
        command += "import base64, os, zlib\nos.execl('/bin/bash', 'bash', '-lc', zlib.decompress(base64.b64decode(\n"
        command += '\n'.join(repr(encoded[i:i+1000]) for i in range(0, len(encoded), 1000))
        command += ")).decode())\nMONITOR_TRANSFER_PY\n"
        command += f"printf '\\036{tag}:%s\\037\\n' \"$?\";\n}}\n"
        sequence = cursor = 0
        started = complete = False
        pending = b''
        marker = b'\x1e' + tag.encode() + b':'
        try:
            for start in range(0, len(command), 1800):
                sequence += 1
                await bridge.write(key, sequence, command[start:start+1800], None, owner)
            while True:
                result = await bridge.read(key, cursor, owner)
                cursor = result['cursor']
                pending += b''.join(base64.b64decode(chunk) for chunk in result['chunks'])
                if not started:
                    beginning = marker + b'start\x1f'
                    if beginning not in pending:
                        if result['closed']:
                            raise ActionError('CCI 传输连接中断，请检查节点状态。')
                        pending = pending[-len(beginning):]
                        continue
                    pending = pending.split(beginning, 1)[1]
                    started = True
                if marker in pending and b'\x1f' in pending.split(marker, 1)[1]:
                    output, status = pending.split(marker, 1)
                    for line in output.splitlines():
                        on_output(line.decode('utf-8', 'replace'))
                    code = int(status.split(b'\x1f', 1)[0])
                    complete = True
                    return code
                lines = re.split(rb'[\r\n]', pending)
                pending = lines.pop()
                for line in lines:
                    if line:
                        on_output(line.decode('utf-8', 'replace'))
                if result['closed']:
                    raise ActionError('CCI 传输连接中断，请检查节点状态。')
                if len(pending) > 65536:
                    raise ActionError('传输输出单行过长。')
        finally:
            try:
                if not complete:
                    await bridge.write(key, sequence + 1, '\x03', None, owner)
            finally:
                await bridge.disconnect(key, owner)

    def listing(self):
        jobs = sorted(self.jobs.values(), key=lambda item: item['started_at'], reverse=True)
        return {'jobs': [{**job, 'last_output': job['last_output'][-20:]} for job in jobs[:50]]}

    def logs(self, key, offset=0, full=False):
        job = self.get(key)
        lines = self.full_logs.get(key, job['last_output']) if full else job['last_output']
        first = 0 if full else job.get('log_offset', 0)
        total = len(lines) if full else first + len(lines)
        # Re-send the last line because a live progress row may have changed.
        start = max(first, min(offset, total) - 1)
        return {'first': first, 'start': start, 'next': total,
                'lines': lines[start - first:]}


def register_transfer_routes(app, manager, perform, action_body):
    from fastapi import Request

    @app.get('/api/transfers/executors')
    async def executors():
        return await perform(manager.executors)

    @app.post('/api/transfers/browse')
    async def browse(request: Request):
        async def run():
            data = await action_body(request, ('executor_id', 'path'))
            return await manager.browse(data['executor_id'], data['path'])
        return await perform(run)

    @app.post('/api/transfers')
    async def start(request: Request):
        async def run():
            data = await action_body(request, ('source', 'destination'), ('executor', 'mode'))
            return await manager.start(data)
        return await perform(run)

    @app.get('/api/transfers')
    async def jobs():
        return manager.listing()

    @app.get('/api/transfers/{key}/logs')
    async def logs(key: str, offset: int = 0, full: bool = False):
        async def read():
            return manager.logs(key, offset, full)
        return await perform(read)

    @app.post('/api/transfers/{key}/cancel')
    async def cancel(key: str):
        return await perform(lambda: manager.cancel(key))
