"""Explicit ACP mutations. Read previews are separate; cloud writes never retry."""
import asyncio
import copy
import json
import re
import time
import uuid
from datetime import datetime
import httpx
from .auth import load_session
from .collector import Collector
from .config import SERVICES, WORKSPACE
from .normalize import iso, cluster_from_raw
from .domain import finite_number
from .acp_states import is_charles, STOPPABLE_STATES

PRIORITY = ('modeltoolchain-test','modeltoolchain','vigen')
API = '/compute/acp/data/v2'+WORKSPACE+'/trainingJobs'
PREVIEW_TTL = 600
COPY_FIELDS = ('metadata','mount','env','tensorboard','async_checkpoint','lme','scheduling','ssh','fault_tolerance','barrier')


class ActionError(Exception):
    def __init__(self,message,code='invalid_request',status=400):
        super().__init__(message)
        self.code,self.status=code,status


def integer(value,label,maximum=1024):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ActionError(label+'必须是 1 到 '+str(maximum)+' 之间的整数。')
    return value


def validate_form(data):
    required={'display_name','replicas','gpus_per_replica','command'}
    if not isinstance(data,dict) or not required <= set(data) <= required | {'cluster_id','partition_id'}:
        raise ActionError('请填写显示名称、副本数、每副本 GPU 数和启动命令。')
    partition=data.get('partition_id','')
    if not isinstance(partition,str) or len(partition)>253 or (partition and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_+\-]*',partition)):
        raise ActionError('请选择有效的 AFS 分区。')
    cluster_id=data.get('cluster_id','')
    if not isinstance(cluster_id,str) or len(cluster_id)>253 or (cluster_id and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',cluster_id)):
        raise ActionError('请选择有效的集群。')
    name=data['display_name']
    if not isinstance(name,str) or not name.strip() or len(name)>128 or not is_charles(name) or any(ord(c)<32 for c in name):
        raise ActionError('显示名称须包含 charles，长度不超过 128，且不能包含控制字符。')
    integer(data['replicas'],'副本数');integer(data['gpus_per_replica'],'每副本 GPU 数',64)
    if not isinstance(data['command'],str) or len(data['command'])>65536 or '\x00' in data['command']:
        raise ActionError('启动命令必须是有效文本，最多 65536 字符。')
    command=data['command'].replace('\r\n','\n').strip()
    if command and not re.fullmatch(r'sleep[ \t]+inf[ \t]*;?',command.splitlines()[-1].strip()):
        command += '\nsleep inf'
    return {**data,'display_name':name.strip(),'command':command or 'sleep inf'}


def job_name(task_id):
    if not isinstance(task_id,str) or not task_id.startswith('ACP:') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}',task_id[4:]):
        raise ActionError('仅支持当前工作空间的 ACP 任务。')
    return task_id[4:]


def public_template(raw):
    return {'id':'ACP:'+raw['name'],'name':raw['display_name'],'created_at':raw.get('create_time'),'state':raw.get('state')}


def created_timestamp(raw):
    try:return datetime.fromisoformat(raw['create_time'].replace('Z','+00:00')).timestamp()
    except (KeyError,TypeError,ValueError):raise ActionError('任务创建时间不完整，无法确认最新模板。','template_unavailable',409) from None


def afs_partition(raw):
    mounts=[m for m in raw.get('mount',[]) if m.get('type')=='PV_AFS']
    if not mounts or any(not all(isinstance(m.get(k),str) and m[k] for k in ('zone','id','mount_path')) for m in mounts):return None
    return '+'.join(sorted({m['zone'] for m in mounts}))


def public_partition(raw):
    key=afs_partition(raw)
    label=(key[-1].upper()+' 区 · ') if re.fullmatch(r'cn-sh-01[a-z]',key) else ''
    return {'id':key,'name':label+'AFS '+key,'template':public_template(raw)}


def template_config(raw):
    return {'images':[r['image_path'] for r in raw.get('roles',[]) if r.get('image_path')],
            'afs_mounts':[{k:m.get(k) for k in ('id','zone','mount_path','subdir')} for m in raw.get('mount',[]) if m.get('type')=='PV_AFS']}


