"""Demand-loaded GPU ranking for all running CCI and ACP tasks in one site."""
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
        self.sources = {kind: {'status': 'loading', 'updated_at': None} for kind in ('CCI', 'ACP')}
        self.status = 'loading'
        self.updated_at = None
        self.checked_at = None
        self.refresh_task = None
        self.slots = None

    async def cci_start(self, client, task, row):
        try:
            async with self.slots:
                instances = await asyncio.wait_for(self.reader.paginate(client, 'CCI', '/compute/cci/data/v2'
                    + self.site.workspace + '/apps/' + quote(task['source_id'], safe='') + '/instances',
                    'instances'), 15)
            now, created = time.time(), timestamp(row.get('created_at'))
            starts = []
            for instance in instances:
                if instance.get('app_name') != task['source_id'] or instance.get('state') != 'RUNNING':
                    continue
                started = timestamp(instance.get('last_started_time'))
                if started is not None and started <= now and (created is None or started >= created):
                    starts.append(started)
            if starts:
                row['started_at'] = iso(min(starts))
        except Exception:
            pass  # Instance details are optional; retain the creation-time estimate.

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
        kind = task['kind']
        names = None
        if kind == 'ACP':
            workers = await self.reader.paginate(client, 'AEC2', '/compute/acp/data/v2' + self.site.workspace
                                                + '/trainingJobs/' + quote(task['source_id'], safe='') + '/workers', 'workers')
            names = {worker['name'] for worker in workers if isinstance(worker.get('name'), str) and worker['name']}
            if not names:
                return
            filters = {'label_acp_lepton_sensetime_com_job_name': task['uid'], 'exported_pod': '|'.join(sorted(names))}
        else:
            filters = {'label_cci_lepton_sensetime_com_deployment_name': task['uid'], 'exported_pod': '.*', 'gpu': '.*'}
        now = time.time()
        body = {'query_filter': {'resource_id': [self.reader.workspace_uid], 'filters': filters},
                'start': int(now) - 300, 'end': int(now), 'step': 60, 'range_align': True}
        panel = 'cdi/panels/DeploymentGpuUtilTotal' if kind == 'CCI' else 'acpnew/panels/WorkerGpuUtilTotal'
        payload = await self.reader.request(client, 'MONITOR', '/monitor/ts/data/v1' + self.reader.station
                                            + '/dashboards/' + panel + '/metrics', body=body)
        if not isinstance(payload.get('metrics_result'), list):
            raise SourceError('GPU 指标格式异常')
        series = metric_series(payload, 'gpu', kind)
        # Unknown/out-of-range values must never enter a low-utilization ranking.
        fresh = []
        for item in series:
            worker = item['label'].split(' · ', 1)[-1].split(' / GPU ', 1)[0]
            if names is not None and worker not in names:
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
        if any(latest - stamp > 60 for _, stamp, _ in fresh) or (names is not None and {worker for worker, _, _ in fresh} != names):
            return
        samples = [{'metric': 'gpu', 'points': [[stamp, value]]} for _, stamp, value in fresh]
        row.update(gpu_utilization=summarize_metrics(samples, now)['gpu'],
                   metric_updated_at=iso(latest), metric_status='ok')

    async def collect_kind(self, client, kind, metadata):
        resource, key, service = ('apps', 'apps', 'CCI') if kind == 'CCI' else ('trainingJobs', 'training_jobs', 'AEC2')
        previous = self.sources[kind]
        self.sources[kind] = {**previous, 'status': 'loading'}
        try:
            raw_rows = await self.reader.paginate(client, service, '/compute/' + kind.lower() + '/data/v2'
                + self.site.workspace + '/' + resource, key,
                {'page_size': 20 if kind == 'CCI' else 100, 'filter': "state='RUNNING'"})
            tasks = {}
            for raw in raw_rows:
                if (not isinstance(raw, dict) or raw.get('state') != 'RUNNING'
                        or not isinstance(raw.get('name'), str)
                        or raw.get('id') != self.site.workspace + '/' + resource + '/' + raw['name']):
                    continue
                task = task_from_raw(raw, kind, time.time())
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
                row = {key: task[key] for key in ('id', 'kind', 'name', 'cluster', 'gpu_count', 'replicas', 'can_stop', 'state')}
                row.update(owner=owner if isinstance(owner, str) else None, url=url, observed_at=iso(now),
                           created_at=iso(created) if created is not None else None,
                           started_at=iso(started) if started is not None else None,
                           is_mine=is_charles(task['name']),
                           gpu_utilization=None, metric_updated_at=None,
                           metric_status='no_gpu' if task['gpu_count'] == 0 else 'unknown')
                tasks[task['id']] = (task, row)
            # Publish each source independently before telemetry; preserve the other source.
            self.records = [row for row in self.records if row['kind'] != kind] + [row for _, row in tasks.values()]
            self.updated_at = iso(time.time())
            self.sources[kind] = {'status': 'ok', 'updated_at': self.updated_at}
        except asyncio.CancelledError:
            self.sources[kind] = {**previous, 'status': 'error'}
            raise
        except Exception:
            self.sources[kind] = {**previous, 'status': 'error'}
            return
        if kind == 'CCI':
            await asyncio.gather(*(self.cci_start(client, task, row) for task, row in tasks.values()))
        if any(row['gpu_count'] != 0 for _, row in tasks.values()):
            try:
                await asyncio.shield(metadata)
            except Exception:
                return
            await asyncio.gather(*(self.gpu(client, task, row) for task, row in tasks.values()))

    async def collect(self):
        # Python 3.9 binds semaphores at construction; the server loop starts later.
        if self.slots is None:
            self.slots = asyncio.Semaphore(get_runtime_profile()['queue_concurrency'])
        async with httpx.AsyncClient(headers=self.session_loader()['headers'], timeout=20,
                                     transport=self.transport, follow_redirects=False) as client:
            metadata = asyncio.create_task(self.reader.metadata(client))
            try:
                await asyncio.gather(*(self.collect_kind(client, kind, metadata) for kind in self.sources))
                errors = sum(source['status'] != 'ok' for source in self.sources.values())
                self.status = 'error' if errors == len(self.sources) else 'partial' if errors or any(
                    row['metric_status'] not in ('ok', 'no_gpu') for row in self.records) else 'ok'
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

    async def snapshot(self, force=False):
        loading = bool(self.refresh_task and not self.refresh_task.done())
        if not loading and (force or self.checked_at is None or time.monotonic() - self.checked_at >= 60):
            self.refresh_task = asyncio.create_task(self.refresh())
            loading = True
        tasks = copy.deepcopy(self.records)
        now = time.time()
        for row in tasks:
            started = timestamp(row.get('started_at'))
            estimated = timestamp(row.get('created_at')) if row.get('kind') == 'CCI' and started is None else None
            row['duration_estimated'] = estimated is not None
            started = started if started is not None else estimated
            source = self.sources.get(row.get('kind'), {})
            observed_at = timestamp(source.get('updated_at') or self.updated_at)
            current = (self.status != 'error' and source.get('status') == 'ok'
                       and observed_at is not None and 0 <= now - observed_at <= 180)
            row['can_stop'] = bool(row.get('can_stop') and current)
            observed = now if current else observed_at
            row['duration_seconds'] = (int(observed - started) if started is not None
                                       and observed is not None and observed >= started else None)
            sampled = timestamp(row.get('metric_updated_at'))
            if row.get('gpu_utilization') is not None and (not current or sampled is None or not 0 <= now - sampled <= 300):
                row.update(gpu_utilization=None, metric_status='stale')
        tasks.sort(key=lambda row: (row['gpu_utilization'] is None,
                                   row['gpu_utilization'] if row['gpu_utilization'] is not None else 0, row['id']))
        status = self.status
        warnings = [kind + ' 列表读取失败或权限不足；保留上次结果，旧利用率不参与排名，停止操作暂不可用。'
                    for kind, source in self.sources.items() if source['status'] == 'error']
        if any(row['metric_status'] not in ('ok', 'no_gpu') for row in tasks) and not loading:
            if status != 'error': status = 'partial'
            warnings.append('部分任务 GPU 指标未获取、Worker 不完整或超过 5 分钟未更新，已标为未知并排在末尾。')
        return {'site': self.site.id, 'tasks': tasks, 'status': status, 'loading': loading,
                'updated_at': self.updated_at, 'source_status': copy.deepcopy(self.sources), 'warnings': warnings}

    async def close(self):
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
            await asyncio.gather(self.refresh_task, return_exceptions=True)
