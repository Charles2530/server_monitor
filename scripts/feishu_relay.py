#!/usr/bin/env python3
"""Private, read-only localhost monitor -> signed Feishu group notifications."""
import argparse
import base64
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

HOME = Path.home() / 'Library/Application Support/CharlesMonitorFeishu'
SITES = ('maoshanwang', 'sensecore')
KINDS = ('CCI', 'ACP')
FRESH_SECONDS = 300
TZ = timezone(timedelta(hours=8))
LABELS = {'running': '开始运行', 'succeeded': '成功', 'failed': '失败', 'stopped': '已停止'}
LOG = logging.getLogger('feishu-relay')


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.timestamp() if dt.tzinfo else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def fresh(stamp, now):
    return stamp is not None and -60 <= now - stamp <= FRESH_SECONDS


def source_time(snapshot, kind, now):
    source = snapshot.get('source_status', {}).get(kind, {})
    ts = timestamp(source.get('updated_at'))
    return ts if source.get('status') == 'ok' and fresh(ts, now) else None


def official_url(value):
    try:
        u = urlsplit(value or '')
        return value if (u.scheme == 'https' and not u.username and not u.password
                         and len(value) <= 2000 and u.port in (None, 443) and u.hostname in {
                             'console.sensecore.cn', 'console.ms-sc-01.maoshanwangtech.com'}) else ''
    except ValueError:
        return ''


def task_text(site, task, title):
    text = '[%s] %s\n%s\n类型：%s  集群：%s  GPU：%s' % (
        site, title, str(task.get('name', ''))[:300], task.get('kind', ''),
        str(task.get('cluster', '未知'))[:100], task.get('gpu_count', '未知'))
    link = official_url(task.get('url'))
    return text + ('\n官方任务：' + link if link else '')


def signed_payload(text, secret, now=None):
    ts = str(int(time.time() if now is None else now))
    sign = base64.b64encode(hmac.new((ts + '\n' + secret).encode(), b'', hashlib.sha256).digest()).decode()
    return {'timestamp': ts, 'sign': sign, 'msg_type': 'text', 'content': {'text': text}}


