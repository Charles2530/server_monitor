"""On-demand, read-only SSH host checks. Remote output and SSH traces stay private."""
import asyncio
import json
import math
import shlex
import socket
import time

from .acp_actions import ActionError


MARKER = b'ILAB_HEALTH_V1\n'
REMOTE_SCRIPT = r'''
import csv, io, json, os, platform, subprocess, time
from pathlib import Path

PROC = Path('/proc')

def read(name):
    try: return (PROC / name).read_text()
    except OSError: return ''

def run(args):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, env={**os.environ, 'LC_ALL': 'C'})
        return result.stdout.decode(errors='replace') if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired): return ''

def counters():
    cpu = read('stat').splitlines()
    values = [int(v) for v in cpu[0].split()[1:9]] if cpu and cpu[0].startswith('cpu ') else []
    disks = {}
    for line in read('diskstats').splitlines():
        fields = line.split()
        if len(fields) < 14: continue
        name = fields[2]
        if name.startswith(('loop', 'ram', 'zram', 'sr', 'fd')) or (Path('/sys/class/block') / name / 'partition').exists(): continue
        disks[name] = (int(fields[5]) * 512, int(fields[9]) * 512)
    return values, disks

def collect():
    data = dict(system=platform.system(), release=platform.release(), architecture=platform.machine(), notes=[])
    if data['system'] != 'Linux':
        data['notes'].append('unsupported_os'); return data
    data['cpu_count'] = os.cpu_count()
    data['load'] = list(os.getloadavg())
    uptime = read('uptime').split()
    data['uptime_seconds'] = float(uptime[0]) if uptime else None
    before, disk_before = counters(); started = time.monotonic()
    time.sleep(.5)
    after, disk_after = counters(); elapsed = time.monotonic() - started
    total = sum(after) - sum(before)
    idle = sum(after[3:5]) - sum(before[3:5])
    data['cpu_percent'] = round(100 * (total-idle) / total, 1) if before and after and total > 0 else None
    data['io'] = [dict(device=name, read_bps=max(0, counts[0]-disk_before[name][0])/elapsed,
                       write_bps=max(0, counts[1]-disk_before[name][1])/elapsed)
                  for name, counts in disk_after.items() if name in disk_before][:32]
    memory = {}
    for line in read('meminfo').splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit(): memory[fields[0].rstrip(':')] = int(fields[1]) * 1024
    data['memory'] = dict(total_bytes=memory.get('MemTotal'), available_bytes=memory.get('MemAvailable'),
                          swap_total_bytes=memory.get('SwapTotal'), swap_free_bytes=memory.get('SwapFree'))
    inodes = {}
    for line in run(['df', '-P', '-i', '-l']).splitlines()[1:]:
        fields = line.split(None, 5)
        if len(fields) == 6:
            try: inodes[fields[5]] = float(fields[4].rstrip('%'))
            except ValueError: pass
    data['disks'] = []
    for line in run(['df', '-P', '-k', '-l']).splitlines()[1:]:
        fields = line.split(None, 5)
        if len(fields) != 6 or fields[0] in ('tmpfs', 'devtmpfs', 'shm'): continue
        try:
            data['disks'].append(dict(mount=fields[5], total_bytes=int(fields[1])*1024,
                used_bytes=int(fields[2])*1024, available_bytes=int(fields[3])*1024,
                used_percent=float(fields[4].rstrip('%')), inode_percent=inodes.get(fields[5])))
        except ValueError: pass
        if len(data['disks']) >= 64: break
    data['gpus'] = []
    gpu = run(['nvidia-smi', '--query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit', '--format=csv,noheader,nounits'])
    for row in csv.reader(io.StringIO(gpu)):
        if len(row) != 8: continue
        def number(value):
            try: return float(value.strip())
            except ValueError: return None
        data['gpus'].append(dict(zip(('index','name','temperature_c','utilization_percent','memory_used_mib','memory_total_mib','power_w','power_limit_w'),
                                    [number(row[0]), row[1].strip()] + [number(v) for v in row[2:]])))
        if len(data['gpus']) >= 32: break
    if not gpu: data['notes'].append('gpu_unavailable')
    return data

if __name__ == '__main__': print(json.dumps(collect(), allow_nan=False))
'''


