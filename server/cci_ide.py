"""Prepare a CCI-local editor and keep its authenticated transport on this Mac."""
import asyncio
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import time

import httpx
from .acp_actions import ActionError
from .auth import AuthRequired
from .ide_bootstrap import bootstrap_script
from .ide_proxy import EditorProxy


def private_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
        if not any(address in ipaddress.IPv4Network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')): raise ValueError()
        return str(address)
    except (ValueError, TypeError):
        raise ActionError('CCI 实例未返回有效内网地址。') from None


async def run_python(bridge, data, script, *, timeout=600):
    owner = 'cci-editor-bootstrap'
    opened = await bridge.open({**data, 'cols': 240, 'rows': 40}, owner)
    key = opened['session_id']
    tag = secrets.token_hex(16)
    payload = 'import json\ntry:\n' + '\n'.join('    ' + line for line in script.splitlines()) + '\nexcept Exception as exc:\n    RESULT = {"error": str(exc)}\nprint(chr(30)+' + repr(tag) + '+json.dumps(RESULT)+chr(31), flush=True)\n'
    encoded = base64.b64encode(payload.encode()).decode()
    # Heredoc lines remain under canonical PTY input limits. Only our own shell is changed.
    command = "stty -echo; python3 - <<'ILAB_EDITOR_PY'\nimport base64\nexec(base64.b64decode(\n" + '\n'.join(repr(encoded[i:i+1000]) for i in range(0, len(encoded), 1000)) + "))\nILAB_EDITOR_PY\nstty echo\n"
    async def execute():
        for index, start in enumerate(range(0, len(command), 1800), 1):
            await bridge.write(key, index, command[start:start+1800], None, owner)
        raw = b''; cursor = 0
        marker = b'\x1e' + tag.encode()
        while True:
            result = await bridge.read(key, cursor, owner)
            cursor = result['cursor']
            raw += b''.join(base64.b64decode(chunk) for chunk in result['chunks'])
            if marker in raw and b'\x1f' in raw.split(marker, 1)[1]:
                message = raw.split(marker, 1)[1].split(b'\x1f', 1)[0]
                if len(message) > 16384: raise ActionError('编辑器返回异常。')
                return json.loads(message)
            if result['closed']: raise ActionError('编辑器准备时容器连接中断。')
            if len(raw) > 1024 * 1024: raise ActionError('编辑器准备输出过长。')
    try:
        return await asyncio.wait_for(execute(), timeout)
    finally:
        try: await bridge.disconnect(key, owner)
        except ActionError: pass


class SSHForward:
    def __init__(self, alias, address, port):
        self.alias, self.address, self.remote_port = alias, address, port
        self.port = None
        self.process = None

    async def start(self):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', self.alias):
            raise ActionError('编辑器 SSH 别名配置无效。')
        known = Path.home() / '.ssh/known_hosts'
        if not known.is_file(): raise ActionError('请先在此电脑配置并验证编辑器 SSH 跳板连接。')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        self.process = await asyncio.create_subprocess_exec('/usr/bin/ssh', '-N', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(known), '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3', '-L', '127.0.0.1:%d:%s:%d' % (self.port, self.address, self.remote_port), self.alias, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        for _ in range(150):
            if self.process.returncode is not None: break
            try:
                reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
                writer.close(); await writer.wait_closed()
                return
            except OSError: await asyncio.sleep(.1)
        await self.close()
        raise ActionError('SSH 跳板连接失败，请检查本机 SSH 别名、密钥及 known_hosts。')

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try: await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.kill(); await self.process.wait()


class CCIEditor:
    def __init__(self, site, bridge, resolver):
        self.site, self.bridge, self.resolver = site, bridge, resolver
        self.requests = {}
        self.sessions = {}
        self.lock = asyncio.Lock()
        self.reaper = None

    async def begin(self, action, data, origin):
        if action not in ('targets', 'open'): raise ActionError('编辑器请求无效。')
        if not isinstance(data.get('task_id'), str) or not re.fullmatch(r'CCI:[A-Za-z0-9][A-Za-z0-9_-]{0,99}', data['task_id']):
            raise ActionError('仅本人运行中的 CCI 支持站内 VS Code。')
        if action == 'open':
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
        async def prepare():
            try:
                result = await asyncio.wait_for(self.targets(data['task_id']) if action == 'targets' else self.open(data, origin), 720)
                return {'pending': False, 'result': result}
            except ActionError as exc: return {'pending': False, 'error': str(exc)}
            except Exception: return {'pending': False, 'error': '编辑器准备失败，请检查容器网络、Python 3 和本机 SSH 配置后重试。'}
        key = secrets.token_urlsafe(32)
        self.requests[key] = {'task': asyncio.create_task(prepare()), 'created': now, 'origin': origin, 'action': action, 'data': dict(data)}
        if self.reaper is None: self.reaper = asyncio.create_task(self.reap())
        return {'request_id': key}

    async def status(self, key, origin):
        item = self.requests.get(key) if isinstance(key, str) else None
        if not item or item['origin'] != origin: raise ActionError('编辑器请求已过期，请重试。', 'ide_expired', 404)
        if not item['task'].done(): return {'pending': True, 'message': '正在准备编辑器，首次下载约 220 MB，请稍候…'}
        return item['task'].result()

    async def targets(self, task_id):
        if not task_id.startswith('CCI:'): raise ActionError('仅 CCI 支持站内 VS Code。')
        return await self.bridge.targets(task_id)

    async def resolve(self, data):
        started = time.monotonic()
        try: result = await self.resolver.targets(data['task_id'], include_address=True)
        except AuthRequired:
            await self.bridge.renew_once(started)
            result = await self.resolver.targets(data['task_id'], include_address=True)
        target = next((t for t in result['targets'] if t['instance'] == data['instance'] and t['container'] == data['container']), None)
        if not target: raise ActionError('所选 CCI 容器已不可用，请刷新任务。')
        return result, private_ipv4(target['address'])

    async def open(self, data, origin):
        async with self.lock:
            result, address = await self.resolve(data)
            identity = (data['task_id'], data['instance'], data['container'], origin)
            session = self.sessions.get(identity)
            if session and (session['address'] != address or session['forward'].process.returncode is not None or not await session['proxy'].healthy()):
                await self.dispose(identity); session = None
            if not session:
                if len(self.sessions) >= 4: raise ActionError('最多同时打开 4 个编辑器，请关闭闲置页面并稍后重试。', 'ide_limit', 429)
                prepared = await run_python(self.bridge, data, bootstrap_script(data['instance'], address))
                if 'error' in prepared:
                    # Arbitrary remote output is never reflected to the browser.
                    raise ActionError('容器内编辑器准备失败，请检查 Python 3、GitHub 下载和专用 editor.log。')
                port = prepared.get('port'); password = prepared.get('password')
                if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(password, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}', password): raise ActionError('容器编辑器返回无效配置。')
                env_key = 'SENSECORE_CCI_IDE_SSH_HOST' if self.site.id == 'sensecore' else 'MONITOR_CCI_IDE_SSH_HOST'
                forward = SSHForward(os.environ.get(env_key, 'sensecore' if self.site.id == 'sensecore' else 'maoshanwangtech'), address, port)
                proxy = None
                try:
                    await forward.start()
                    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
                        login = await client.post('http://127.0.0.1:%d/login' % forward.port, data={'password': password, 'base': '/'})
                        if login.status_code not in (302, 303) or not client.cookies: raise ActionError('编辑器自动认证失败，请重新准备。')
                        cookie = '; '.join(k + '=' + v for k, v in client.cookies.items())
                    proxy = EditorProxy(forward.port, cookie, origin)
                    await proxy.start()
                    session = {'forward': forward, 'proxy': proxy, 'address': address}
                    self.sessions[identity] = session
                except BaseException:
                    if proxy: await proxy.close()
                    await forward.close()
                    raise
            proxy = session['proxy']; proxy.touched = time.monotonic()
            return {'site': self.site.id, 'task_id': data['task_id'], 'name': result['name'], 'instance': data['instance'], 'container': data['container'], 'port': proxy.port, 'ticket': proxy.ticket()}

    async def dispose(self, key):
        session = self.sessions.pop(key)
        await session['proxy'].close()
        await session['forward'].close()

    async def reap(self):
        while True:
            await asyncio.sleep(30)
            async with self.lock:
                for key, item in list(self.sessions.items()):
                    if not item['proxy'].active_sockets and time.monotonic() - item['proxy'].touched > 900:
                        await self.dispose(key)

    async def close(self):
        tasks = [item['task'] for item in self.requests.values()]
        if self.reaper: tasks.append(self.reaper)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for key in list(self.sessions): await self.dispose(key)
