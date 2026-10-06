"""Persistent, exclusive loopback ports for authenticated editor identities."""
import asyncio
import errno
import fcntl
import json
import os
from pathlib import Path
import socket
import tempfile
import time

from .acp_actions import ActionError


LOCK_TIMEOUT_SECONDS = 2


def identity_key(identity):
    if not isinstance(identity, (list, tuple)) or len(identity) != 6 or any(not isinstance(item, str) or not item or len(item) > 2048 for item in identity):
        raise ValueError('invalid editor identity')
    return json.dumps(identity, ensure_ascii=True, separators=(',', ':'))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate registry key')
        result[key] = value
    return result


class EditorOriginRegistry:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            with self.path.open() as source:
                data = json.load(source, object_pairs_hook=unique_object)
        except FileNotFoundError:
            return {}
        if (not isinstance(data, dict) or set(data) != {'version', 'origins'}
                or type(data['version']) is not int or data['version'] != 1 or not isinstance(data['origins'], dict)):
            raise ValueError('invalid registry')
        ports = set()
        for key, port in data['origins'].items():
            if identity_key(json.loads(key)) != key or type(port) is not int or not 1024 <= port <= 65535 or port in ports:
                raise ValueError('invalid registry entry')
            ports.add(port)
        return data['origins']

    def save(self, origins):
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as target:
                json.dump({'version': 1, 'origins': origins}, target, ensure_ascii=True)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    @staticmethod
    def socket(port):
        sock = socket.socket()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))
            # Listen before releasing the registry lock: SO_REUSEADDR alone can
            # otherwise allow a second process to bind the same inactive socket.
            sock.listen()
            return sock
        except BaseException as error:
            sock.close()
            if isinstance(error, OSError) and error.errno == errno.EADDRINUSE and port:
                raise ActionError('固定编辑器端口已被占用，请关闭旧编辑器连接或占用该端口的本地程序后重试。', 'ide_origin_busy', 409) from None
            raise

    def bind_socket(self, identity):
        sock = None
        try:
            key = identity_key(identity)
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(fd, 'a+b') as lock:
                deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ActionError('编辑器端口注册表正被其他连接占用，请稍后重试。', 'ide_origin_locked', 503) from None
                        time.sleep(min(.02, remaining))
                origins = self.load()
                if key in origins:
                    sock = self.socket(origins[key])
                else:
                    used = set(origins.values())
                    for _ in range(128):
                        sock = self.socket(0)
                        port = sock.getsockname()[1]
                        if port not in used: break
                        sock.close(); sock = None
                    if sock is None:
                        raise ActionError('没有可分配的独立编辑器端口，请关闭闲置本地连接后重试。', 'ide_origin_busy', 409)
                    origins[key] = port
                    self.save(origins)
            return sock
        except BaseException as error:
            if sock is not None: sock.close()
            if isinstance(error, (OSError, ValueError, TypeError)):
                raise ActionError('编辑器端口注册表无法读取或保存，请检查本地文件；为保护原有浏览器状态，本次未更换地址。', 'ide_origin_registry', 503) from None
            raise

    async def bind(self, identity):
        task = asyncio.create_task(asyncio.to_thread(self.bind_socket, identity))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A worker may still own the file lock or acquire a socket after its
            # caller leaves. Drain it and close its result before propagating.
            drained = asyncio.gather(task, return_exceptions=True)
            while not drained.done():
                try: await asyncio.shield(drained)
                except asyncio.CancelledError: pass
            result = drained.result()[0]
            if not isinstance(result, BaseException): result.close()
            raise