def build_payload(template,binding,spec,form,name):
    roles=template.get('roles') or []
    if len(roles)!=1 or roles[0].get('name')!='Worker' or not roles[0].get('image_path') or template.get('fine_tuning_specs'):
        raise ActionError('最新任务不是支持的单 Worker 角色模板，请在云控制台检查。','unsupported_template',409)
    payload={key:copy.deepcopy(template[key]) for key in COPY_FIELDS if template.get(key) is not None}
    framework=template.get('framework')
    for single,multi in (('PYTORCH','PYTORCH_DDP'),('TENSORFLOW','TENSORFLOW_PS'),('SENSEPARROTS','SENSEPARROTS_DDP')):
        if framework in (single,multi):framework=multi if form['replicas']>1 else single;break
    if framework not in ('PYTORCH','PYTORCH_DDP','TENSORFLOW','TENSORFLOW_PS','SENSEPARROTS','SENSEPARROTS_DDP','MPI'):
        raise ActionError('最新模板的训练框架暂不支持复制。','unsupported_template',409)
    payload.update(name=name,display_name=form['display_name'],framework=framework,
        roles=[{'name':'Worker','resource_spec':[{'name':spec['name'],'replicas':0}],
                'total_replicas':form['replicas'],'startup_script':form['command'],'image_path':roles[0]['image_path']}],
        resource_pool={'name':binding['name'],'zone':binding['id'].split('/zones/')[1].split('/')[0],'vpc_id':binding['vpc_id']})
    payload.setdefault('scheduling',{})['quota_type']='RESERVED'
    return payload


