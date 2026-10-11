"""Prepare a CCI-local editor and keep its authenticated transport on this Mac."""
import asyncio
import base64
from contextlib import asynccontextmanager
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
import time

import httpx
from .acp_actions import ActionError
from .auth import AuthRequired
from .ide_bootstrap import bootstrap_script
from .ide_appearance import APPEARANCE_VERSION
from .ide_proxy import EditorProxy
from .ide_origins import EditorOriginRegistry
from .ide_history import MAX_HISTORY_RESPONSE_BYTES, _normal_path, history_script, sanitize_history_result
from .sites import MAOSHANWANG
from .cci_terminal import VerifiedCCITargets
from .cloud_forward import CloudForward, forward_alive


def private_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
        if not any(address in ipaddress.IPv4Network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')): raise ValueError()
        return str(address)
    except (ValueError, TypeError):
        raise ActionError('CCI 实例未返回有效内网地址。') from None


PROGRESS_MESSAGES = {
    'waiting_lock': '正在等待容器内的安装检查…',
    'checking': '正在检查容器内已有编辑器…',
    'download_editor': '首次安装：正在下载 VS Code 编辑器…',
    'verify_editor': '正在校验编辑器安装包…',
    'extract_editor': '正在解压编辑器…',
    'download_codex': '正在下载 Codex 插件…',
    'verify_codex': '正在校验 Codex 插件…',
    'install_codex': '正在安装 Codex 插件…',
    'install_appearance': '正在准备代码字体与文件图标…',
    'start_editor': '正在启动容器内编辑器…',
    'connect': '正在建立编辑器连接…',
    'authenticate': '正在完成编辑器认证…',
    'reuse': '正在复用已有编辑器…',
    'ssh_retry': 'SSH 预连接失败，正在重试 SSH。',
    'ssh_failed': 'SSH 连接失败，正在切换云端通道。',
    'ssh_connected': '已通过 SSH 连接。',
    'cloud_connected': '已通过云端通道连接。',
}
TRANSPORT_STAGES = ('ssh_retry', 'ssh_failed', 'ssh_connected', 'cloud_connected')


def safe_progress(value):
    if not isinstance(value, dict) or not isinstance(value.get('stage'), str) or value['stage'] not in PROGRESS_MESSAGES: return None
    result = {'stage': value['stage']}
    if value['stage'].startswith('download_'):
        received, total = value.get('downloaded_bytes'), value.get('total_bytes')
        if type(received) is not int or not 0 <= received <= 250 * 1024 * 1024: return None
        if type(total) is not int or not received <= total <= 250 * 1024 * 1024 or total == 0: total = None
        result.update(downloaded_bytes=received, total_bytes=total)
    return result


async def run_python(bridge, data, script, *, timeout=600, on_progress=None, verified_targets=None, cleanup_jobs=None, max_result_bytes=16384, session_id=None):
    if type(max_result_bytes) is not int or not 1 <= max_result_bytes <= MAX_HISTORY_RESPONSE_BYTES:
        raise ValueError("invalid result byte limit")
    owner = 'cci-editor-bootstrap'
    verified = {'verified_targets': verified_targets} if verified_targets is not None else {}
    sequence = cursor_start = 0
    if session_id is None:
        opened = await bridge.open({**data, 'cols': 240, 'rows': 40}, owner, **verified)
        key = opened['session_id']
    else:
        # The caller serializes use of its dedicated control shell.
        shell = bridge.get(session_id, owner)
        if shell.ended or shell.target != data: raise ActionError('后台控制连接已失效。')
        key, sequence, cursor_start = session_id, shell.sequence, shell.cursor
    tag = secrets.token_hex(16)
    reporter = "def _ilab_report_progress(stage, downloaded_bytes=None, total_bytes=None):\n    print(chr(30)+" + repr(tag) + "+json.dumps({'__ilab_progress__': {'stage': stage, 'downloaded_bytes': downloaded_bytes, 'total_bytes': total_bytes}})+chr(31), flush=True)\n"
    payload = 'import json\n' + reporter + 'try:\n' + '\n'.join('    ' + line for line in script.splitlines()) + '\nexcept Exception as exc:\n    RESULT = {"error": str(exc)}\nprint(chr(30)+' + repr(tag) + '+json.dumps(RESULT)+chr(31), flush=True)\n'
    encoded = base64.b64encode(payload.encode()).decode()
    # Heredoc lines remain under canonical PTY input limits. Only our own shell is changed.
    command = "stty -echo; python3 - <<'ILAB_EDITOR_PY'\nimport base64\nexec(base64.b64decode(\n" + '\n'.join(repr(encoded[i:i+1000]) for i in range(0, len(encoded), 1000)) + "))\nILAB_EDITOR_PY\nstty echo\n"
    async def execute():
        for index, start in enumerate(range(0, len(command), 1800), 1):
            await bridge.write(key, sequence + index, command[start:start+1800], None, owner)
        raw = b''; cursor = cursor_start
        marker = b'\x1e' + tag.encode()
        while True:
            result = await bridge.read(key, cursor, owner)
            cursor = result['cursor']
            for chunk in result['chunks']:
                # Reject before decoding or appending an unbounded terminal chunk.
                if len(chunk) > 4 * ((1024 * 1024 + 2) // 3): raise ActionError('编辑器准备输出过长。')
                decoded = base64.b64decode(chunk)
                if len(raw) + len(decoded) > 1024 * 1024: raise ActionError('编辑器准备输出过长。')
                raw += decoded
            while marker in raw and b'\x1f' in raw.split(marker, 1)[1]:
                message, raw = raw.split(marker, 1)[1].split(b'\x1f', 1)
                if len(message) > max_result_bytes: raise ActionError('编辑器返回异常。')
                value = json.loads(message)
                if isinstance(value, dict) and '__ilab_progress__' in value:
                    if len(message) > 16384: raise ActionError('编辑器返回异常。')
                    progress = safe_progress(value['__ilab_progress__'])
                    if progress is not None and on_progress: on_progress(progress)
                else: return value
            if marker in raw and len(raw.split(marker, 1)[1]) > max_result_bytes:
                raise ActionError('编辑器返回异常。')
            if result['closed']: raise ActionError('编辑器准备时容器连接中断。')
            if len(raw) > 1024 * 1024: raise ActionError('编辑器准备输出过长。')
    completed = False
    async def cleanup():
        try: await bridge.disconnect(key, owner)
        except ActionError: pass
    try:
        result = await asyncio.wait_for(execute(), timeout)
        completed = True
        return result
    finally:
        if completed and session_id is not None:
            pass  # Keep the dedicated shell until its owning terminal closes.
        elif completed and cleanup_jobs is not None:
            # The result is complete; closing this temporary shell can overlap
            # SSH setup. Its owner still joins cleanup during shutdown.
            job = asyncio.create_task(cleanup()); cleanup_jobs.add(job)
            def finished(task):
                cleanup_jobs.discard(task)
                if not task.cancelled(): task.exception()
            job.add_done_callback(finished)
        else:
            await cleanup()


class SSHForward:
    def __init__(self, alias, address, port):
        self.alias, self.address, self.remote_port = alias, address, port
        self.port = None
        self.process = None
        self.control_directory = None
        self.control_request = None

    def options(self):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', self.alias):
            raise ActionError('编辑器 SSH 别名配置无效。')
        known = Path.home() / '.ssh/known_hosts'
        if not known.is_file(): raise ActionError('请先在此电脑配置并验证编辑器 SSH 跳板连接。')
        return ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(known), '-o', 'ForkAfterAuthentication=no', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3']

    async def warm(self):
        options = self.options()
        # Keep the Unix socket short enough for macOS, private to this process's
        # user, and unique to this session. No credentials are written here.
        self.control_directory = tempfile.TemporaryDirectory(prefix='ilab-ide-', dir='/tmp')
        control = Path(self.control_directory.name) / 'ssh'
        try:
            self.process = await asyncio.create_subprocess_exec('/usr/bin/ssh', '-N', '-T', '-M', '-S', str(control), '-o', 'ControlPersist=no', '-o', 'ClearAllForwardings=yes', *options, self.alias, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            for _ in range(150):
                if self.process.returncode is not None: break
                if control.is_socket(): return
                await asyncio.sleep(.1)
            raise ActionError('SSH 跳板预连接失败。')
        except BaseException:
            await self.close()
            raise

    async def start(self):
        options = self.options()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        forwarding = '127.0.0.1:%d:%s:%d' % (self.port, self.address, self.remote_port)
        if self.control_directory:
            if self.process.returncode is not None: raise ActionError('SSH 跳板预连接已退出。')
            control = str(Path(self.control_directory.name) / 'ssh')
            try:
                # -O only talks to our owned master. Ignore configured forwards
                # here, including ClearAllForwardings from the warmup command.
                self.control_request = await asyncio.create_subprocess_exec('/usr/bin/ssh', '-F', '/dev/null', '-S', control, '-O', 'forward', '-o', 'ExitOnForwardFailure=yes', '-L', forwarding, self.alias, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                if await asyncio.wait_for(self.control_request.wait(), 10): raise ActionError('SSH 编辑器转发失败。')
            finally:
                await self.stop_process(self.control_request)
                self.control_request = None
        else:
            self.process = await asyncio.create_subprocess_exec('/usr/bin/ssh', '-N', '-T', *options, '-L', forwarding, self.alias, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        for _ in range(150):
            if self.process.returncode is not None: break
            try:
                reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
                writer.close(); await writer.wait_closed()
                return
            except OSError: await asyncio.sleep(.1)
        await self.close()
        raise ActionError('SSH 跳板连接失败，请检查本机 SSH 别名、密钥及 known_hosts。')

    @staticmethod
    async def stop_process(process):
        if process and process.returncode is None:
            try: process.terminate()
            except ProcessLookupError: pass
            try: await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                try: process.kill()
                except ProcessLookupError: pass
                await process.wait()

    async def close(self):
        await self.stop_process(self.control_request)
        await self.stop_process(self.process)
        if self.control_directory:
            self.control_directory.cleanup()
            self.control_directory = None


class CCIEditor:
    def __init__(self, site, bridge, resolver, *, origin_registry=None):
        self.site, self.bridge, self.resolver = site, bridge, resolver
        self.origin_registry = origin_registry if origin_registry is not None else EditorOriginRegistry(MAOSHANWANG.data / 'ide-origins.json')
        self.requests = {}
        self.sessions = {}
        self.optional_jobs = {}
        self.cleanup_jobs = set()
        self.target_leases = {}
        self.locks = {}
        self.preparing = set()
        self.reaper = None

    @asynccontextmanager
    async def session_lock(self, identity):
        # Retain an entry while holders or waiters exist; unrelated containers
        # must not wait behind a first installation or SSH repair.
        entry = self.locks.setdefault(identity, {'lock': asyncio.Lock(), 'users': 0})
        entry['users'] += 1
        try:
            async with entry['lock']:
                yield
        finally:
            entry['users'] -= 1
            if not entry['users']: self.locks.pop(identity, None)

    @asynccontextmanager
    async def preparation_slot(self, identity):
        reserved = identity not in self.sessions
        if reserved:
            if len(self.sessions) + len(self.preparing) >= 4:
                raise ActionError('最多同时打开 4 个编辑器，请关闭闲置页面并稍后重试。', 'ide_limit', 429)
            self.preparing.add(identity)
        try:
            yield
        finally:
            if reserved: self.preparing.discard(identity)

    async def begin(self, action, data, origin):
        if action not in ('targets', 'open', 'recover'): raise ActionError('编辑器请求无效。')
        if not isinstance(data.get('task_id'), str) or not re.fullmatch(r'CCI:[A-Za-z0-9][A-Za-z0-9_-]{0,99}', data['task_id']):
            raise ActionError('仅本人运行中的 CCI 支持站内 VS Code。')
        if action in ('open', 'recover'):
            for field in ('instance', 'container'):
                if not isinstance(data.get(field), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}', data[field]): raise ActionError('容器选择无效。')
        now = time.monotonic()
        self.requests = {key: item for key, item in self.requests.items() if not item['task'].done() or now - item['created'] < 900}
        # Retrying a pending preparation reuses work, including installation.
        for key, item in self.requests.items():
            if item['action'] == action and item['data'] == data and item['origin'] == origin and not item['task'].done(): return {'request_id': key}
        if sum(not item['task'].done() for item in self.requests.values()) >= 4: raise ActionError('编辑器准备请求过多，请稍后重试。', 'ide_limit', 429)
        while len(self.requests) >= 64:
            key = next((k for k, v in self.requests.items() if v['task'].done()), None)
            if key is None: break
            del self.requests[key]
        progress_state, connection_events = {}, []
        def progress(value):
            safe = safe_progress(value)
            if safe is not None:
                progress_state.clear(); progress_state.update(safe)
                if safe['stage'] in TRANSPORT_STAGES:
                    connection_events.append(safe['stage']); del connection_events[:-16]
        async def prepare():
            try:
                operation = self.targets(data['task_id']) if action == 'targets' else (self.recover(data, origin, progress=progress) if action == 'recover' else self.open(data, origin, progress=progress))
                result = await asyncio.wait_for(operation, 90 if action == 'recover' else 720)
                return {'pending': False, 'result': result}
            except ActionError as exc: return {'pending': False, 'error': str(exc)}
            except Exception:
                message = '编辑器连接暂不可用；已保留当前页面，请检查本机网络和 SSH 跳板后重试。' if action == 'recover' else '编辑器准备失败，请检查容器网络、Python 3 和本机 SSH 配置后重试。'
                return {'pending': False, 'error': message}
        key = secrets.token_urlsafe(32)
        self.requests[key] = {'task': asyncio.create_task(prepare()), 'created': now, 'origin': origin, 'action': action, 'data': dict(data), 'progress': progress_state, 'connection_events': connection_events}
        if self.reaper is None: self.reaper = asyncio.create_task(self.reap())
        return {'request_id': key}

    async def status(self, key, origin):
        item = self.requests.get(key) if isinstance(key, str) else None
        if not item or item['origin'] != origin: raise ActionError('编辑器请求已过期，请重试。', 'ide_expired', 404)
        # Bound polling without cancelling preparation shared by other tabs.
        if not item['task'].done(): await asyncio.wait([item['task']], timeout=1)
        events = {'connection_events': list(item['connection_events'])} if item.get('connection_events') else {}
        if not item['task'].done():
            message = '正在读取最新实例与容器…' if item['action'] == 'targets' else '正在检查已有编辑器并建立连接，请稍候…'
            progress = item.get('progress')
            if progress: return {'pending': True, 'message': PROGRESS_MESSAGES[progress['stage']], 'progress': dict(progress), **events}
            return {'pending': True, 'message': message, **events}
        result = {**item['task'].result(), **events}
        if item['action'] == 'open' and isinstance(result.get('result'), dict) and 'port' in result['result']:
            data = item['data']
            session = self.sessions.get((data['task_id'], data['instance'], data['container'], origin))
            if not session or session['proxy'].port != result['result']['port']:
                raise ActionError('编辑器连接已变化，请重新打开。', 'ide_expired', 404)
            # Preparation can be shared by tabs or completed while a tab sleeps.
            # Issue the one-use credential when each caller receives its result.
            return {**result, 'result': {**result['result'], 'ticket': session['proxy'].ticket()}}
        return result

    async def targets(self, task_id):
        if not task_id.startswith('CCI:'): raise ActionError('仅 CCI 支持站内 VS Code。')
        result = await self.verified_targets(task_id)
        return {**result, 'targets': [{k: v for k, v in target.items() if k != 'address'} for target in result['targets']]}

    async def verified_targets(self, task_id):
        lease = self.target_leases.get(task_id)
        result = lease.get(self.site, task_id) if lease else None
        if result is not None: return result
        started = time.monotonic()
        try: result = await self.resolver.targets(task_id, include_address=True)
        except AuthRequired:
            await self.bridge.renew_once(started)
            result = await self.resolver.targets(task_id, include_address=True)
        self.target_leases = {key: value for key, value in self.target_leases.items() if value.get(self.site, key) is not None}
        if len(self.target_leases) >= 32: self.target_leases.pop(next(iter(self.target_leases)))
        self.target_leases[task_id] = VerifiedCCITargets(self.site, result)
        return result

    async def resolve(self, data):
        result = await self.verified_targets(data['task_id'])
        target = next((t for t in result['targets'] if t['instance'] == data['instance'] and t['container'] == data['container']), None)
        if not target: raise ActionError('所选 CCI 容器已不可用，请刷新任务。')
        return result, private_ipv4(target['address'])

    async def healthy(self, session, *, retry=True):
        if not forward_alive(session['forward']): return False
        if await session['proxy'].healthy(): return True
        if not retry: return False
        # One slow response must not tear down a live editor or its browser origin.
        await asyncio.sleep(.3)
        return await session['proxy'].healthy()

    async def authenticate(self, port, password):
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            login = await client.post('http://127.0.0.1:%d/login' % port, data={'password': password, 'base': '/'})
            if login.status_code not in (302, 303) or not client.cookies:
                raise ActionError('编辑器自动认证失败，请重新打开编辑器页面。')
            return '; '.join(k + '=' + v for k, v in client.cookies.items())

    def make_forward(self, address, port, session=None, *, target=None, cloud=False):
        old = session['forward'] if session else None
        if cloud or isinstance(old, CloudForward) or (old is None and self.site.id == 'sensecore'):
            if isinstance(old, CloudForward): target = old.target
            if target is None: raise ActionError('编辑器容器选择缺失。')
            target = dict(target)
            async def verify():
                self.target_leases.pop(target['task_id'], None)
                _, current = await self.resolve(target)
                if current != address: raise ActionError('容器地址已变化，请重新打开编辑器。')
            return CloudForward(self.bridge, target, address, port, verify=verify)
        env_key = 'SENSECORE_CCI_IDE_SSH_HOST' if self.site.id == 'sensecore' else 'MONITOR_CCI_IDE_SSH_HOST'
        alias = session['forward'].alias if session else os.environ.get(env_key, 'sensecore' if self.site.id == 'sensecore' else 'maoshanwangtech')
        return SSHForward(alias, address, port)

    async def connect(self, address, port, password, origin, notify, session=None, *, prepared_forward=None, target=None):
        forward = prepared_forward or self.make_forward(address, port, session, target=target)
        forward.remote_port = port
        proxy = None
        try:
            for attempt in range(2):
                authenticating = False
                try:
                    notify({'stage': 'connect'})
                    try: await forward.start()
                    except (ActionError, OSError, asyncio.TimeoutError):
                        if prepared_forward is None: raise
                        notify({'stage': 'ssh_retry'})
                        await forward.close()
                        forward = self.make_forward(address, port, session, target=target)
                        await forward.start()
                    authenticating = True
                    notify({'stage': 'authenticate'})
                    cookie = await self.authenticate(forward.port, password)
                    break
                except (ActionError, OSError, httpx.TransportError, asyncio.TimeoutError) as error:
                    if (attempt or self.site.id != 'maoshanwang' or not isinstance(forward, SSHForward)
                            or target is None or (authenticating and isinstance(error, ActionError))): raise
                    notify({'stage': 'ssh_failed'})
                    await forward.close()
                    forward = self.make_forward(address, port, target=target, cloud=True)
                    prepared_forward = None
            notify({'stage': 'cloud_connected' if isinstance(forward, CloudForward) else 'ssh_connected'})
            websocket_forward = lambda: self.make_forward(address, port, {'forward': forward}, target=target)
            if session:
                # Commit the replacement only after authentication succeeds. The local
                # listener, browser cookie and existing iframe document stay intact.
                previous = session['forward']
                async with session['proxy'].websocket_forward_lock:
                    if (isinstance(forward, CloudForward) != isinstance(previous, CloudForward)
                            or session.get('address', address) != address or previous.remote_port != port):
                        stale = session['proxy'].websocket_forward_instance
                        session['proxy'].websocket_forward_instance = None
                        if stale: await stale.close()
                    await previous.close()
                    session['proxy'].upstream = 'http://127.0.0.1:' + str(forward.port)
                    session['proxy'].cookie = cookie
                    session['proxy'].websocket_forward = websocket_forward
                    session.update(forward=forward, password=password, address=address)
                return session
            origin_identity = (self.site.id, target['task_id'], target['instance'], target['container'], origin, address) if target else None
            proxy = EditorProxy(forward.port, cookie, origin, cache_dir=self.site.data / 'ide-assets',
                                origin_registry=self.origin_registry, origin_identity=origin_identity,
                                websocket_forward=websocket_forward)
            await proxy.start()
            return {'forward': forward, 'proxy': proxy, 'address': address, 'password': password}
        except BaseException:
            if proxy: await proxy.close()
            await forward.close()
            raise

    async def recover(self, data, origin, progress=None, *, failed_upstream=None):
        notify = progress or (lambda value: None)
        identity = (data['task_id'], data['instance'], data['container'], origin)
        async with self.session_lock(identity):
            session = self.sessions.get(identity)
            if not session:
                raise ActionError('编辑器连接已过期；请保留当前页面中的未保存内容，再重新打开编辑器页面。', 'ide_expired', 404)
            if progress is None:
                def notify(value):
                    if value.get('stage') in TRANSPORT_STAGES:
                        events = session.setdefault('connection_events', [])
                        events.append(value['stage']); del events[:-16]
            else:
                for stage in session.pop('connection_events', []): notify({'stage': stage})
            session['proxy'].touched = time.monotonic()
            # A proxy failure already supplied the first failure signal. Confirm
            # once before replacement; routine heartbeats still get two probes.
            healthy = await self.healthy(session, retry=False) if failed_upstream == session['proxy'].upstream else await self.healthy(session)
            if not healthy:
                # Revalidate ownership before reconnecting: private IPs may be reused.
                self.target_leases.pop(data['task_id'], None)
                _, address = await self.resolve(data)
                if address != session['address']:
                    raise ActionError('容器地址已变化；请保留未保存内容后重新打开编辑器页面。')
                if not session.get('password'):
                    raise ActionError('编辑器连接无法自动恢复，请保留未保存内容后重新打开编辑器页面。')
                await self.connect(session['address'], session['forward'].remote_port, session['password'], origin, notify, session, target=data)
            session['proxy'].touched = time.monotonic()
            return self.details(data, origin)

    def details(self, data, origin):
        """Read local preparation state; never probe the cloud or start an install."""
        identity = (data['task_id'], data['instance'], data['container'], origin)
        session = self.sessions.get(identity)
        if not session:
            raise ActionError('编辑器会话已过期，请保留当前内容后重新打开。', 'ide_expired', 404)
        job = self.optional_jobs.get(identity)
        codex, appearance = session.get('codex', {}), session.get('appearance', {})
        return {'site': self.site.id, **data, 'port': session['proxy'].port,
                'workspace': session.get('workspace'),
                'codex': {key: codex[key] for key in ('configured', 'installed') if key in codex},
                'appearance': {key: appearance[key] for key in ('installed', 'version') if key in appearance},
                'optional_pending': bool(job and not job.done())}

    async def history(self, data, origin):
        """Read only filtered Codex session metadata from the active CCI container."""
        identity = (data['task_id'], data['instance'], data['container'], origin)
        session = self.sessions.get(identity)
        if not session:
            raise ActionError('编辑器会话已过期，请重新打开。', 'ide_expired', 404)
        workspace = _normal_path(data['workspace'] if 'workspace' in data else session.get('workspace'))
        if not isinstance(workspace, str) or not workspace.startswith('/'):
            raise ActionError('容器工作区路径暂不可用。', 'history_unavailable', 503)
        # Same-project callers share the result after the first read completes.
        # Separate workspaces do not block each other or evict each other immediately.
        async with self.session_lock((*identity, 'history', workspace)):
            if self.sessions.get(identity) is not session:
                raise ActionError('编辑器会话已过期，请重新打开。', 'ide_expired', 404)
            caches = session.setdefault('history_caches', {})
            cached = caches.get(workspace)
            if cached and time.monotonic() - cached['created'] < 60:
                return dict(cached['result'])
            try:
                raw = await run_python(self.bridge, data, history_script(workspace), timeout=45, cleanup_jobs=self.cleanup_jobs, max_result_bytes=MAX_HISTORY_RESPONSE_BYTES)
            except Exception:
                raise ActionError('容器 Codex 历史暂不可用。', 'history_unavailable', 503) from None
            if (not isinstance(raw, dict) or raw.get('error')
                    or not isinstance(raw.get('records'), list)
                    or ('workspace' in raw and not _normal_path(raw['workspace']))):
                raise ActionError('容器 Codex 历史暂不可用。', 'history_unavailable', 503)
            result = sanitize_history_result(raw, workspace)
            result = {'site': self.site.id, **data, **result, 'source': 'container'}
            caches.pop(workspace, None)
            if len(caches) >= 4:
                caches.pop(next(iter(caches)))
            caches[workspace] = {'created': time.monotonic(), 'result': result}
            return dict(result)

    def prepare_optional(self, identity, session, data):
        codex, appearance = session.get('codex', {}), session.get('appearance', {})
        needed = ((codex.get('configured') and codex.get('installed') is False)
                  or (appearance and (appearance.get('version') != APPEARANCE_VERSION
                                      or appearance.get('installed') is False)))
        job = self.optional_jobs.get(identity)
        if (not needed or (job and not job.done())
                or time.monotonic() - session.get('optional_attempted', float('-inf')) < 300): return
        session['optional_attempted'] = time.monotonic()
        async def prepare():
            try:
                prepared = await run_python(self.bridge, data, bootstrap_script(data['instance'], session['address'], download_proxy=self.download_proxy()), cleanup_jobs=self.cleanup_jobs)
                if 'error' not in prepared and self.sessions.get(identity) is session:
                    session.update(codex=prepared.get('codex', {}), appearance=prepared.get('appearance', {}),
                                  workspace=prepared.get('workspace') or session.get('workspace'))
                    if session['proxy'].assets is not None:
                        roots = prepared.get('asset_roots', [])
                        session['proxy'].assets.resource_roots = roots if isinstance(roots, list) else []
            except Exception:
                # Optional marketplace/setup failures must not break a usable editor.
                pass
        self.optional_jobs[identity] = asyncio.create_task(prepare())

    def download_proxy(self):
        prefix = 'SENSECORE' if self.site.id == 'sensecore' else 'MONITOR'
        return os.environ.get(prefix + '_CCI_IDE_DOWNLOAD_PROXY') or None

    def opened(self, identity, session, data, origin, notify):
        for stage in session.pop('connection_events', []): notify({'stage': stage})
        session['proxy'].repair = lambda failed_upstream: self.recover(data, origin, failed_upstream=failed_upstream)
        self.prepare_optional(identity, session, data)
        notify({'stage': 'reuse'})
        proxy = session['proxy']; proxy.touched = time.monotonic()
        job = self.optional_jobs.get(identity)
        return {'site': self.site.id, **data, 'name': session.get('name', data['task_id']),
                'port': proxy.port, 'ticket': proxy.ticket(), 'workspace': session.get('workspace'),
                'codex': session.get('codex', {}), 'appearance': session.get('appearance', {}),
                'optional_pending': bool(job and not job.done())}

    async def open(self, data, origin, progress=None):
        notify = progress or (lambda value: None)
        identity = (data['task_id'], data['instance'], data['container'], origin)
        async with self.session_lock(identity):
            session = self.sessions.get(identity)
            # The authenticated live session already establishes ownership, just as
            # for recover(). Revalidate cloud identity before every new transport.
            if session and await self.healthy(session):
                return self.opened(identity, session, data, origin, notify)
            if session: self.target_leases.pop(data['task_id'], None)
            result, address = await self.resolve(data)
            if session and session['address'] != address:
                await self.dispose(identity); session = None
            async with self.preparation_slot(identity):
                if session and session.get('password'):
                    try:
                        session = await self.connect(address, session['forward'].remote_port, session['password'], origin, notify, session, target=data)
                    except (ActionError, httpx.HTTPError, OSError, asyncio.TimeoutError):
                        # Explicit reopen may restart a stopped remote editor. Automatic
                        # recover remains transport-only and preserves unsaved buffers.
                        pass
                    else:
                        session['name'] = result['name']
                        return self.opened(identity, session, data, origin, notify)
                # New sessions can authenticate the jump host while the verified
                # container checks its editor. Existing recovery stays unchanged.
                warmed = self.make_forward(address, None, target=data) if session is None else None
                if isinstance(warmed, CloudForward): warmed = None
                warming = asyncio.create_task(warmed.warm()) if warmed else None
                try:
                    prepared = await run_python(self.bridge, data, bootstrap_script(data['instance'], address, install_optional=False, download_proxy=self.download_proxy()), on_progress=notify,
                                                verified_targets=self.target_leases.get(data['task_id']), cleanup_jobs=self.cleanup_jobs)
                    if 'error' in prepared:
                        # Arbitrary remote output is never reflected to the browser.
                        raise ActionError('容器内编辑器准备失败，请检查 Python 3、GitHub 下载和专用 editor.log。')
                    port = prepared.get('port'); password = prepared.get('password')
                    if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(password, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}', password): raise ActionError('容器编辑器返回无效配置。')
                    if warming:
                        try: await warming
                        except (ActionError, OSError, asyncio.TimeoutError):
                            notify({'stage': 'ssh_retry'})
                            await warmed.close(); warmed = None
                        warming = None
                    if warmed:
                        session = await self.connect(address, port, password, origin, notify, session, prepared_forward=warmed, target=data)
                        warmed = None  # The returned session owns its master.
                    else:
                        session = await self.connect(address, port, password, origin, notify, session, target=data)
                finally:
                    if warming:
                        warming.cancel()
                        await asyncio.gather(warming, return_exceptions=True)
                    if warmed: await warmed.close()
                session.update(codex=prepared.get('codex', {}), appearance=prepared.get('appearance', {}),
                               workspace=prepared.get('workspace'), name=result['name'])
                if session['proxy'].assets is not None:
                    roots = prepared.get('asset_roots', [])
                    session['proxy'].assets.resource_roots = roots if isinstance(roots, list) else []
                self.sessions[identity] = session
                return self.opened(identity, session, data, origin, notify)

    async def dispose(self, key):
        optional = self.optional_jobs.pop(key, None)
        if optional:
            optional.cancel()
            await asyncio.gather(optional, return_exceptions=True)
        session = self.sessions.pop(key)
        await session['proxy'].close()
        await session['forward'].close()

    async def reap(self):
        while True:
            await asyncio.sleep(30)
            for key in list(self.sessions):
                # Do not let one busy editor stall cleanup of all the others.
                if key in self.locks: continue
                async with self.session_lock(key):
                    item = self.sessions.get(key)
                    if item and not item['proxy'].active_sockets and time.monotonic() - item['proxy'].touched > 900:
                        await self.dispose(key)

    async def close(self):
        tasks = [item['task'] for item in self.requests.values()] + list(self.optional_jobs.values())
        if self.reaper: tasks.append(self.reaper)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*self.cleanup_jobs, return_exceptions=True)
        for key in list(self.sessions): await self.dispose(key)
