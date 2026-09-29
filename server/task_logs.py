"""Read bounded log snapshots for verified personal CCI/ACP tasks."""
import base64
import binascii
import json
import re
import time
from urllib.parse import quote
import httpx
from .auth import load_session, AuthRequired
from .collector import Collector
from .config import WORKSPACE
from .acp_actions import ActionError
from .acp_states import is_charles
from .normalize import iso

LOG_ENDPOINT = 'monitor-data.ms-sc-01.maoshanwangtechapi.com'
MAX_TEXT = 1000000
MAX_RESPONSE = 8000000
# Render log text only: discard terminal color, cursor and hyperlink escape sequences.
ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]')
CONTROLS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def task_identity(task_id):
    match=re.fullmatch(r'(CCI|ACP):([A-Za-z0-9][A-Za-z0-9_-]{0,62})',task_id) if isinstance(task_id,str) else None
    if not match:raise ActionError('任务标识无效。')
    return match.groups()


def log_text(entries,tail):
    if not isinstance(entries,list):raise ActionError('日志响应格式异常。','logs_unavailable',502)
    chunks=[];count=0;truncated=False;eof=False;broken=False
    # A bounded tail response should be small; still constrain malformed upstream responses.
    for entry in entries[-(tail+20):]:
        if not isinstance(entry,dict):raise ActionError('日志条目格式异常。','logs_unavailable',502)
        kind=entry.get('type')
        if kind=='EOF':eof=True;continue
        if kind=='BROKEN':broken=True;continue
        if kind=='HEARTBEAT':continue
        if kind!='LOG':raise ActionError('无法识别日志条目类型。','logs_unavailable',502)
        content=entry.get('content','')
        if not isinstance(content,str):raise ActionError('日志文本格式异常。','logs_unavailable',502)
        try:
            raw=base64.b64decode(content,validate=True).decode('utf-8',errors='replace')
        except (ValueError,binascii.Error):
            raise ActionError('日志文本解码失败。','logs_unavailable',502) from None
        raw=CONTROLS.sub('',ANSI.sub('',raw)).replace('\r\n','\n').replace('\r','\n')
        chunks.append(raw);count+=1
    if count>tail:chunks=chunks[-tail:];truncated=True
    text=''.join(chunks)
    if len(text)>MAX_TEXT:text=text[-MAX_TEXT:];truncated=True
    warning='日志过长，仅显示末尾内容。' if truncated else ''
    if broken:warning+='平台报告日志流中断，部分内容可能缺失。'
    return text,min(count,tail),eof,truncated,warning


class TaskLogs:
    def __init__(self,*,transport=None,session_loader=None,site=None):
        self.workspace=site.workspace if site else WORKSPACE
        self.log_endpoints=(site.log_endpoints or (site.log_endpoint,)) if site else (LOG_ENDPOINT,)
        self.transport=transport;self.session_loader=session_loader or ((lambda:load_session(site)) if site else load_session)
        self.reader=Collector(site)

    def client(self):
        return httpx.AsyncClient(headers=self.session_loader()['headers'],timeout=25,follow_redirects=False,transport=self.transport)

    async def resolve(self,client,task_id):
        kind,name=task_identity(task_id)
        service='AEC2' if kind=='ACP' else 'CCI'
        path='/compute/'+kind.lower()+'/data/v2'+self.workspace+('/trainingJobs' if kind=='ACP' else '/apps')
        rows=await self.reader.paginate(client,service,path+'Own','training_jobs' if kind=='ACP' else 'apps',{'page_size':20,'filter':"name='"+name+"'"})
        task=next((row for row in rows if row.get('name')==name and is_charles(row.get('display_name'))),None)
        if not task or (task.get('id') and task['id']!=self.workspace+('/trainingJobs/' if kind=='ACP' else '/apps/')+name):
            raise ActionError('只能查看当前工作空间本人 charles 任务的日志。','target_mismatch',403)
        if not isinstance(task.get('uid'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',task['uid']):
            raise ActionError('任务日志标识不完整。','logs_unavailable',409)
        key='workers' if kind=='ACP' else 'instances'
        raw=await self.reader.paginate(client,service,path+'/'+name+'/'+key,key,{'page_size':100})
        workers=[]
        for row in raw:
            worker=row.get('name')
            if not isinstance(worker,str) or not worker:continue
            containers=(row.get('containers') or [])+(row.get('init_containers') or []) if kind=='ACP' else row.get('container_infos') or []
            names=[c.get('name' if kind=='ACP' else 'container_name') for c in containers]
            names=list(dict.fromkeys(n for n in names if isinstance(n,str) and n))
            workers.append({'name':worker,'state':row.get('phase' if kind=='ACP' else 'state'),'containers':names})
        return task,{'task_id':task_id,'kind':kind,'name':task['display_name'],'workers':workers,'updated_at':iso(time.time())}

    async def targets(self,task_id):
        task_identity(task_id)
        async with self.client() as client:
            _,targets=await self.resolve(client,task_id)
        return targets

    async def read(self,task_id,worker,container,tail=1000):
        task_identity(task_id)
        if type(tail) is not int or not 1<=tail<=1000:raise ActionError('日志条数须在 1 到 1000 之间。')
        if not all(isinstance(v,str) and 0<len(v)<=253 for v in (worker,container)):raise ActionError('请选择有效的 Worker 和容器。')
        async with self.client() as client:
            task,targets=await self.resolve(client,task_id)
            if not any(w['name']==worker and container in w['containers'] for w in targets['workers']):
                raise ActionError('Worker 或容器不属于该任务，请刷新日志列表。','target_mismatch',403)
            await self.reader.metadata(client)
            filters=[{'key':'pod','val':worker},{'key':'container','val':container},
                     {'key':'lepton.sensetime.com/workload-name','val':task['name']},
                     {'key':'lepton.sensetime.com/workload-uid','val':task['uid']},
                     {'key':'lepton.sensetime.com/workload-type','val':targets['kind'].lower()}]
            auth=await self.reader.request(client,'MONITOR','/monitor/ts/data/v1'+self.reader.station+'/logLivestream/token',body={
                'resource_id':self.reader.workspace_uid,'resource_type':'resource_type.compute.workspace.v1.instance','filters':filters})
        if auth.get('endpoint') not in self.log_endpoints or not isinstance(auth.get('token'),str) or not auth['token']:
            raise ActionError('平台日志服务地址或凭证无法确认。','logs_unavailable',502)
        url='https://'+auth['endpoint']+'/v1/polling/resources/'+quote(self.reader.workspace_uid,safe='')
        # The short-lived log token is sufficient; never forward the IAM header to the data endpoint.
        try:
            async with httpx.AsyncClient(timeout=70,follow_redirects=False,transport=self.transport) as client:
                async with client.stream('POST',url,json={'token':auth['token'],'resource_id':self.reader.workspace_uid,'tail':tail}) as response:
                    if response.status_code in (401,403):raise AuthRequired('日志会话已失效。')
                    response.raise_for_status()
                    content=bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content)>MAX_RESPONSE:raise ActionError('日志响应超过读取限制，请稍后重试。','logs_unavailable',502)
                    payload=json.loads(content)
            if not isinstance(payload,dict):raise ValueError('invalid response')
            text,count,eof,truncated,warning=log_text(payload.get('entries'),tail)
        except (httpx.HTTPError,ValueError):
            raise ActionError('日志暂时无法读取，请稍后刷新。','logs_unavailable',503) from None
        return {'task_id':task_id,'worker':worker,'container':container,'text':text,'entries':count,'tail':tail,
                'updated_at':iso(time.time()),'eof':eof,'truncated':truncated,'warning':warning}