class ACPManager:
    def __init__(self,store,*,transport=None,session_loader=None,site=None):
        self.site=site
        self.workspace=site.workspace if site else WORKSPACE
        self.services=site.services if site else SERVICES
        self.api="/compute/acp/data/v2"+self.workspace+"/trainingJobs"
        self.store=store;self.transport=transport;self.session_loader=session_loader or ((lambda:load_session(site)) if site else load_session)
        self.reader=Collector(site);self.previews={};self.lock=asyncio.Lock()

    def client(self):
        return httpx.AsyncClient(headers=self.session_loader()['headers'],timeout=25,follow_redirects=False,transport=self.transport)

    async def own_jobs(self,client):
        return await self.reader.paginate(client,'AEC2',self.api+'Own','training_jobs')

    async def detail(self,client,name):
        raw=await self.reader.request(client,'AEC2',self.api+'/'+name)
        if raw.get('name')!=name or raw.get('id')!=self.workspace+'/trainingJobs/'+name or not is_charles(raw.get('display_name')):
            raise ActionError('任务不属于当前工作空间中名称包含 charles 的 ACP 任务。','target_mismatch',403)
        return raw

    @property
    def partitioned(self):
        return bool(self.site and self.site.id=='sensecore')

    async def template_context(self,client,partition_id=None):
        rows=[j for j in await self.own_jobs(client) if is_charles(j.get('display_name'))]
        if not rows:raise ActionError('没有找到名称包含 charles 的 ACP 模板任务。','template_unavailable',409)
        partitions=[]
        if self.partitioned:
            groups={}
            for row in sorted(rows,key=created_timestamp,reverse=True):
                key=afs_partition(row)
                if key:groups.setdefault(key,row)
            if not groups:raise ActionError('没有找到包含完整 AFS 挂载的 charles 模板。','template_unavailable',409)
            chosen=partition_id or ('cn-sh-01a' if 'cn-sh-01a' in groups else next(iter(groups)))
            if chosen not in groups:raise ActionError('AFS 分区已不存在或没有可用模板，请重新读取配置。','partition_unavailable',409)
            latest=groups[chosen]
            partitions=[public_partition(row) for _,row in sorted(groups.items())]
        else:
            if partition_id:raise ActionError('当前站点不支持 AFS 分区选择。')
            running=[j for j in rows if j.get('state')=='RUNNING']
            latest=max(running or rows,key=created_timestamp)
        template=await self.detail(client,job_name('ACP:'+latest['name']))
        if self.partitioned and afs_partition(template)!=chosen:
            raise ActionError('模板 AFS 配置已变化，请重新读取分区。','partition_unavailable',409)
        return template,partitions

    async def mounts_compatible(self,client,template,zone,cache):
        mismatched=[m for m in template.get('mount',[]) if m.get('zone') and m['zone']!=zone]
        if not mismatched:return True
        if not self.partitioned or any(m.get('type')!='PV_AFS' for m in mismatched):return False
        if zone not in cache:
            data=await self.reader.request(client,'AFS','/storage/afs/data/v1/zones/'+zone+'/crossAttach',params={'source_zone':zone})
            zones=data.get('zones')
            if not isinstance(zones,list) or any(not isinstance(z,str) for z in zones):
                raise ActionError('AFS 跨区挂载规则暂不可用，请重新读取配置。','resources_unavailable',503)
            cache[zone]=set(zones)
        return all(m['zone'] in cache[zone] for m in mismatched)

    async def catalog(self,client,include_all=False,template=None):
        if template is None:template,_=await self.template_context(client)
        bindings=await self.reader.paginate(client,'AEC2','/compute/workspace/data/v1'+self.workspace+'/workspaceAEC2Bindings','aec2s')
        selected=[];mount_zones={}
        priority=self.site.cluster_priority if self.site else PRIORITY
        if priority is None:
            template_cluster=(template.get('resource_pool') or {}).get('name')
            priority=sorted((b['name'] for b in bindings if b.get('type','').lower()!='public' and b.get('name')!='public'),
                key=lambda name:(name!=template_cluster,name))
        if include_all:
            priority=list(priority)+sorted({b['name'] for b in bindings if b.get('name') and b['name'] not in priority})
        for name in priority:
            matches=[b for b in bindings if b.get('name')==name and b.get('state')=='ACTIVE' and b.get('type','').lower()!='public' and name!='public']
            if len(matches)>1:raise ActionError('集群标识不唯一，请检查工作空间绑定。','resources_unavailable',409)
            if not matches:continue
            b=matches[0];rid=b.get('id','')
            if not rid.startswith(self.workspace.split('/zones/')[0]+'/zones/') or '/aec2s/' not in rid or rid.rsplit('/',1)[-1]!=name:
                raise ActionError('集群绑定格式不完整。','resources_unavailable',409)
            if not b.get('vpc_id'):raise ActionError('集群网络信息不完整。','resources_unavailable',409)
            # Mounts must remain valid when choosing a different cluster.
            zone=rid.split('/zones/')[1].split('/')[0]
            if not await self.mounts_compatible(client,template,zone,mount_zones):continue
            payload=await self.reader.request(client,'AEC2','/compute/aec2/data/v1'+rid+'/resourceSpecs')
            if not isinstance(payload.get('resource_specs'),list):raise ActionError('资源规格暂不可用。','resources_unavailable',503)
            specs=[]
            for spec in payload['resource_specs']:
                g=finite_number(spec.get('device',{}).get('number'))
                cpu=finite_number(spec.get('cpu',{}).get('vcpu_allocatable'))
                mem=finite_number(spec.get('memory',{}).get('allocatable'))
                if g is not None and g>0 and g.is_integer() and cpu is not None and cpu>0 and mem is not None and mem>0 and spec.get('name'):
                    specs.append(spec)
            if specs:selected.append((b,specs))
        if not selected:raise ActionError('优先集群中没有可继承当前挂载的资源。','resources_unavailable',409)
        return template,selected

    async def options(self,partition_id=None):
        async with self.client() as client:
            template,partitions=await self.template_context(client,partition_id)
            template,catalog=await self.catalog(client,include_all=True,template=template)
        priority=self.site.cluster_priority if self.site else PRIORITY
        automatic=[(b,specs) for b,specs in catalog if priority is None or b['name'] in priority]
        gpu_options=sorted({int(s['device']['number']) for _,specs in automatic for s in specs})
        if not gpu_options:raise ActionError('没有可选 GPU 容器规格。','resources_unavailable',409)
        command='sleep inf';command_source=None
        if template.get('state')=='RUNNING':
            roles=template.get('roles') or []
            if len(roles)!=1 or roles[0].get('name')!='Worker':
                raise ActionError('运行模板不是支持的单 Worker 角色，无法确定默认命令。','unsupported_template',409)
            script=roles[0].get('startup_script')
            if script is not None and (not isinstance(script,str) or len(script)>65536 or '\x00' in script):
                raise ActionError('运行模板的命令格式异常或超过编辑器限制。','unsupported_template',409)
            if script and script.strip():
                command=script.replace('\r\n','\n')
                command_source={'id':'ACP:'+template['name'],'name':template['display_name']}
        return {**({'partitions':partitions,'partition':public_partition(template),'template_config':template_config(template)} if self.partitioned else {}),
                'template':public_template(template),'gpu_options':gpu_options,'default_gpus_per_replica':gpu_options[0],
                'default_command':command,'command_source':command_source,
                'clusters':[{'id':b['name'],'name':b.get('display_name') or b['name'],
                             'gpu_options':sorted({int(s['device']['number']) for s in specs})} for b,specs in catalog],
                'cluster_order':[b.get('display_name') or b['name'] for b,_ in automatic],'updated_at':iso(time.time())}

    async def preview(self,data):
        form=validate_form(data)
        cluster_id=form.get('cluster_id','')
        async with self.client() as client:
            template,_=await self.template_context(client,form.get('partition_id'))
            template,catalog=await self.catalog(client,include_all=bool(cluster_id),template=template)
        if cluster_id:
            catalog=[(b,specs) for b,specs in catalog if b['name']==cluster_id]
            if not catalog:raise ActionError('指定集群已不可用或与模板挂载不兼容，请重新选择。','cluster_unavailable',409)
        candidates=[]
        for binding,specs in catalog:
            matches=[s for s in specs if s['device']['number']==form['gpus_per_replica']]
            if not matches:continue
            # Console's standard whole-card size, before reduced CPU/memory variants.
            matches.sort(key=lambda s:(not s['name'].endswith('.'+str(form['gpus_per_replica'])),s['name']))
            spec=matches[0];quota=cluster_from_raw(binding,time.time())
            demand={'available_gpus':form['replicas']*form['gpus_per_replica'],
                    'available_vcpu':form['replicas']*spec['cpu']['vcpu_allocatable'],
                    'available_memory_gib':form['replicas']*spec['memory']['allocatable']}
            enough=all(quota[k] is not None and quota[k]>=v for k,v in demand.items())
            candidates.append((binding,spec,enough))
        if not candidates:raise ActionError('当前集群不支持所选的每副本卡数。','resources_unavailable',409)
        binding,spec,enough=next((c for c in candidates if c[2]),candidates[0])
        token=uuid.uuid4().hex;now=time.time();name='cm-'+token[:24]
        payload=build_payload(template,binding,spec,form,name)
        preview={**({'partition':public_partition(template),'template_config':template_config(template)} if self.partitioned else {}),
                 'preview_id':token,'expires_at':iso(now+PREVIEW_TTL),'template':public_template(template),
                 'selection_mode':'manual' if cluster_id else 'auto',
                 'cluster':{'id':binding['name'],'name':binding.get('display_name') or binding['name']},
                 'replicas':form['replicas'],'gpus_per_replica':form['gpus_per_replica'],
                 'total_gpus':form['replicas']*form['gpus_per_replica'],'vcpu_per_replica':spec['cpu']['vcpu_allocatable'],
                 'memory_gib_per_replica':spec['memory']['allocatable'],'command':form['command'],'will_queue':not enough,
                 'message':'当前配额不足或未知，将提交并等待调度。' if not enough else '当前配额充足；实际启动时间取决于调度，余量变化时自动排队。'}
        self.previews={k:v for k,v in self.previews.items() if v['expires']>now}
        if len(self.previews)>=100:self.previews.pop(next(iter(self.previews)))
        self.previews[token]={'expires':now+PREVIEW_TTL,'payload':payload,'preview':preview}
        return copy.deepcopy(preview)

    async def write_once(self,client,path,body=None,params=None):
        try:
            response=await client.post(self.services['AEC2']+path,params=params,**({'json':body} if body is not None else {}))
        except httpx.HTTPError:
            raise ActionError('云端结果暂不明确，请核对任务状态；该请求不会自动重复提交。','outcome_unknown',502) from None
        if response.status_code>=500 or response.status_code in (408,409):
            raise ActionError('云端结果暂不明确，请核对任务状态；该请求不会自动重复提交。','outcome_unknown',502)
        if response.status_code in (401,403):raise ActionError('云会话已失效或没有操作权限，请重新连接。','auth_required',403)
        if not 200<=response.status_code<300:raise ActionError('云平台拒绝了操作，请核对资源和任务配置。','cloud_rejected',400)
        try:return response.json() if response.content else {}
        except ValueError:raise ActionError('云端响应无法确认，请核对任务状态，不要重复创建。','outcome_unknown',502) from None

    async def previous_result(self,record,kind,target=None,display_name=None):
        if record['kind']!=kind or (target is not None and record['target']!=target) or (display_name is not None and record['display_name']!=display_name):
            raise ActionError('请求标识已用于其他任务。','request_conflict',409)
        if record['status']=='succeeded':return record['result']
        if record['status']=='failed':raise ActionError(record['error'],'cloud_rejected',409)
        # A lost response or process restart is reconciled with a read, never a second POST.
        try:
            async with self.client() as client:raw=await self.detail(client,record['target'])
            if raw['display_name']==record['display_name'] and (kind=='create' or raw.get('state') in ('SUSPENDING','SUSPENDED')):
                result={'id':'ACP:'+raw['name'],'name':raw['display_name'],'state':raw.get('state'),'message':'已从云端核实操作结果。'}
                self.store.finish_operation(record['key'],'succeeded',result=result)
                return result
        except Exception:pass
        raise ActionError('上次操作结果尚不明确，请在控制台按任务 ID '+record['target']+' 核对；不会重复发送。','outcome_unknown',409)

    async def create(self,preview_id):
        if not isinstance(preview_id,str) or not re.fullmatch('[a-f0-9]{32}',preview_id):raise ActionError('无效的创建预览。')
        async with self.lock:
            old=self.store.operation(preview_id)
            if old:return await self.previous_result(old,'create')
            prepared=self.previews.get(preview_id)
            if not prepared or prepared['expires']<time.time():raise ActionError('预览已过期，请重新预览配置。','preview_expired',409)
            payload=prepared['payload'];name=payload['name'];display=payload['display_name']
            async with self.client() as client:
                if not self.store.begin_operation(preview_id,'create',name,display):
                    return await self.previous_result(self.store.operation(preview_id),'create')
                try:
                    raw=await self.write_once(client,self.api,body=payload,params={'enable_queuing':'true'})
                    if not isinstance(raw,dict) or (raw.get('name') and raw['name']!=name):
                        raise ActionError('创建返回标识不一致，请按生成的任务 ID 核对。','outcome_unknown',502)
                    result={'id':'ACP:'+name,'name':display,'state':raw.get('state') or 'CREATING','message':'ACP 任务已提交；运行或排队状态会自动更新。'}
                    self.store.finish_operation(preview_id,'succeeded',result=result)
                    self.previews.pop(preview_id,None)
                    return result
                except ActionError as exc:
                    self.store.finish_operation(preview_id,'unknown' if exc.code=='outcome_unknown' else 'failed',error=str(exc))
                    raise

    async def stop(self,task_id,display_name,request_id):
        name=job_name(task_id)
        if not is_charles(display_name):raise ActionError('只能停止名称包含 charles 的 ACP 任务。')
        try:key=str(uuid.UUID(request_id))
        except (ValueError,TypeError,AttributeError):raise ActionError('无效的停止请求标识。') from None
        async with self.lock:
            old=self.store.operation(key)
            if old:return await self.previous_result(old,'stop',name,display_name)
            async with self.client() as client:
                if not any(j.get('name')==name and is_charles(j.get('display_name')) for j in await self.own_jobs(client)):
                    raise ActionError('没有找到可操作的本人 ACP 任务。','target_mismatch',403)
                raw=await self.detail(client,name)
                if raw['display_name']!=display_name:raise ActionError('任务名称已经变化，请刷新后重新核对。','target_mismatch',409)
                if raw.get('state') not in STOPPABLE_STATES:raise ActionError('任务当前不可停止，请刷新查看最新状态。','state_changed',409)
                if not self.store.begin_operation(key,'stop',name,display_name):
                    return await self.previous_result(self.store.operation(key),'stop',name,display_name)
                try:
                    await self.write_once(client,self.api+'/'+name+':stop')
                    result={'id':task_id,'name':display_name,'state':'SUSPENDING','message':'停止请求已提交，等待平台完成资源释放。'}
                    self.store.finish_operation(key,'succeeded',result=result)
                    return result
                except ActionError as exc:
                    self.store.finish_operation(key,'unknown' if exc.code=='outcome_unknown' else 'failed',error=str(exc))
                    raise
