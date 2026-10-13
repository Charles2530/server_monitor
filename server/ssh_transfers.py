"""Shared file browser and cancellable scp transfers for SSH management."""
import asyncio
import ctypes
import json
import os
from pathlib import Path
import posixpath
import secrets
import shlex
import shutil
import sys
import tempfile
import time
import re

from .acp_actions import ActionError
from .ssh import command, stop_process


REMOTE_SCRIPT = r'''
import ctypes, itertools, json, os, shutil, stat, sys, tempfile
path = os.path.realpath(sys.argv[1] or os.path.expanduser('~'))
mode = sys.argv[2]
try:
    if mode in ('check', 'prepare', 'publish', 'cleanup'):
        name = sys.argv[3]
        if mode in ('check', 'prepare'):
            if name != os.path.basename(name) or name in ('', '.', '..'): raise ValueError('name')
            if not os.path.isdir(path): raise NotADirectoryError()
            if os.path.lexists(os.path.join(path, name)): raise FileExistsError()
            if mode == 'prepare': path = tempfile.mkdtemp(prefix='.monitor-transfer-', dir=path)
        else:
            if not os.path.basename(path).startswith('.monitor-transfer-'): raise ValueError('stage')
            if mode == 'cleanup': shutil.rmtree(path)
            else:
                if name != os.path.basename(name) or name in ('', '.', '..'): raise ValueError('name')
                source, target = os.fsencode(os.path.join(path, name)), os.fsencode(os.path.join(os.path.dirname(path), name))
                library = ctypes.CDLL(None, use_errno=True)
                if sys.platform == 'darwin':
                    result = library.renamex_np(source, target, ctypes.c_uint(4))
                elif hasattr(library, 'renameat2'):
                    result = library.renameat2(-100, source, -100, target, ctypes.c_uint(1))
                else: raise OSError('Atomic rename unavailable')
                if result: raise OSError(ctypes.get_errno(), 'Cannot publish')
        print(json.dumps(dict(path=path)))
        sys.exit(0)
    info = os.stat(path)
    kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else None
    if kind is None: raise ValueError('unsupported')
    result = dict(path=path, parent=os.path.dirname(path), name=os.path.basename(path), kind=kind, size=info.st_size)
    if mode == 'list' and kind == 'directory':
        entries = []
        with os.scandir(path) as items:
            for index, item in enumerate(itertools.islice(items, 2001)):
                if index == 2000: break
                try:
                    s = item.stat()
                    k = 'directory' if stat.S_ISDIR(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else None
                    if k and not any(ord(c) < 32 for c in item.name):
                        entries.append(dict(name=item.name, path=item.path, kind=k, size=s.st_size))
                except OSError: pass
            else: index = -1
        entries.sort(key=lambda e: (e['kind'] != 'directory', e['name'].lower()))
        result.update(entries=entries, parent=os.path.dirname(path), truncated=index == 2000)
    print(json.dumps(result, ensure_ascii=True))
except (OSError, ValueError) as error:
    print(json.dumps(dict(error='exists' if isinstance(error, FileExistsError) else 'permission' if isinstance(error, PermissionError) else 'unavailable')))
'''


def validate_path(value, allow_home=False):
    if allow_home and value == '': return value
    if (not isinstance(value, str) or not value.startswith('/') or len(value) > 4096
            or any(ord(char) < 32 for char in value)):
        raise ActionError('请选择有效的绝对路径。')
    return value


