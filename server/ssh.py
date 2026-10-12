"""Local SSH inventory and adapters for the existing terminal/editor lifecycle."""
import asyncio
import errno
import fcntl
import glob
import json
import os
from pathlib import Path
import pty
import re
import secrets
import shlex
import signal
import struct
import termios
import time
from types import SimpleNamespace

from .acp_actions import ActionError
from .cci_ide import CCIEditor, SSHForward
from .config import DATA
from .ide_bootstrap import bootstrap_script
from .terminal_bridge import TerminalBridge, Session, dimensions, MAX_SESSIONS, IDLE_SECONDS

ALIAS = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}'
SSH_SITE = SimpleNamespace(id='ssh', data=DATA / 'ssh')


def config_aliases(path, seen=None):
    seen = set() if seen is None else seen
    path = Path(path).expanduser().resolve()
    if path in seen or not path.is_file(): return []
    seen.add(path)
    aliases = []
    for line in path.read_text().splitlines():
        fields = shlex.split(line, comments=True)
        if not fields: continue
        # OpenSSH accepts both "Host value" and "Host=value".
        key, separator, value = fields[0].partition('=')
        values = ([value] if separator and value else []) + fields[1:]
        if values and values[0] == '=': values.pop(0)
        if key.lower() == 'host':
            aliases.extend(item for item in values if re.fullmatch(ALIAS, item))
        elif key.lower() == 'include':
            for pattern in values:
                candidate = Path(pattern).expanduser()
                if not candidate.is_absolute(): candidate = path.parent / candidate
                for included in sorted(glob.glob(str(candidate))):
                    aliases.extend(config_aliases(included, seen))
    return list(dict.fromkeys(aliases))


async def stop_process(process):
    if process.returncode is not None: return
    try: os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: await asyncio.wait_for(process.wait(), 2)
    except asyncio.TimeoutError:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        await process.wait()


async def command(*args, timeout=12, capture_stderr=False):
    process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL, start_new_session=True)
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout)
        return (process.returncode, output, error) if capture_stderr else (process.returncode, output)
    finally:
        await stop_process(process)