def check_response(response):
    code = response.get('code', response.get('StatusCode'))
    if type(code) is not int or code != 0:
        # Do not echo a remote error body or URL, which may contain credentials.
        raise RuntimeError('Feishu rejected message (code=%s)' % (code if type(code) is int else 'invalid'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_message(config, text):
    data = json.dumps(signed_payload(text, config['secret']), ensure_ascii=False).encode()
    if len(data) > 19000:
        raise ValueError('Message exceeds safe Feishu payload size')
    req = Request(config['webhook'], data=data, headers={'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
        check_response(json.load(response))


def get_snapshot(config, site, endpoint):
    req = Request(config.get('monitor_url', 'http://127.0.0.1:8766') + endpoint,
                  headers={'X-Monitor-Site': site})
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
        return json.load(response)


def read_config(path):
    if path.stat().st_mode & 0o077:
        raise ValueError('Config permissions must be 0600')
    config = json.loads(path.read_text())
    u = urlsplit(config['webhook'])
    if (u.scheme != 'https' or u.netloc != 'open.feishu.cn' or u.query or u.fragment
            or not u.path.startswith('/open-apis/bot/v2/hook/') or not config.get('secret')):
        raise ValueError('A signed official Feishu webhook is required')
    local = urlsplit(config.get('monitor_url', 'http://127.0.0.1:8766'))
    if local.scheme != 'http' or local.hostname != '127.0.0.1' or local.username or local.password or local.path or local.query or local.fragment:
        raise ValueError('Monitor URL must be a loopback HTTP origin')
    for key, default in [('low_gpu_seconds', 1800), ('cooldown_seconds', 21600)]:
        config.setdefault(key, default)
        if not isinstance(config[key], (int, float)) or config[key] < 60:
            raise ValueError('Invalid ' + key)
    config.setdefault('low_gpu_percent', 10)
    if not 0 <= config['low_gpu_percent'] <= 100:
        raise ValueError('Invalid GPU threshold')
    config.setdefault('daily_hour', 9)
    if config['daily_hour'] is not None and (type(config['daily_hour']) is not int or not 0 <= config['daily_hour'] <= 23):
        raise ValueError('Invalid daily hour')
    return config


class Relay:
    def __init__(self, path, config):
        self.config = config
        self.db = sqlite3.connect(str(path))
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL, sent REAL, dead INTEGER NOT NULL DEFAULT 0)')
        row = self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
        self.state = json.loads(row[0]) if row else {'tasks': {}, 'baselines': {}, 'daily_day': None}
        self.db.commit()

    def close(self):
        self.db.close()

    def save(self):
        self.db.execute('INSERT OR REPLACE INTO state VALUES (1,?)', (json.dumps(self.state),))

    def queue(self, text, now):
        text += '\n记录时间：' + datetime.fromtimestamp(now, TZ).strftime('%Y-%m-%d %H:%M:%S UTC+8')
        self.db.execute('INSERT INTO outbox(text,created,next_try) VALUES(?,?,?)', (text, now, now))

    def pending(self):
        return [dict(id=row[0], text=row[1]) for row in self.db.execute(
            'SELECT id,text FROM outbox WHERE sent IS NULL AND dead=0 ORDER BY id')]

    def observe(self, site, current, history, now):
        if site not in SITES:
            raise ValueError('Unknown monitor site')
        with self.db:
            self.state.setdefault('enrolled', now)
            present = set()
            for endpoint, snapshot in [('current', current), ('history', history)]:
                for kind in KINDS:
                    ts = source_time(snapshot, kind, now)
                    if ts is None:
                        continue
                    base = site + '/' + endpoint + '/' + kind
                    initialized = self.state['baselines'].get(base, False)
                    for task in snapshot.get('tasks', []):
                        if task.get('kind') != kind or 'charles' not in str(task.get('name', '')).lower() or not task.get('id'):
                            continue
                        status = 'running' if endpoint == 'current' else task.get('status')
                        if status not in LABELS:
                            continue
                        key = site + '/' + str(task['id'])
                        previous = self.state['tasks'].get(key)
                        if endpoint == 'current':
                            present.add(key)
                        # A slower endpoint must not overwrite a more recent observation.
                        if previous and ts < previous['source_at']:
                            continue
                        if not previous or previous['status'] != status:
                            ended = timestamp(task.get('ended_at'))
                            notify = bool(previous) or (initialized and status == 'running') or (
                                status != 'running' and ended is not None
                                and self.state['enrolled'] <= ended <= now + 60)
                            if notify:
                                self.queue(task_text(site, task, '任务' + LABELS[status]), now)
                            previous = {'status': status, 'source_at': ts, 'low_since': None,
                                        'low_sample': None, 'last_low_alert': None}
                            self.state['tasks'][key] = previous
                        previous['source_at'] = ts
                        previous['task'] = task
                        if endpoint == 'current':
                            self.low_gpu(site, task, previous, now)
                    self.state['baselines'][base] = True
            # Missing/error/stale samples break continuity, but never imply task completion.
            for key, record in self.state['tasks'].items():
                if key.startswith(site + '/') and key not in present:
                    record['low_since'] = record['low_sample'] = None
            self.save()

    def low_gpu(self, site, task, record, now):
        if not self.config.get('low_gpu_enabled', True):
            return
        value = task.get('metrics', {}).get('gpu')
        sampled = timestamp(task.get('metric_updated_at', {}).get('gpu'))
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                 and 0 <= value < self.config.get('low_gpu_percent', 10) and fresh(sampled, now)
                 and 'gpu' not in task.get('failed_metrics', []) and (task.get('gpu_count') or 0) > 0)
        if not valid:
            record['low_since'] = record['low_sample'] = None
            return
        last = record['low_sample']
        if last is None or sampled - last > FRESH_SECONDS or sampled < last:
            record['low_since'] = sampled
        record['low_sample'] = sampled
        last_alert = record['last_low_alert']
        if (sampled - record['low_since'] >= self.config.get('low_gpu_seconds', 1800)
                and (last_alert is None or now - last_alert >= self.config.get('cooldown_seconds', 21600))):
            minutes = self.config.get('low_gpu_seconds', 1800) / 60
            self.queue(task_text(site, task, 'GPU 低利用率提醒') +
                       '\nGPU %.1f%%，持续低于 %g%% 至少 %g 分钟。' % (value, self.config.get('low_gpu_percent', 10), minutes), now)
            record['last_low_alert'] = now

    def daily(self, snapshots, now):
        hour = self.config.get('daily_hour', 9)
        if hour is None:
            return
        dt = datetime.fromtimestamp(now, TZ)
        day = dt.date().isoformat()
        with self.db:
            if self.state['daily_day'] is None:
                # No catch-up summary when first installed after today's scheduled time.
                self.state['daily_day'] = day if dt.hour >= hour else (dt.date() - timedelta(days=1)).isoformat()
            if dt.hour >= hour and self.state['daily_day'] < day:
                lines = ['服务器监控每日汇总 · ' + day + '（UTC+8）']
                for site in SITES:
                    snapshot = snapshots.get(site, {})
                    valid = {kind for kind in KINDS if source_time(snapshot, kind, now) is not None}
                    tasks = [t for t in snapshot.get('tasks', []) if t.get('kind') in valid and 'charles' in str(t.get('name', '')).lower()]
                    lines.append('\n[%s] 已确认运行 %d 个任务，%d 张 GPU%s' % (
                        site, len(tasks), sum(t.get('gpu_count') or 0 for t in tasks),
                        '；部分数据不可用' if len(valid) < len(KINDS) else ''))
                    shown, budget = 0, 0
                    for task in tasks[:8]:
                        detail = task_text(site, task, '运行中')
                        size = len(json.dumps(detail, ensure_ascii=False).encode())
                        if budget + size > 6000:
                            break
                        lines.append(detail)
                        budget += size
                        shown += 1
                    if len(tasks) > shown:
                        lines.append('另有 %d 个任务，请在官方控制台查看。' % (len(tasks)-shown))
                self.queue('\n'.join(lines), now)
                self.state['daily_day'] = day
            self.save()

    def flush(self, sender, now):
        rows = self.db.execute('SELECT id,text,attempts,created,next_try FROM outbox WHERE sent IS NULL AND dead=0 ORDER BY id LIMIT 10').fetchall()
        for ident, text, attempts, created, next_try in rows:
            if now - created > 86400 or attempts >= 12:
                with self.db:
                    self.db.execute('UPDATE outbox SET dead=1 WHERE id=?', (ident,))
                LOG.error('Notification %d expired after failed delivery; inspect relay status', ident)
                continue
            if next_try > now:
                break  # Never deliver a completion before an earlier start in backoff.
            try:
                sender(text)
            except Exception as exc:
                with self.db:
                    self.db.execute('UPDATE outbox SET attempts=attempts+1,next_try=? WHERE id=?',
                                    (now + min(3600, 60 * 2 ** attempts), ident))
                LOG.warning('Notification %d delivery failed (%s); retry scheduled', ident, type(exc).__name__)
                # Stop the burst if a destination is currently unavailable.
                break
            else:
                with self.db:
                    self.db.execute('UPDATE outbox SET sent=? WHERE id=?', (now, ident))
                LOG.info('Notification %d delivered', ident)
        with self.db:
            self.db.execute('DELETE FROM outbox WHERE sent < ?', (now - 7 * 86400,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=HOME / 'config.json')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        config = read_config(args.config)
        if args.test:
            send_message(config, '服务器监控通知 · 接入测试\n签名验证成功。本机后台将转发 maoshanwang / sensecore 两站点 charles 任务状态变化。\n默认规则：GPU 低于 10% 持续 30 分钟，冷却 6 小时；每天北京时间 09:00 汇总。\n这是一条配置测试消息，不代表任务发生故障。')
            print('Signed Feishu test accepted (code=0)')
            return
        relay = Relay(args.config.parent / 'relay.sqlite3', config)
        if args.status:
            print(json.dumps({'baselines': relay.state['baselines'], 'known_tasks': len(relay.state['tasks']),
                              'pending': len(relay.pending()), 'dead': relay.db.execute('SELECT count(*) FROM outbox WHERE dead=1').fetchone()[0]}, ensure_ascii=False))
            relay.close()
            return
        # launchd enforces one job; flock also prevents accidental CLI duplicate sends.
        import fcntl
        lock = open(args.config.parent / 'relay.lock', 'a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            snapshots = {}
            for site in SITES:
                results = []
                for endpoint in ('/api/state', '/api/task-history'):
                    try:
                        results.append(get_snapshot(config, site, endpoint))
                    except Exception as exc:
                        LOG.warning('%s %s unavailable (%s)', site, endpoint, type(exc).__name__)
                        results.append({})
                snapshots[site] = results[0]
                relay.observe(site, results[0], results[1], time.time())
            now = time.time()
            relay.daily(snapshots, now)
            def sender(text):
                send_message(config, text)
                time.sleep(1)  # Below the bot's per-second and per-minute rate limits.
            relay.flush(sender, now)
            if args.once:
                relay.close()
                return
            time.sleep(60)
    except Exception as exc:
        LOG.error('Relay stopped (%s); check config or service status', type(exc).__name__)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
