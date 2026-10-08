"""Resolve official ACP worker terminals without executing container commands."""
import re
from urllib.parse import urlencode
import httpx
from .auth import load_session
from .collector import Collector
from .acp_actions import ActionError
from .acp_states import is_charles
from .terminal_resolver import TerminalResolver


class ACPTerminal(TerminalResolver):
    kind = 'ACP'
    kind_error = '请选择 ACP 任务。'

    async def read_targets(self,task_id,name,headers,**options):
        path='/compute/acp/data/v2'+self.site.workspace+'/trainingJobs'
        async with httpx.AsyncClient(headers=headers,timeout=25,follow_redirects=False,transport=self.transport) as client:
            rows=await self.reader.paginate(client,'AEC2',path+'Own','training_jobs',{'page_size':20,'filter':"name='"+name+"'"})
            task=next((row for row in rows if row.get('name')==name and is_charles(row.get('display_name'))),None)
            if not task or task.get('id')!=self.site.workspace+'/trainingJobs/'+name:
                raise ActionError('只能进入当前工作空间本人 charles 的 ACP 任务。','target_mismatch',403)
            if task.get('state')!='RUNNING':
                raise ActionError('该 ACP 任务已不在运行，请返回监控刷新状态。','terminal_unavailable',409)
            workers=await self.reader.paginate(client,'AEC2',path+'/'+name+'/workers','workers',{'page_size':100})
        targets=[];seen=set()
        for row in workers:
            worker=row.get('name')
            if row.get('phase')!='Running' or not isinstance(worker,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}',worker) or worker in seen:continue
            seen.add(worker)
            query=urlencode({'workspace':self.site.workspace,'job_id':name,'pod_name':worker})
            targets.append({'instance':worker,'url':self.site.console+'/acp/terminal?'+query})
        result={'site':self.site.id,'task_id':task_id,'name':task['display_name'],'targets':targets}
        return result