def number(value, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or maximum is not None and value > maximum:
        return None
    return round(value, 2)


def normalize_health(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('system'), str):
        raise ValueError('invalid health report')
    text = lambda value: ''.join(c for c in str(value or '') if c.isprintable())[:180]
    health = {key: text(raw.get(key)) for key in ('system', 'release', 'architecture')}
    health.update({key: number(raw.get(key)) for key in ('cpu_count', 'uptime_seconds')})
    health['cpu_percent'] = number(raw.get('cpu_percent'), 100)
    health['load'] = [number(v) for v in raw.get('load', [])[:3]] if isinstance(raw.get('load'), list) else []
    mem = raw.get('memory') if isinstance(raw.get('memory'), dict) else {}
    memory = {key: number(mem.get(key)) for key in ('total_bytes', 'available_bytes', 'swap_total_bytes', 'swap_free_bytes')}
    for target, total_key, free_key in (('used_percent', 'total_bytes', 'available_bytes'), ('swap_percent', 'swap_total_bytes', 'swap_free_bytes')):
        total, free = memory[total_key], memory[free_key]
        memory[target] = round(100 * (total - free) / total, 1) if total and free is not None and free <= total else None
    health['memory'] = memory
    health['disks'], health['gpus'], health['io'] = [], [], []
    for collection, label, fields, limit in (
        ('disks', 'mount', ('total_bytes', 'used_bytes', 'available_bytes', 'used_percent', 'inode_percent'), 64),
        ('gpus', 'name', ('index', 'temperature_c', 'utilization_percent', 'memory_used_mib', 'memory_total_mib', 'power_w', 'power_limit_w'), 32),
        ('io', 'device', ('read_bps', 'write_bps'), 32),
    ):
        rows = raw.get(collection) if isinstance(raw.get(collection), list) else []
        for row in rows[:limit]:
            if not isinstance(row, dict): continue
            health[collection].append({label: text(row.get(label)), **{key: number(row.get(key), 100 if 'percent' in key else None) for key in fields}})
    notes = raw.get('notes') if isinstance(raw.get('notes'), list) else []
    health['notes'] = [note for note in ('unsupported_os', 'gpu_unavailable') if note in notes]
    issues = []
    for key, threshold, label in (('used_percent', 90, '内存使用率达到 90%'), ('swap_percent', 75, 'Swap 使用率达到 75%')):
        if memory[key] is not None and memory[key] >= threshold: issues.append(label)
    for disk in health['disks']:
        for key, label in (('used_percent', '空间'), ('inode_percent', 'inode')):
            if disk[key] is not None and disk[key] >= 90: issues.append(f"{disk['mount']} {label}使用率达到 90%")
    for gpu in health['gpus']:
        if gpu['temperature_c'] is not None and gpu['temperature_c'] >= 85: issues.append(f"GPU {gpu['index']} 温度达到 85°C")
    return health, issues


async def resolve(hostname, port):
    records = await asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys((record[4][0], record[4][1]) for record in records))[:6]


async def connect(addresses):
    for address, port in addresses:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), 1.5)
            writer.close()
            await writer.wait_closed()
            return
        except (OSError, asyncio.TimeoutError):
            continue
    raise OSError('no reachable address')


