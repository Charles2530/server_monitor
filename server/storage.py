"""Site-scoped, read-only AFS inventory and recent charles mount references."""
import asyncio
import copy
import json
import math
import time
import httpx
from .auth import load_session
from .collector import Collector, SourceError
from .normalize import iso
from .acp_states import is_charles


UNITS = {name:1000**i for i,name in enumerate(('B','KB','MB','GB','TB','PB'))}
UNITS.update({name:1024**i for i,name in enumerate(('B','KIB','MIB','GIB','TIB','PIB'))})


def capacity_bytes(value, unit):
    if isinstance(value,bool) or not isinstance(unit,str) or unit.upper() not in UNITS:
        return None
    try:
        number=float(value)*UNITS[unit.upper()]
        return number if math.isfinite(number) and number>=0 else None
    except (ValueError,TypeError,OverflowError):
        return None


class StorageManager:
    def __init__(self,site,transport=None,session_loader=None):
        self.site,self.transport=site,transport
        self.session_loader=session_loader or (lambda:load_session(site))
        self.reader=Collector(site)
        self.lock=asyncio.Lock()
        self.cached=None
        self.cached_at=0
        self.mount_task=None
        self.mount_cache=None
        self.mount_checked_at=0
        self.mount_updated_at=None

    async def volumes(self,client):
        token,seen,rows='1',set(),[]
        while token:
            if token in seen or len(seen)>=1000:
                raise SourceError('AFS 分页异常，已停止读取。')
            seen.add(token)
            data=await self.reader.request(client,'MANAGEMENT','/rmh/v1/resources:page',body={},params={
                'filter':'resource_type="storage.afs.v1.volume" OR resource_type="storage.afs.v2.volume"',
                'page_size':100,'page_token':token})
            page=data.get('resources')
            if not isinstance(page,list):raise SourceError('AFS 列表格式异常。')
            # This API advances next_page_token even on its terminal empty page.
            if not page:break
            rows.extend(page)
            token=str(data.get('next_page_token') or '')
        prefix=self.site.workspace.split('/zones/')[0]+'/zones/'
        region=self.site.workspace.split('/zones/')[1].split('/')[0][:-1]
        result={}
        for row in rows:
            if not isinstance(row,dict):continue
            zone=row.get('zone','')
            if (row.get('type') not in ('storage.afs.v1.volume','storage.afs.v2.volume')
                or row.get('state') in ('DELETED','DELETING')
                or not isinstance(zone,str) or not zone.startswith(region) or len(zone)!=len(region)+1
                or not isinstance(row.get('rid'),str) or not row['rid'].startswith(prefix+zone+'/')
                or not isinstance(row.get('id'),str) or not row['id']):continue
            props=row.get('properties',{})
            if isinstance(props,str):
                try:props=json.loads(props)
                except ValueError:props={}
            try:
                billing=props['resources']['billing_items']
                total=capacity_bytes(billing.get('capacity'),billing.get('capacity_unit'))
            except (KeyError,TypeError,AttributeError):total=None
            order=row.get('order_info') or {}
            result[(zone,row['id'])]={'id':row['id'],'name':row.get('display_name') or row.get('name') or row['id'],
                'resource_name':row.get('name'),'zone':zone,'state':row.get('state') or 'UNKNOWN',
                'created_at':row.get('create_time'),'expires_at':order.get('end_time') if isinstance(order,dict) else None,
                'total_bytes':total,'used_bytes':None,'free_bytes':None,'used_percent':None,'mounts':[]}
        return list(result.values())

    async def usage(self,client,volumes,warnings):
        zones=sorted({v['zone'] for v in volumes})
        async def read_zone(zone):
            group=[v for v in volumes if v['zone']==zone]
            for start in range(0,len(group),100):
                batch=group[start:start+100]
                try:
                    data=await self.reader.request(client,'AFS','/storage/afs/data/v1/volume/running_status',
                        params={'zone':zone},body={'volume_ids':';'.join(v['id'] for v in batch)})
                    rows=data.get('volume_running_status')
                    if not isinstance(rows,list):raise SourceError('容量格式异常')
                    by_id={r.get('volume_id'):r for r in rows if isinstance(r,dict)}
                    for volume in batch:
                        raw=by_id.get(volume['id'],{})
                        used=None if raw.get('message') else capacity_bytes(raw.get('used_capacity'),raw.get('unit'))
                        volume['used_bytes']=used
                        total=volume['total_bytes']
                        if used is not None and total is not None:
                            volume['free_bytes']=max(0,total-used)
                            volume['used_percent']=used/total*100 if total>0 else None
                except Exception:
                    warnings.append(zone+' 的已用容量读取失败，暂显示未知。')
        await asyncio.gather(*(read_zone(zone) for zone in zones))
        missing=sum(v['used_bytes'] is None or v['total_bytes'] is None for v in volumes)
        if missing:warnings.append(str(missing)+' 个存储卷的容量信息不完整。')

    async def mounts(self,client,volumes,warnings):
        index={(v['zone'],v['id']):v for v in volumes}
        status={}
        async def read_kind(kind,service,path,key):
            try:
                rows=await self.reader.paginate(client,service,path,key)
                rows=[r for r in rows if is_charles(r.get('display_name')) and isinstance(r.get('id'),str)
                      and r['id'].startswith(self.site.workspace+('/apps/' if kind=='CCI' else '/trainingJobs/'))]
                rows.sort(key=lambda r:r.get('create_time') or '',reverse=True)
                seen=set()
                for row in rows:
                    if kind=='ACP':mounts=row.get('mount') or []
                    else:
                        template=row.get('template') or {}
                        containers=(template.get('containers') or [])+(template.get('init_containers') or [])
                        mounts=[m for c in containers for m in c.get('volume_mounts',[])]
                    for mount in mounts:
                        identity=(mount.get('zone'),mount.get('id'))
                        reference=(*identity,mount.get('subdir'),mount.get('mount_path'))
                        if mount.get('type')!='PV_AFS' or identity not in index or reference in seen:continue
                        seen.add(reference)
                        index[identity]['mounts'].append({'kind':kind,'task_name':row['display_name'],
                            'task_id':kind+':'+row['name'],'task_state':row.get('state'),
                            'created_at':row.get('create_time'),'subdir':mount.get('subdir'),
                            'mount_path':mount.get('mount_path')})
                status[kind]='ok'
            except Exception:
                status[kind]='error'
                warnings.append(kind+' 挂载关系读取失败，当前列表可能不完整。')
        await asyncio.gather(
            read_kind('CCI','CCI','/compute/cci/data/v2'+self.site.workspace+'/appsOwn','apps'),
            read_kind('ACP','AEC2','/compute/acp/data/v2'+self.site.workspace+'/trainingJobsOwn','training_jobs'))
        return status

    async def refresh_mounts(self,volumes):
        warnings=[]
        try:
            async with httpx.AsyncClient(headers=self.session_loader()['headers'],timeout=25,
                                         follow_redirects=False,transport=self.transport) as client:
                status=await asyncio.wait_for(self.mounts(client,volumes,warnings),120)
        except Exception:
            status={'CCI':'error','ACP':'error'}
            warnings=['挂载记录暂不可用，将自动重试；已有记录可能过期。']
        records={(v['zone'],v['id']):[m for m in v['mounts'] if status.get(m['kind'])=='ok'] for v in volumes}
        if self.mount_cache:
            for identity,old in self.mount_cache['records'].items():
                # Preserve only failed sources; successful empty results are authoritative.
                retained=[m for m in old if status.get(m['kind'])=='error']
                records.setdefault(identity,[]).extend(retained)
        if all(value=='ok' for value in status.values()):self.mount_updated_at=iso(time.time())
        self.mount_cache={'records':records,'status':status,'warnings':warnings}
        self.mount_checked_at=time.monotonic()

    async def close(self):
        if self.mount_task and not self.mount_task.done():
            self.mount_task.cancel()
            await asyncio.gather(self.mount_task,return_exceptions=True)

    async def snapshot(self):
        async with self.lock:
            if self.cached is None or time.monotonic()-self.cached_at>=30:
                headers=self.session_loader()['headers']
                async with httpx.AsyncClient(headers=headers,timeout=25,follow_redirects=False,transport=self.transport) as client:
                    volumes=await self.volumes(client)
                    warnings=[]
                    await self.usage(client,volumes,warnings)
                self.cached={'site':self.site.id,'volumes':sorted(volumes,key=lambda v:(v['zone'],v['name'])),
                             'updated_at':iso(time.time()),'warnings':warnings,
                             'console_url':self.site.console+'/afs/list'}
                self.cached_at=time.monotonic()
            data=copy.deepcopy(self.cached)
            interval=300 if self.mount_cache and all(v=='ok' for v in self.mount_cache['status'].values()) else 30
            if (self.mount_task is None or self.mount_task.done()) and (self.mount_cache is None or time.monotonic()-self.mount_checked_at>=interval):
                self.mount_task=asyncio.create_task(self.refresh_mounts(copy.deepcopy(data['volumes'])))
            data['mount_refreshing']=bool(self.mount_task and not self.mount_task.done())
            data['mount_updated_at']=self.mount_updated_at
            data['mount_status']=copy.deepcopy(self.mount_cache['status']) if self.mount_cache else {'CCI':'loading','ACP':'loading'}
            if self.mount_cache:
                for volume in data['volumes']:
                    volume['mounts']=copy.deepcopy(self.mount_cache['records'].get((volume['zone'],volume['id']),[]))
                data['warnings'].extend(self.mount_cache['warnings'])
            data['warnings']=sorted(set(data['warnings']))
            data['status']='partial' if data['warnings'] else 'ok'
            return data
