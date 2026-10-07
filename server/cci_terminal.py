"""Resolve official CCI terminal deep links; never execute container commands locally."""
import re
import copy
import time
from urllib.parse import urlencode
import httpx
from .auth import load_session
from .collector import Collector
from .acp_actions import ActionError
from .acp_states import is_charles
from .task_logs import task_identity
from .runtime_profile import get_runtime_profile
from .terminal_target_cache import TerminalTargetCache


TARGET_CACHE_SECONDS = get_runtime_profile()['target_cache_seconds']


class VerifiedCCITargets:
    """Internal, short-lived proof shared by one editor preparation's stages."""
    def __init__(self, site, result):
        self.site = (site.id, site.workspace)
        self.result = copy.deepcopy(result)
        self.created = time.monotonic()

    def get(self, site, task_id):
        if (self.site == (site.id, site.workspace) and self.result.get('site') == site.id
                and self.result.get('task_id') == task_id and 0 <= time.monotonic() - self.created < 10):
            return copy.deepcopy(self.result)
        return None


class CCITerminal:
    def __init__(self,site,*,transport=None,session_loader=None):
        self.site=site;self.reader=Collector(site);self.transport=transport
        self.session_loader=session_loader or (lambda:load_session(site))
        self.target_cache=TerminalTargetCache(TARGET_CACHE_SECONDS)

    async def targets(self,task_id,*,include_address=False,reuse=False):
        kind,name=task_identity(task_id)
        if kind!='CCI':raise ActionError('仅 CCI 支持 Web Terminal。','terminal_unavailable',400)
        headers=dict(self.session_loader()['headers'])
        return await self.target_cache.resolve((task_id,include_address),headers,
            lambda:self.read_targets(task_id,name,headers,include_address),reuse=reuse)

    async def read_targets(self,task_id,name,headers,include_address):
        path='/compute/cci/data/v2'+self.site.workspace+'/apps'
        async with httpx.AsyncClient(headers=headers,timeout=25,follow_redirects=False,transport=self.transport) as client:
            rows=await self.reader.paginate(client,'CCI',path+'Own','apps',{'page_size':20,'filter':"name='"+name+"'"})
            task=next((row for row in rows if row.get('name')==name and is_charles(row.get('display_name'))),None)
            if not task or task.get('id')!=self.site.workspace+'/apps/'+name:
                raise ActionError('只能进入当前工作空间本人 charles 的 CCI 任务。','target_mismatch',403)
            if task.get('state')!='RUNNING':
                raise ActionError('该 CCI 任务已不在运行，请返回监控刷新状态。','terminal_unavailable',409)
            instances=await self.reader.paginate(client,'CCI',path+'/'+name+'/instances','instances',{'page_size':100})
        targets=[];seen=set()
        for row in instances:
            instance=row.get('name')
            if row.get('state')!='RUNNING' or row.get('app_name')!=name or not isinstance(instance,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}',instance):continue
            for info in row.get('container_infos') or []:
                container=info.get('container_name')
                if info.get('container_type')!='MAIN' or info.get('container_state')!='RUNNING' or not isinstance(container,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}',container):continue
                if (instance,container) in seen:continue
                seen.add((instance,container))
                query=urlencode({'container_name':container,'container_instance_name':instance,'app_name':name,'workspace':self.site.workspace})
                target={'instance':instance,'container':container,'url':self.site.console+'/cci/terminal?'+query}
                if include_address:target['address']=row.get('instance_ip')
                targets.append(target)
        result={'site':self.site.id,'task_id':task_id,'name':task['display_name'],'targets':targets}
        return result
