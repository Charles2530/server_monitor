"""Personal running metrics, all-user startup queues and cluster capacity reads."""
import asyncio
import json
import time
from urllib.parse import quote
import httpx
from .auth import AuthRequired, load_session
from .inventory import enrich_clusters
from .config import SERVICES, WORKSPACE
from .domain import is_target, summarize_metrics, merge_series
from .normalize import task_from_raw, waiting_from_raw, cluster_from_raw, metric_series, iso
from .acp_states import is_charles
from .queue_states import QUEUE_LABELS

PANELS = {'cpu':'CpuUtilization','memory':'MemUtilization','gpu':'GpuUtilTotal','gpu_memory':'GpuMemUtilTotal'}

class SourceError(Exception):
    pass

class Collector:
    def __init__(self, site=None):
        self.site = site
        self.workspace = site.workspace if site else WORKSPACE
        self.services = site.services if site else SERVICES
        self.semaphore = asyncio.Semaphore(6)
        # Leave two of the six request slots for node inventory/allocation;
        # otherwise queued metric panels can exhaust its short timeout before
        # an observation request even reaches the cloud.
        self.metric_semaphore = asyncio.Semaphore(4)
        self.workspace_uid = None
        self.station = None

    async def request(self, client, service, path, *, body=None, params=None):
        for attempt in range(3):
            try:
                async with self.semaphore:
                    response = await client.request('POST' if body is not None else 'GET', self.services[service]+path, json=body, params=params)
                if response.status_code in (401,403):
                    raise AuthRequired('云会话已失效或权限不足，请重新连接。')
                if 400 <= response.status_code < 500 and response.status_code not in (408,429):
                    raise SourceError('云接口请求失败，请稍后重试。')
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise SourceError('数据源返回格式异常。')
                return result
            except (httpx.HTTPError, ValueError):
                if attempt == 2:
                    raise SourceError('云接口请求失败，请稍后重试。') from None
            # A failing endpoint must not occupy a request slot while backing off.
            await asyncio.sleep(1 + attempt)

    async def paginate(self, client, service, path, key, extra_params=None):
        result, token, seen = [], '1', set()
        while token:
            if token in seen:
                raise SourceError('云接口分页重复，已停止采集以避免遗漏误报。')
            seen.add(token)
            payload = await self.request(client,service,path,params={'page_size':100,**(extra_params or {}),'page_token':token})
            if not isinstance(payload.get(key), list):
                raise SourceError('数据源列表格式异常。')
            result.extend(payload[key])
            token = str(payload.get('next_page_token') or '')
        return result

    async def metadata(self, client):
        if not self.workspace_uid:
            data = await self.request(client,'AEC2','/compute/workspace/data/v1'+self.workspace)
            self.workspace_uid = data.get('uid')
            if not self.workspace_uid:
                raise SourceError('工作空间标识缺失。')
        if not self.station:
            data = await self.paginate(client,'MANAGEMENT','/rmh/v1/resources','resources',{'filter':'resource_type="monitor.ts.v1.telemetryStation"'})
            prefix = self.workspace.split('/workspaces/')[0] + '/telemetryStations/'
            for item in data:
                try:
                    props = json.loads(item.get('properties','{}'))
                except (ValueError,TypeError):
                    continue
                if props.get('station_type') == 'TENANT' and item.get('rid','').startswith(prefix):
                    self.station = item['rid']
                    break
            if not self.station:
                raise SourceError('没有找到本工作空间的租户监控站。')

    async def task_metrics(self, client, task, now):
        kind = task['kind']
        errors = []
        failed_metrics = set()
        # CPU-only tasks have no GPU series. Missing device data is not a
        # collection failure; unknown GPU counts still require all panels.
        panels = {key:value for key,value in PANELS.items()
                  if task.get('gpu_count') != 0 or key not in ('gpu','gpu_memory')}
        if kind == 'ACP':
            async with self.metric_semaphore:
                workers = await self.paginate(client,'AEC2','/compute/acp/data/v2'+self.workspace+'/trainingJobs/'+quote(task['source_id'],safe='')+'/workers','workers')
            task['workers'] = [w['name'] for w in workers if w.get('name')]
            if not task['workers']:
                raise SourceError('任务尚未返回 worker。')
            filters = {'label_acp_lepton_sensetime_com_job_name':task['uid'], 'exported_pod':'|'.join(task['workers'])}
        else:
            filters = {'label_cci_lepton_sensetime_com_deployment_name':task['uid'],'exported_pod':'.*','gpu':'.*'}
        async def metric(key, suffix):
            dashboard = 'cdi' if kind=='CCI' else 'acpnew'
            panel = ('Deployment' if kind=='CCI' else 'Worker') + suffix
            body={'query_filter':{'resource_id':[self.workspace_uid],'filters':filters},'start':int(now)-3600,'end':int(now),'step':60,'range_align':True}
            async with self.metric_semaphore:
                payload = await self.request(client,'MONITOR','/monitor/ts/data/v1'+self.station+'/dashboards/'+dashboard+'/panels/'+panel+'/metrics',body=body)
            if 'metrics_result' not in payload:
                raise SourceError('监控接口返回格式异常。')
            series = metric_series(payload,key,kind)
            if kind == 'ACP':
                for worker in task['workers']:
                    worker_series = [item for item in series if item['label'].split(' · ',1)[-1].split(' / GPU ',1)[0] == worker]
                    if summarize_metrics(worker_series,now).get(key) is None:
                        errors.append('worker 指标缺失或过期：'+worker)
                        failed_metrics.add(key)
            return series
        results = await asyncio.gather(*(metric(k,v) for k,v in panels.items()),return_exceptions=True)
        for key,result in zip(panels,results):
            if isinstance(result,AuthRequired):
                raise result
            if isinstance(result,Exception):
                errors.append('部分监控指标获取失败')
                failed_metrics.add(key)
            else:
                task['series'].extend(result)
        task['metrics']=summarize_metrics(task['series'],now)
        times = [p[0] for s in task['series'] for p in s['points'] if p[1] is not None]
        task['updated_at']=iso(max(times)) if times else None
        if any(task['metrics'].get(key) is None for key in panels):
            errors.append('部分指标缺失或超过 5 分钟未更新')
        failed_metrics.update(k for k in panels if task['metrics'].get(k) is None)
        task['failed_metrics']=sorted(failed_metrics)
        task['metric_updated_at']={}
        for key in PANELS:
            valid_times=[p[0] for item in task['series'] if item['metric']==key for p in item['points'] if p[1] is not None]
            task['metric_updated_at'][key]=iso(max(valid_times)) if valid_times and key not in failed_metrics else None
            if key in failed_metrics:
                task['metrics'][key]=None
        task['metric_error']='；'.join(sorted(set(errors))) or None

    async def collect(self, previous=None):
        previous = previous or {}
        now=time.time()
        headers=(load_session(self.site) if self.site else load_session())['headers']
        state={'tasks':[], 'waiting_tasks':[], 'clusters':[], 'source_status':{}, 'updated_at':iso(now),'connected':True,'status':'ok','error':None}
        async with httpx.AsyncClient(headers=headers,timeout=25,follow_redirects=False) as client:
            metadata_error = None
            try:
                await self.metadata(client)
            except AuthRequired:
                raise
            except Exception:
                metadata_error = '监控元数据暂不可用'
            own_acp_names=set()
            async def source(kind):
                if kind=='CCI':
                    rows=await self.paginate(client,'CCI','/compute/cci/data/v2'+self.workspace+'/appsOwn','apps',{'filter':"state='RUNNING'"})
                else:
                    states=('RUNNING',*QUEUE_LABELS['ACP'])
                    rows=await self.paginate(client,'AEC2','/compute/acp/data/v2'+self.workspace+'/trainingJobsOwn','training_jobs',
                        {'filter':' OR '.join("state='"+value+"'" for value in states)})
                    own_acp_names.update(r['name'] for r in rows if is_charles(r.get('display_name')))
                return [task_from_raw(r,kind,now) for r in rows if is_target(r.get('display_name',''),r.get('state'))]
            async def queue(kind,service,path,key):
                # Filter before pagination: full CCI records can exceed the cloud's 4 MiB limit.
                params={'page_size':20,'filter':' OR '.join("state='"+value+"'" for value in QUEUE_LABELS[kind])}
                return await self.paginate(client,service,path,key,params)
            async def read_list(read,auth_fatal=True):
                try:
                    return await read
                except AuthRequired as error:
                    if auth_fatal:raise
                    return error
                except Exception as error:
                    return error
            # Read independent lists together, then assemble in the original order.
            # In particular, ACP queue permissions need the completed personal list.
            reads=[asyncio.create_task(read_list(read,auth_fatal)) for read,auth_fatal in (
                (source('CCI'),True),(source('ACP'),True),
                (queue('CCI','CCI','/compute/cci/data/v2'+self.workspace+'/apps','apps'),False),
                (queue('ACP','AEC2','/compute/acp/data/v2'+self.workspace+'/trainingJobs','training_jobs'),False),
                (self.paginate(client,'AEC2','/compute/workspace/data/v1'+self.workspace+'/workspaceAEC2Bindings','aec2s'),True))]
            try:
                lists=await asyncio.gather(*reads)
            finally:
                # Authentication failure or cancellation must drain siblings before
                # leaving the shared HTTP client context.
                for read in reads:
                    if not read.done():read.cancel()
                await asyncio.gather(*reads,return_exceptions=True)
            for kind,tasks in zip(('CCI','ACP'),lists[:2]):
                try:
                    if isinstance(tasks,BaseException):raise tasks
                    state['tasks'].extend(tasks)
                    state['source_status'][kind]={'status':'ok','updated_at':iso(now)}
                except AuthRequired:
                    raise
                except Exception:
                    state['tasks'].extend(t for t in previous.get('tasks',[]) if t['kind']==kind)
                    state['source_status'][kind]={'status':'error','error':'任务列表获取失败，保留旧快照','updated_at':previous.get('source_status',{}).get(kind,{}).get('updated_at')}
            for kind,rows in zip(('CCI','ACP'),lists[2:4]):
                source_key=kind+'_queue'
                try:
                    if isinstance(rows,BaseException):raise rows
                    waiting=[]
                    for row in rows:
                        if row.get('state') not in QUEUE_LABELS[kind]:
                            continue
                        task=waiting_from_raw(row,now,kind)
                        task['can_stop']=task['can_stop'] and row['name'] in own_acp_names
                        waiting.append(task)
                    state['waiting_tasks'].extend(waiting)
                    state['source_status'][source_key]={'status':'ok','updated_at':iso(now)}
                except Exception:
                    # All-user list permission failures must not invalidate personal monitoring.
                    state['waiting_tasks'].extend(t for t in previous.get('waiting_tasks',[]) if t.get('kind')==kind and t.get('state') in QUEUE_LABELS[kind])
                    state['source_status'][source_key]={'status':'error','error':'全用户等待队列获取失败或权限不足，保留旧快照','updated_at':previous.get('source_status',{}).get(source_key,{}).get('updated_at')}
            try:
                rows=lists[4]
                if isinstance(rows,BaseException):raise rows
                state['clusters']=[cluster_from_raw(r,now) for r in rows if r.get('state')=='ACTIVE']
                state['source_status']['resources']={'status':'ok','updated_at':iso(now)}
            except AuthRequired:
                raise
            except Exception:
                state['clusters']=previous.get('clusters',[])
                state['source_status']['resources']={'status':'error','error':'集群余量获取失败，保留旧快照'}
            fresh=[t for t in state['tasks'] if state['source_status'][t['kind']]['status']=='ok']
            async def metric_results():
                return await asyncio.gather(*(self.task_metrics(client,t,now) for t in fresh),return_exceptions=True) if not metadata_error else [SourceError(metadata_error)]*len(fresh)
            _,results = await asyncio.gather(enrich_clusters(self.request,client,state,previous,now,targets=self.site.inventory_clusters if self.site else None,all_bound=bool(self.site and self.site.inventory_clusters is None)),metric_results())
            old_tasks={t['id']:t for t in previous.get('tasks',[])}
            for task,result in zip(fresh,results):
                if isinstance(result,AuthRequired):
                    raise result
                if isinstance(result,Exception):
                    task['metric_error']='监控指标暂不可用'
                if isinstance(result,Exception):
                    task['failed_metrics']=list(PANELS)
                if task.get('metric_error') and task['id'] in old_tasks:
                    old = old_tasks[task['id']]
                    task['series']=merge_series(old.get('series',[]),task['series'])
                    for series in task['series']:
                        series['points']=[point for point in series['points'] if now-3600 <= point[0] <= now+60]
                    task.setdefault('metric_updated_at',{})
                    for key in task.get('failed_metrics',list(PANELS)):
                        task['metrics'][key]=old.get('metrics',{}).get(key)
                        task['metric_updated_at'][key]=old.get('metric_updated_at',{}).get(key,old.get('updated_at'))
                    if not task['updated_at']:
                        task['updated_at']=old.get('updated_at')
                    if not task['workers']:
                        task['workers'] = old.get('workers',[])
                    task['metric_error'] += '；缺失项显示上次指标快照'
            errors=[k+'：'+v['error'] for k,v in state['source_status'].items() if v['status']=='error']
            if any(t.get('metric_error') for t in state['tasks']):
                errors.append('部分任务监控指标缺失或过期')
            if errors:
                state['status']='partial'
                state['error']='；'.join(errors)
        return state
