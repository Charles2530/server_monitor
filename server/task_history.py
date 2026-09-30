"""Read-only terminal charles tasks; queued cancellations need run evidence."""
import asyncio
import copy
import time
from datetime import datetime
import httpx
from .auth import load_session
from .collector import Collector
from .acp_states import is_charles
from .normalize import iso,task_from_raw,task_console_url

STATES={'SUCCEEDED':('succeeded','成功'),'FAILED':('failed','失败'),'SUSPENDED':('stopped','已停止')}


def timestamp(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None or parsed.year<2000:return None
        return parsed.timestamp()
    except (ValueError,TypeError,AttributeError,OverflowError):return None


def belongs(raw,kind,site):
    return (isinstance(raw,dict) and is_charles(raw.get('display_name')) and isinstance(raw.get('name'),str)
            and raw.get('id')==site.workspace+('/trainingJobs/' if kind=='ACP' else '/apps/')+raw['name'])


def history_task(raw,kind,site,observed):
    if not belongs(raw,kind,site) or raw.get('state') not in STATES:return None
    task_id=kind+':'+raw['name']
    url=task_console_url({'id':task_id,'kind':kind},site)
    if not url:return None
    state=raw['state'];status,label=STATES[state]
    start=timestamp(raw.get('start_time'))
    ended=timestamp(raw.get('suspend_time') if state=='SUSPENDED' else raw.get('complete_time')) if kind=='ACP' else None
    created=timestamp(raw.get('create_time'));updated=timestamp(raw.get('update_time'))
    if start is not None and ((created is not None and start<created) or (ended is not None and start>ended) or start>time.time()+60):start=None
    evidence='start_time' if start is not None else 'observed' if task_id in observed else None
    if status=='stopped' and not evidence:return None
    normalized=task_from_raw(raw,kind,time.time())
    return {'id':task_id,'name':raw['display_name'],'kind':kind,'status':status,'state':state,'status_label':label,
            'cluster':normalized['cluster'],'gpu_count':normalized['gpu_count'],'replicas':normalized['replicas'],
            'created_at':iso(created) if created is not None else None,'started_at':iso(start) if start is not None else None,
            'ended_at':iso(ended) if ended is not None else None,'updated_at':iso(updated) if updated is not None else None,
            'duration_seconds':ended-start if start is not None and ended is not None and ended>=start else None,
            'run_evidence':evidence,'url':url}


class TaskHistory:
    def __init__(self,store,site,transport=None,session_loader=None):
        self.store,self.site,self.transport=store,site,transport
        self.session_loader=session_loader or (lambda:load_session(site))
        self.reader=Collector(site)
        self.records={'CCI':[],'ACP':[]}
        self.sources={kind:{'status':'loading','updated_at':None} for kind in self.records}
        self.filtered={'CCI':0,'ACP':0}
        self.refresh_task=None
        self.checked_at=None

    async def refresh(self):
        try:observed=self.store.running_task_ids()
        except Exception:
            for source in self.sources.values():source["status"]="error"
            self.checked_at=time.monotonic()
            return
        async def read(kind,service,path,key):
            previous=self.sources[kind]
            try:
                async with httpx.AsyncClient(headers=self.session_loader()['headers'],timeout=25,transport=self.transport,follow_redirects=False) as client:
                    rows=await self.reader.paginate(client,service,path,key)
                tasks={};filtered=0
                for raw in rows:
                    if not belongs(raw,kind,self.site):continue
                    task=history_task(raw,kind,self.site,observed)
                    if task:tasks[task['id']]=task
                    elif raw.get('state')=='SUSPENDED':filtered+=1
                self.records[kind]=list(tasks.values());self.filtered[kind]=filtered
                self.sources[kind]={'status':'ok','updated_at':iso(time.time())}
            except asyncio.CancelledError:raise
            except Exception:
                self.sources[kind]={'status':'error','updated_at':previous.get('updated_at')}
        async def bounded(*args):
            try:await asyncio.wait_for(read(*args),150)
            except asyncio.TimeoutError:self.sources[args[0]]['status']='error'
        try:
            await asyncio.gather(
                bounded('CCI','CCI','/compute/cci/data/v2'+self.site.workspace+'/appsOwn','apps'),
                bounded('ACP','AEC2','/compute/acp/data/v2'+self.site.workspace+'/trainingJobsOwn','training_jobs'))
        finally:self.checked_at=time.monotonic()

    async def snapshot(self):
        loading=bool(self.refresh_task and not self.refresh_task.done())
        if not loading and (self.checked_at is None or time.monotonic()-self.checked_at>=60):
            self.refresh_task=asyncio.create_task(self.refresh());loading=True
        tasks=copy.deepcopy(self.records['CCI']+self.records['ACP'])
        tasks.sort(key=lambda t:((t['ended_at'] or t['updated_at'] or t['created_at'] or ''),t['id']),reverse=True)
        warnings=[kind+' 历史读取失败；'+('保留上次结果，数据可能过期。' if source['updated_at'] else '当前列表不完整。') for kind,source in self.sources.items() if source['status']=='error']
        return {'site':self.site.id,'tasks':tasks,'loading':loading,'source_status':copy.deepcopy(self.sources),
                'filtered_stopped':dict(self.filtered),'warnings':warnings}

    async def close(self):
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
            await asyncio.gather(self.refresh_task,return_exceptions=True)
