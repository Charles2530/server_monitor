"""Demand-loaded, read-only GPU ranking for all running ACP jobs in one site."""
import asyncio
import copy
import time
from urllib.parse import quote

import httpx

from .auth import load_session
from .acp_states import is_charles
from .collector import Collector, SourceError
from .domain import summarize_metrics
from .normalize import iso, metric_series, task_console_url, task_from_raw
from .task_history import timestamp
from .runtime_profile import get_runtime_profile


class UtilizationQueue:
    def __init__(self, site, transport=None, session_loader=None):
        self.site, self.transport = site, transport
        self.session_loader = session_loader or (lambda: load_session(site))
        self.reader = Collector(site)
        self.records = []
        self.status = 'loading'
        self.updated_at = None
        self.checked_at = None
        self.refresh_task = None
        self.slots = None

    async def gpu(self, client, task, row):
        if row['gpu_count'] == 0:
            row['metric_status'] = 'no_gpu'
            return
        if not isinstance(task.get('uid'), str) or not task['uid']:
            return
        async with self.slots:
            try:
                await asyncio.wait_for(self.read_gpu(client, task, row), 25)
            except asyncio.CancelledError:
                raise
            except Exception:
                row['metric_status'] = 'error'

    async def read_gpu(self, client, task, row):
        workers = await self.reader.paginate(client, 'AEC2', '/compute/acp/data/v2' + self.site.workspace
                                            + '/trainingJobs/' + quote(task['source_id'], safe='') + '/workers', 'workers')
        names = {worker['name'] for worker in workers if isinstance(worker.get('name'), str) and worker['name']}
        if not names:
            return
        now = time.time()
        body = {'query_filter': {'resource_id': [self.reader.workspace_uid], 'filters': {
            'label_acp_lepton_sensetime_com_job_name': task['uid'], 'exported_pod': '|'.join(sorted(names))}},
            'start': int(now) - 300, 'end': int(now), 'step': 60, 'range_align': True}
        payload = await self.reader.request(client, 'MONITOR', '/monitor/ts/data/v1' + self.reader.station
                                            + '/dashboards/acpnew/panels/WorkerGpuUtilTotal/metrics', body=body)
        if not isinstance(payload.get('metrics_result'), list):
            raise SourceError('GPU 指标格式异常')
        series = metric_series(payload, 'gpu', 'ACP')
        # Unknown/out-of-range values must never enter a low-utilization ranking.
        fresh = []
        for item in series:
            worker = item['label'].split(' · ', 1)[-1].split(' / GPU ', 1)[0]
            if worker not in names:
                continue
            if not item['points']:
                return
            stamp, value = max(item['points'], key=lambda point: point[0])
            if value is None or not 0 <= value <= 100 or not 0 <= now - stamp <= 300:
                return
            fresh.append((worker, stamp, value))
        if not fresh:
            return
        latest = max(stamp for _, stamp, _ in fresh)
        if any(latest - stamp > 60 for _, stamp, _ in fresh) or {worker for worker, _, _ in fresh} != names:
            return
        samples = [{'metric': 'gpu', 'points': [[stamp, value]]} for _, stamp, value in fresh]
        row.update(gpu_utilization=summarize_metrics(samples, now)['gpu'],
                   metric_updated_at=iso(latest), metric_status='ok')

    async def collect(self):
        # Python 3.9 binds semaphores at construction; the server loop starts later.
        if self.slots is None:
            self.slots = asyncio.Semaphore(get_runtime_profile()['queue_concurrency'])
        async with httpx.AsyncClient(headers=self.session_loader()['headers'], timeout=20,
                                     transport=self.transport, follow_redirects=False) as client:
            # All creation dates: do not inherit the console's recent-month filter.
            listing = asyncio.create_task(self.reader.paginate(
                client, 'AEC2', '/compute/acp/data/v2' + self.site.workspace + '/trainingJobs',
                'training_jobs', {'page_size': 100, 'filter': "state='RUNNING'"}))
            metadata = asyncio.create_task(self.reader.metadata(client))
            try:
                raw_rows = await listing
                tasks = {}
                for raw in raw_rows:
                    if (not isinstance(raw, dict) or raw.get('state') != 'RUNNING'
                            or not isinstance(raw.get('name'), str)
                            or raw.get('id') != self.site.workspace + '/trainingJobs/' + raw['name']):
                        continue
                    task = task_from_raw(raw, 'ACP', time.time())
                    url = task_console_url(task, self.site)
                    if not url:
                        continue
                    owner = (raw.get('ownership') or {}).get('user_name')
                    now = time.time()
                    created, started = timestamp(raw.get('create_time')), timestamp(raw.get('start_time'))
                    if created is not None and created > now:
                        created = None
                    if started is not None and (started > now or (created is not None and started < created)):
                        started = None
                    row = {key: task[key] for key in ('id', 'kind', 'name', 'cluster', 'gpu_count', 'replicas')}
                    row.update(owner=owner if isinstance(owner, str) else None, url=url,
                               created_at=iso(created) if created is not None else None,
                               started_at=iso(started) if started is not None else None,
                               is_mine=is_charles(task['name']),
                               gpu_utilization=None, metric_updated_at=None,
                               metric_status='no_gpu' if task['gpu_count'] == 0 else 'unknown')
                    tasks[task['id']] = (task, row)
                # Publish the list before slower telemetry reads, without stale utilization.
                self.records = [row for _, row in tasks.values()]
                self.updated_at = iso(time.time())
                self.status = 'ok'
                if any(row['gpu_count'] != 0 for row in self.records):
                    try:
                        await metadata
                    except Exception:
                        self.status = 'partial'
                        return
                    await asyncio.gather(*(self.gpu(client, task, row) for task, row in tasks.values()))
                if any(row['metric_status'] not in ('ok', 'no_gpu') for row in self.records):
                    self.status = 'partial'
            finally:
                if not metadata.done():
                    metadata.cancel()
                await asyncio.gather(metadata, return_exceptions=True)

    async def refresh(self):
        previous_update = self.updated_at
        try:
            await asyncio.wait_for(self.collect(), 90)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.status = 'partial' if self.updated_at != previous_update else 'error'
        finally:
            self.checked_at = time.monotonic()

    async def snapshot(self):
        loading = bool(self.refresh_task and not self.refresh_task.done())
        if not loading and (self.checked_at is None or time.monotonic() - self.checked_at >= 60):
            self.refresh_task = asyncio.create_task(self.refresh())
            loading = True
        tasks = copy.deepcopy(self.records)
        now = time.time()
        for row in tasks:
            started = timestamp(row.get('started_at'))
            observed = timestamp(self.updated_at) if self.status == 'error' else now
            row['duration_seconds'] = (int(observed - started) if started is not None
                                       and observed is not None and observed >= started else None)
            sampled = timestamp(row.get('metric_updated_at'))
            if row.get('gpu_utilization') is not None and (self.status == 'error' or sampled is None or not 0 <= now - sampled <= 300):
                row.update(gpu_utilization=None, metric_status='stale')
        tasks.sort(key=lambda row: (row['gpu_utilization'] is None,
                                   row['gpu_utilization'] if row['gpu_utilization'] is not None else 0, row['id']))
        status = self.status
        warnings = []
        if status == 'error':
            warnings.append('全用户 ACP 列表读取失败或权限不足；保留上次列表，任务状态可能已变化，旧利用率不参与排名。')
        elif any(row['metric_status'] not in ('ok', 'no_gpu') for row in tasks) and not loading:
            status = 'partial'
            warnings.append('部分任务 GPU 指标未获取、Worker 不完整或超过 5 分钟未更新，已标为未知并排在末尾。')
        return {'site': self.site.id, 'tasks': tasks, 'status': status, 'loading': loading,
                'updated_at': self.updated_at, 'warnings': warnings}

    async def close(self):
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
            await asyncio.gather(self.refresh_task, return_exceptions=True)