def local_listing(path='', folders_only=True):
    validate_path(path, allow_home=True)
    directory = Path(path or Path.home())
    entries, truncated = [], False
    try:
        directory = directory.resolve()
        if not folders_only and directory.is_file():
            return {'path': str(directory), 'parent': str(directory.parent), 'name': directory.name, 'kind': 'file', 'size': directory.stat().st_size}
        with os.scandir(directory) as items:
            for index, item in enumerate(items):
                if index >= 10000:
                    truncated = True
                    break
                try:
                    kind = 'directory' if item.is_dir() else 'file' if item.is_file() else None
                    if kind and (not folders_only or kind == 'directory') and not any(ord(c) < 32 for c in item.name):
                        entries.append({'name': item.name, 'path': item.path, 'kind': kind, 'size': item.stat().st_size})
                except OSError: pass
    except PermissionError:
        raise ActionError(f'无权访问本机路径：{directory}。请检查目录权限；若为 macOS 受保护目录，请在“系统设置 → 隐私与安全性”中允许服务使用的 Python 访问。',
                          'local_permission_denied', 403) from None
    except FileNotFoundError:
        raise ActionError(f'本机路径不存在：{directory}。', 'local_path_missing', 404) from None
    except OSError:
        raise ActionError(f'无法读取本机目录：{directory}。请检查路径及访问权限。') from None
    entries.sort(key=lambda entry: (entry['kind'] != 'directory', entry['name'].lower()))
    return {'path': str(directory), 'parent': str(directory.parent), 'entries': entries, 'truncated': truncated}


def publish(source, target):
    """Atomic publication without replacing even a target created during the copy."""
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        rename = library.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(target), 4)  # RENAME_EXCL
    elif hasattr(library, 'renameat2'):
        rename = library.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)  # RENAME_NOREPLACE
    else:
        raise OSError('当前系统不支持安全保存下载。')
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


_SCP_PROGRESS = re.compile(
    r"(?P<file>.+?)\s+(?P<percent>\d{1,3})%\s+"
    r"(?P<size>[\d.]+)\s*(?P<size_unit>[KMGTP]?B)\s+"
    r"(?P<speed>[\d.]+)\s*(?P<speed_unit>[KMGTP]?B)/s\s+"
    r"(?P<eta>\d{1,2}:\d{2})"
)
_SIZE_UNITS = {"B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3,
               "TB": 1000 ** 4, "PB": 1000 ** 5}


def _scaled_size(value, unit):
    return int(float(value) * _SIZE_UNITS.get(unit.upper(), 1))


def parse_scp_progress(line):
    """Parse one OpenSSH scp progress line without exposing its raw diagnostics."""
    match = _SCP_PROGRESS.search(line.strip())
    if not match:
        return None
    minutes, seconds = match["eta"].split(":", 1)
    return {
        "current_file": match["file"].strip(),
        "current_percent": min(100, int(match["percent"])),
        "transferred_bytes": _scaled_size(match["size"], match["size_unit"]),
        "speed_bps": _scaled_size(match["speed"], match["speed_unit"]),
        "eta_seconds": int(minutes) * 60 + int(seconds),
    }


def apply_scp_progress(job, progress):
    job.update(progress)
    total = job.get("total_bytes")
    if total and progress.get("current_percent") is not None:
        job["transferred_bytes"] = min(total, round(total * progress["current_percent"] / 100))
        job["progress_percent"] = progress["current_percent"]
        speed = progress.get("speed_bps") or 0
        job["eta_seconds"] = round(max(0, total - job["transferred_bytes"]) / speed) if speed else None