class SSHCatalog:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path.home() / '.ssh/config'
        self.task = None
        self.state = {'available': [], 'unavailable': [], 'verified': [], 'stale': False,
                      'total': 0, 'checked': 0, 'checked_at': None, 'error': None}
        self.probe_reasons = {}
        self.host_lock = asyncio.Lock()
        self.host_cache = None
        self.host_read_at = 0

    def options(self):
        return ['-F', str(self.path), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                '-o', 'ConnectionAttempts=1',
                '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ClearAllForwardings=yes',
                '-o', 'PermitLocalCommand=no', '-o', 'RemoteCommand=none',
                '-o', 'ForkAfterAuthentication=no', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']

    async def hosts(self, fresh=False):
        async with self.host_lock:
            if not fresh and self.host_cache is not None and time.monotonic() - self.host_read_at < 5:
                return self.host_cache
            aliases = await asyncio.to_thread(config_aliases, self.path)
            limit = asyncio.Semaphore(6)
            async def resolve(alias):
                if any(name in alias.lower() for name in ('github', 'gitlab')): return None
                async with limit:
                    code, output = await command('/usr/bin/ssh', '-G', '-F', str(self.path), alias)
                if code: raise ActionError('SSH 配置解析失败，请检查 ~/.ssh/config。')
                fields = dict(line.split(None, 1) for line in output.decode(errors='replace').splitlines() if ' ' in line)
                hostname = fields.get('hostname', alias)
                if any(name in hostname.lower() for name in ('github', 'gitlab')): return None
                return {'alias': alias, 'hostname': hostname, 'user': fields.get('user', ''), 'port': int(fields.get('port', 22))}
            self.host_cache = [host for host in await asyncio.gather(*(resolve(alias) for alias in aliases)) if host]
            self.host_read_at = time.monotonic()
            return self.host_cache

    async def require(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch('SSH:' + ALIAS, task_id):
            raise ActionError('SSH 服务器标识无效。')
        host = next((host for host in await self.hosts() if host['alias'] == task_id[4:]), None)
        if host is None: raise ActionError('该服务器不在当前 SSH 配置中，请重新检测。')
        return host

    async def probe(self, host):
        reason = 'connection'
        try:
            code, output, error = await command('/usr/bin/ssh', '-T', *self.options(), host['alias'],
                                                'printf ILAB_SSH_OK', timeout=10, capture_stderr=True)
            if code == 0 and output.endswith(b'ILAB_SSH_OK'): return True
            # Only these categories leave the process boundary, never raw SSH diagnostics.
            message = error.decode(errors='replace').lower()
            for category, phrases in (
                ('host_key', ('host key verification failed', 'remote host identification has changed')),
                ('authentication', ('permission denied', 'no supported authentication')),
                ('dns', ('could not resolve hostname', 'name or service not known')),
                ('timeout', ('timed out', 'timeout')),
                ('network', ('no route to host', 'network is unreachable')),
            ):
                if any(phrase in message for phrase in phrases):
                    reason = category
                    break
        except asyncio.TimeoutError: reason = 'timeout'
        except OSError: reason = 'connection'
        self.probe_reasons[host['alias']] = reason
        return False

    def refresh(self):
        if self.task and not self.task.done(): return self.snapshot()
        previous = self.state
        self.state = {**previous, 'available': list(previous['available']), 'unavailable': [],
                      'verified': [], 'stale': bool(previous['available']), 'checked': 0, 'error': None}
        self.probe_reasons = {}
        async def scan():
            try:
                hosts = await self.hosts(fresh=True)
                self.state['total'] = len(hosts)
                limit = asyncio.Semaphore(6)
                async def check(host):
                    async with limit:
                        if await self.probe(host):
                            self.state['available'] = [old for old in self.state['available'] if old['alias'] != host['alias']] + [host]
                            self.state['verified'].append(host['alias'])
                        else:
                            self.state['unavailable'].append({**host, 'reason': self.probe_reasons.get(host['alias'], 'connection')})
                        self.state['checked'] += 1
                await asyncio.gather(*(check(host) for host in hosts))
                self.state['available'] = [host for host in self.state['available'] if host['alias'] in self.state['verified']]
                self.state['available'].sort(key=lambda host: host['alias'].lower())
                self.state['unavailable'].sort(key=lambda host: host['alias'].lower())
                self.state['stale'] = False
                self.state['checked_at'] = time.time()
            except (OSError, ValueError, ActionError, asyncio.TimeoutError):
                self.state = {**previous, 'verified': [], 'stale': True}
                self.state['error'] = '无法读取 SSH 配置，请检查 ~/.ssh/config 后重试。'
        self.task = asyncio.create_task(scan())
        return self.snapshot()

    def snapshot(self):
        return {**self.state, **{key: list(self.state[key]) for key in ('available', 'unavailable', 'verified')},
                'checking': bool(self.task and not self.task.done())}

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


class PTYConnection:
    """Adapt an OpenSSH PTY to the channel protocol consumed by Session."""
    def __init__(self, process, fd):
        self.process, self.fd = process, fd
        self.closed = False
        self.finished = False
        os.set_blocking(fd, False)

    @classmethod
    async def open(cls, catalog, alias, size):
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', size['Height'], size['Width'], 0, 0))
            process = await asyncio.create_subprocess_exec('/usr/bin/ssh', '-tt', *catalog.options(), alias,
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True, env={**os.environ, 'TERM': 'xterm-256color'})
            return cls(process, master)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)

    async def ready(self, writing=False):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        def done():
            if not future.done(): future.set_result(None)
        add, remove = (loop.add_writer, loop.remove_writer) if writing else (loop.add_reader, loop.remove_reader)
        add(self.fd, done)
        try: await future
        finally: remove(self.fd)

    def __aiter__(self): return self

    async def __anext__(self):
        while not self.closed and not self.finished:
            try:
                data = os.read(self.fd, 32768)
                if not data: break
                return b'\1' + data
            except BlockingIOError: await self.ready()
            except OSError as exc:
                if exc.errno != errno.EIO: raise
                break
        if not self.closed and not self.finished:
            self.finished = True
            code = await self.process.wait()
            if code == 0: return b'\3{"status":"Success"}'
            if 0 < code < 255: return b'\3{"reason":"NonZeroExitCode"}'
        raise StopAsyncIteration

    async def send(self, frame):
        if self.closed: raise OSError('SSH session closed')
        if frame[0] == 4:
            size = json.loads(frame[1:])
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', size['Height'], size['Width'], 0, 0))
            # The subprocess owns no controlling TTY; notify ssh to forward the resize.
            try: os.kill(self.process.pid, signal.SIGWINCH)
            except ProcessLookupError: pass
        elif frame[0] == 0:
            data = memoryview(frame)[1:]
            while data:
                try: data = data[os.write(self.fd, data):]
                except BlockingIOError: await self.ready(writing=True)

    async def close(self):
        if self.closed: return
        self.closed = True
        await stop_process(self.process)
        os.close(self.fd)


class SSHBridge(TerminalBridge):
    def __init__(self, catalog):
        super().__init__(SSH_SITE, None, None)
        self.catalog = catalog

    async def targets(self, task_id):
        host = await self.catalog.require(task_id)
        return {'site': 'ssh', 'task_id': task_id, 'name': host['alias'],
                'targets': [{'instance': host['alias'], 'container': 'shell'}]}

    async def open(self, data, owner, *, verified_targets=None):
        size = dimensions(data['cols'], data['rows'])
        host = await self.catalog.require(data['task_id'])
        if data['instance'] != host['alias'] or data['container'] != 'shell':
            raise ActionError('SSH 连接与所选服务器不一致。')
        async with self.open_lock:
            for key, session in list(self.sessions.items()):
                if session.ended or (session.transport is None and time.monotonic() - session.touched > IDLE_SECONDS):
                    await session.close(); del self.sessions[key]
            if len(self.sessions) >= MAX_SESSIONS: raise ActionError('已打开太多终端，请先断开闲置会话。')
            connection = await PTYConnection.open(self.catalog, host['alias'], size)
            key = secrets.token_urlsafe(32)
            session = self.sessions[key] = Session(connection, owner)
            session.target = {field: data[field] for field in ('task_id', 'instance', 'container')}
            return {'site': 'ssh', 'task_id': data['task_id'], 'session_id': key, 'tmux_token': session.tmux_token}


class ConfigSSHForward(SSHForward):
    def __init__(self, catalog, alias, address, port):
        super().__init__(alias, address, port)
        self.catalog = catalog

    async def start(self):
        if self.control_directory is None: await self.warm()
        await super().start()

    def options(self):
        # The warm master clears configured forwards; only our loopback tunnel is added.
        options = self.catalog.options()
        index = options.index('ClearAllForwardings=yes')
        del options[index - 1:index + 1]
        return [*options, '-o', 'ExitOnForwardFailure=yes']


class SSHEditor(CCIEditor):
    task_pattern = 'SSH:' + ALIAS

    def __init__(self, bridge):
        super().__init__(SSH_SITE, bridge, bridge)

    async def targets(self, task_id):
        return await self.bridge.targets(task_id)

    async def resolve(self, data):
        result = await self.targets(data['task_id'])
        if {'instance': data['instance'], 'container': data['container']} not in result['targets']:
            raise ActionError('SSH 连接与所选服务器不一致。')
        return result, '127.0.0.1'

    def bootstrap(self, data, address, *, install_optional=True):
        # SSH authenticates the configured host; its OS hostname need not equal its alias.
        return bootstrap_script(None, address, install_optional=install_optional,
                                download_proxy=self.download_proxy(), activate_proxy=True)

    def make_forward(self, address, port, session=None, *, target=None, cloud=False):
        alias = target['task_id'][4:] if target else session['forward'].alias
        return ConfigSSHForward(self.bridge.catalog, alias, address, port)