class SSHHealth:
    def __init__(self, catalog, runner):
        self.catalog, self.runner = catalog, runner
        self.results, self.tasks, self.started = {}, {}, {}

    async def snapshot(self, alias):
        host = await self.catalog.require('SSH:' + str(alias))
        cached = self.results.get(alias, {})
        return {'alias': alias, 'checking': alias in self.tasks and not self.tasks[alias].done(),
                'result': cached.get('result') if cached.get('config_id') == host.get('config_id') else None}

    async def refresh(self, alias):
        data = await self.snapshot(alias)
        if data['checking']: return data
        host = await self.catalog.require('SSH:' + alias)
        if data['result'] and time.monotonic() - self.started.get(alias, 0) < 10: return data
        if sum(not task.done() for task in self.tasks.values()) >= 2:
            raise ActionError('最多同时体检两台服务器，请稍后重试。', 'health_busy', 409)
        self.started[alias] = time.monotonic()
        self.tasks[alias] = asyncio.create_task(self.check(host))
        return {**data, 'checking': True}

    async def check(self, host):
        steps = []
        def step(key, label, status, message, started=None):
            steps.append({'key': key, 'label': label, 'status': status, 'message': message,
                          'latency_ms': round((time.monotonic()-started)*1000) if started is not None else None})
        step('config', 'SSH 配置', 'ok', '已读取本机有效配置' + ('，使用跳板或代理路径' if host.get('route') == 'proxy' else '，直接连接'))
        if host.get('route') == 'proxy':
            for key, label in (('dns', 'DNS 解析'), ('tcp', 'TCP 端口')):
                step(key, label, 'skipped', '由跳板／代理路径处理，不以本机直连结果判断目标')
        else:
            addresses, started = [], time.monotonic()
            try:
                addresses = await asyncio.wait_for(resolve(host['hostname'], host['port']), 3)
                if not addresses: raise OSError('no address')
                step('dns', 'DNS 解析', 'ok', f'获得 {len(addresses)} 个候选地址', started)
            except (OSError, asyncio.TimeoutError): step('dns', 'DNS 解析', 'error', '无法解析或解析超时，请检查主机名与本机 DNS', started)
            if addresses:
                started = time.monotonic()
                try:
                    await asyncio.wait_for(connect(addresses), 3)
                    step('tcp', 'TCP 端口', 'ok', f"端口 {host['port']} 可建立连接", started)
                except (OSError, asyncio.TimeoutError): step('tcp', 'TCP 端口', 'error', '连接被拒绝、不可达或超时，请检查网络、端口与防火墙', started)
            else: step('tcp', 'TCP 端口', 'skipped', '未取得目标地址')
        started = time.monotonic()
        code, output, error = 255, b'', b''
        try:
            remote = "printf 'ILAB_HEALTH_V1\\n'; python3 -I -B -c " + shlex.quote(REMOTE_SCRIPT)
            code, output, error = await self.runner('/usr/bin/ssh', '-T', *self.catalog.options(), host['alias'], remote, timeout=20, capture_stderr=True)
        except asyncio.TimeoutError: error = b'timed out'
        except OSError: pass
        message = error.decode(errors='replace').lower()
        executed = MARKER in output
        execution_denied = any(phrase in message for phrase in ('exec request failed on channel ', 'shell request failed on channel '))
        authenticated = executed or execution_denied and host.get('route') != 'proxy'
        authentication = 'permission denied' in message or 'no supported authentication' in message
        if authenticated or authentication:
            step('ssh', 'SSH 连接与主机校验', 'ok', ('已到达 SSH 认证阶段，代理路径可能停在跳板' if host.get('route') == 'proxy' else '已到达 SSH 认证阶段') if not authenticated else 'SSH 会话已建立', started)
        else:
            reason = ('主机密钥校验失败，请核对服务器指纹' if 'host key verification failed' in message or 'remote host identification has changed' in message
                      else 'SSH 连接超时，请检查目标或跳板路径' if 'timed out' in message
                      else '目标或跳板拒绝执行命令，未能确认目标会话' if execution_denied
                      else 'SSH 连接未建立，请检查目标、跳板配置或协议兼容性')
            step('ssh', 'SSH 连接与主机校验', 'error', reason, started)
        step('auth', 'SSH 身份认证', 'ok' if authenticated else 'error' if authentication else 'skipped',
             '配置的身份认证成功' if authenticated else '认证失败，请检查 SSH key、agent 和目标用户；代理路径也可能在跳板处失败' if authentication else '未能确认目标认证结果')
        execution_message = ('目标拒绝执行命令，请检查账号 Shell 与服务端会话策略' if authenticated
                             else '命令执行请求被拒绝，可能发生在跳板；请检查跳板与目标的会话策略')
        step('exec', '远端命令执行', 'ok' if executed else 'error' if execution_denied else 'skipped',
             '已验证远端 Shell 可执行固定只读命令' if executed else execution_message if execution_denied else '未建立可执行远端命令的会话')
        health, issues = None, []
        if executed:
            try:
                payload = output.rsplit(MARKER, 1)[-1]
                if code or len(payload) > 131072: raise ValueError()
                health, issues = normalize_health(json.loads(payload))
            except (ValueError, TypeError, UnicodeDecodeError): pass
        complete = (health and health['system'] == 'Linux' and health['cpu_percent'] is not None
                    and health['memory']['used_percent'] is not None and health['disks']
                    and all(disk['inode_percent'] is not None for disk in health['disks']))
        status = 'unavailable' if not authenticated else 'warning' if issues else 'partial' if not complete or any(s['status'] == 'error' for s in steps) else 'healthy'
        result = {'checked_at': time.time(), 'status': status, 'steps': steps, 'health': health, 'issues': issues,
                  'message': execution_message if execution_denied and not executed else '远端指标未获取：请确认 Linux、python3 和读取权限；无需安装 agent。' if executed and not health else ''}
        self.results[host['alias']] = {'config_id': host.get('config_id'), 'result': result}

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