class SSHTransfers:
    def __init__(self, catalog):
        self.catalog = catalog
        self.jobs = {}
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.closed = False

    async def remote(self, alias, path='', mode='list', name=''):
        validate_path(path, allow_home=mode == 'list')
        host = await self.catalog.require('SSH:' + alias if isinstance(alias, str) else '')
        script = shlex.join(['python3', '-c', REMOTE_SCRIPT, path, mode, name])
        try:
            code, output, _ = await command('/usr/bin/ssh', '-T', *self.catalog.options(), host['alias'],
                                             script, timeout=25, capture_stderr=True)
        except asyncio.TimeoutError:
            raise ActionError('读取远端目录超时，请检查连接后重试。', 'ssh_timeout', 504) from None
        if code:
            raise ActionError('远端文件读取失败，请检查 SSH 连接以及服务器是否安装 Python 3。')
        try: result = json.loads(output)
        except (ValueError, UnicodeError):
            raise ActionError('远端未返回有效的文件列表。') from None
        if isinstance(result, dict) and result.get('error') == 'exists':
            raise ActionError('保存位置已有同名文件或文件夹，请选择其他目录。')
        if not isinstance(result, dict) or result.get('error'):
            raise ActionError('无法访问远端路径，请检查路径及读取权限。')
        validate_path(result.get('path'))
        return result

    def scp_args(self, alias, source, directory, recursive, direction='download'):
        # scp's SFTP source is a glob, not a shell expression. Escape glob metacharacters.
        literal = ''.join('\\' + char if char in '\\*?[]{}' else char for char in source)
        endpoints = [source, alias + ':' + str(directory) + '/'] if direction == 'upload' else [alias + ':' + literal, str(directory)]
        return ['/usr/bin/scp', '-s', '-B', *self.catalog.options(), *(['-r'] if recursive else []), '--', *endpoints]

    async def start(self, alias, source, destination, direction='download'):
        if direction not in ('upload', 'download'): raise ActionError('传输方向无效。')
        validate_path(source)
        validate_path(destination)
        if direction == 'download':
            info = await self.remote(alias, source, 'stat')
        else:
            await self.catalog.require('SSH:' + alias if isinstance(alias, str) else '')
            item = Path(source).resolve()
            info = {'path': str(item), 'kind': 'directory' if item.is_dir() else 'file' if item.is_file() else None}
        source = info['path']
        name = posixpath.basename(source)
        if not name or info.get('kind') not in ('file', 'directory'):
            raise ActionError('请选择普通文件或文件夹，不能传输整个根目录。')
        if direction == 'download':
            directory = Path(destination).resolve()
            if not directory.is_dir(): raise ActionError('本机保存目录不存在。')
        else:
            directory = Path((await self.remote(alias, destination, 'check', name))['path'])
        target = directory / name
        async with self.lock:
            if self.closed: raise ActionError('服务正在关闭，请稍后重试。')
            running = [job for job in self.jobs.values() if not self.tasks[job['id']].done()]
            if len(running) >= 3: raise ActionError('最多同时传输 3 项，请等待当前传输完成。')
            if (direction == 'download' and os.path.lexists(target)) or any(
                    job['destination'] == str(target) and job['direction'] == direction
                    and (direction == 'download' or job['alias'] == alias) for job in running):
                raise ActionError('保存位置已有同名文件或文件夹，请选择其他目录。')
            for key in list(self.jobs):
                if len(self.jobs) < 100: break
                if self.tasks[key].done():
                    del self.jobs[key]
                    del self.tasks[key]
            key = secrets.token_urlsafe(24)
            total = info.get('size') if info.get('kind') == 'file' else None
            job = self.jobs[key] = {'id': key, 'alias': alias, 'direction': direction, 'source': source, 'destination': str(target),
                                   'state': 'running', 'started_at': time.time(), 'error': None,
                                   'total_bytes': total, 'transferred_bytes': 0, 'progress_percent': 0 if total else None,
                                   'speed_bps': 0, 'eta_seconds': None, 'current_file': posixpath.basename(source)}
            self.tasks[key] = asyncio.create_task(self._run(job, directory, info['kind'] == 'directory'))
            return dict(job)

    async def _run(self, job, directory, recursive):
        staging, process = None, None
        upload = job['direction'] == 'upload'
        try:
            if upload:
                prepare = asyncio.create_task(self.remote(job['alias'], str(directory), 'prepare', posixpath.basename(job['source'])))
                try: staging = Path((await asyncio.shield(prepare))['path'])
                except asyncio.CancelledError:
                    staging = Path((await prepare)['path'])
                    raise
            else: staging = Path(tempfile.mkdtemp(prefix='.monitor-download-', dir=directory))
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(*self.scp_args(job['alias'], job['source'], staging, recursive, job['direction']),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, start_new_session=True))
            try: process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                process = await spawn
                raise
            # Drain diagnostics while extracting only the safe, numeric progress fields.
            error = b''
            progress_buffer = ''
            while True:
                chunk = await process.stderr.read(8192)
                if not chunk: break
                error = (error + chunk)[-8192:]
                progress_buffer += chunk.decode('utf-8', 'replace')
                parts = re.split(r'[\r\n]', progress_buffer)
                progress_buffer = parts.pop()
                for line in parts:
                    progress = parse_scp_progress(line)
                    if progress: apply_scp_progress(job, progress)
            progress = parse_scp_progress(progress_buffer)
            if progress: apply_scp_progress(job, progress)
            code = await process.wait()
            if code:
                reason = error.lower()
                if b'no space left' in reason: raise ActionError('目标磁盘空间不足。')
                if b'permission denied' in reason: raise ActionError('传输权限不足，请检查 SSH 认证、源文件及保存目录权限。')
                raise ActionError('scp 传输失败，请检查 SSH 连接、源文件及可用磁盘空间。')
            job['state'] = 'finalizing'
            if upload: await self.remote(job['alias'], str(staging), 'publish', posixpath.basename(job['source']))
            else: publish(staging / posixpath.basename(job['source']), Path(job['destination']))
            job['state'] = 'completed'
            if job.get('total_bytes'):
                job['transferred_bytes'] = job['total_bytes']
                job['progress_percent'] = 100
                job['eta_seconds'] = 0
        except asyncio.CancelledError:
            job['state'] = 'cancelling'
        except FileExistsError:
            job.update(state='failed', error='传输期间出现同名目标，已停止保存，原文件未覆盖。')
        except (OSError, ActionError) as error:
            job.update(state='failed', error=str(error) if isinstance(error, ActionError) else '无法保存文件，请检查目录权限和磁盘空间。')
        finally:
            if process: await stop_process(process)
            if staging:
                if upload:
                    try: await self.remote(job['alias'], str(staging), 'cleanup')
                    except (ActionError, OSError): job['warning'] = '远端临时目录未能清理：' + str(staging)
                else: await asyncio.to_thread(shutil.rmtree, staging, True)
            job['finished_at'] = time.time()
            if job['state'] == 'cancelling': job['state'] = 'cancelled'

    def get(self, key):
        if key not in self.jobs: raise ActionError('传输记录不存在，服务可能已重启。', 'not_found', 404)
        return dict(self.jobs[key])

    def listing(self, alias):
        recent = sorted(self.jobs.values(), key=lambda job: job.get('finished_at', job['started_at']), reverse=True)
        return {'jobs': [dict(job) for job in recent if job['alias'] == alias]}

    async def cancel(self, key):
        self.get(key)
        task = self.tasks[key]
        if not task.done() and self.jobs[key]['state'] == 'running':
            self.jobs[key]['state'] = 'cancelling'
            task.cancel()
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            self.jobs[key]['state'] = 'cancelled'
            self.jobs[key]['finished_at'] = time.time()
        return self.get(key)

    async def close(self):
        self.closed = True
        for key in list(self.jobs): await self.cancel(key)
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def register_transfer_routes(app, transfers, perform, action_body):
    from fastapi import Request

    @app.post('/api/ssh/files')
    async def files(request: Request):
        async def run():
            data = await action_body(request, ('side', 'alias', 'path'))
            if data['side'] == 'remote': return await transfers.remote(data['alias'], data['path'])
            if data['side'] == 'local': return await asyncio.to_thread(local_listing, data['path'], False)
            raise ActionError('文件位置无效。')
        return await perform(run)

    @app.get('/api/ssh/transfers')
    async def jobs(alias: str):
        return transfers.listing(alias)

    @app.post('/api/ssh/transfers')
    async def start(request: Request):
        async def run():
            data = await action_body(request, ('alias', 'source', 'destination', 'direction'))
            return await transfers.start(**data)
        return await perform(run)

    @app.post('/api/ssh/transfers/{key}/cancel')
    async def cancel(key: str):
        return await perform(lambda: transfers.cancel(key))
