#!/usr/bin/env python3
"""Read local monitor SQLite snapshots and maintain a private Feishu task table."""
import argparse
from datetime import datetime
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

HOME = Path.home() / 'Library/Application Support/CharlesMonitorBitable'
MONITOR = Path.home() / 'Library/Application Support/CharlesCloudMonitor'
API = 'https://open.feishu.cn/open-apis'
SITES = {
    'maoshanwang': ('茅山王', 'https://console.ms-sc-01.maoshanwangtech.com',
        '/subscriptions/a14649f0-2e3e-4a08-91ca-5a7e435d7ada/resourceGroups/default/zones/ms-sc-01z/workspaces/modeltoolchain'),
    'sensecore': ('SenseCore', 'https://console.sensecore.cn/cn-sh-01',
        '/subscriptions/65b0bc94-2cf4-42a3-b054-9900bbf51b7e/resourceGroups/default/zones/cn-sh-01z/workspaces/fdfe760c-ecd5-4466-aa6f-3d4125b17e7f'),
}
SCHEMA = [('任务名称',1), ('站点',3), ('状态',3), ('GPU数量',2), ('GPU利用率 (%)',2),
          ('显存占用 (%)',2), ('数据状态',3), ('采集时间',5), ('同步时间',5),
          ('集群',1), ('类型',3), ('官方任务',15), ('同步标识',1)]
LOG = logging.getLogger('feishu-bitable')


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z','+00:00'))
        return dt.timestamp() if dt.tzinfo else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def fresh(value, now):
    return value is not None and -60 <= now-value <= 300


def plain(value):
    if isinstance(value, str): return value
    if isinstance(value, list):
        return ''.join(str(part.get('text','')) for part in value if isinstance(part,dict))
    return ''


def load_snapshot(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        row = db.execute('SELECT payload FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
    return json.loads(row[0]) if row else {}


def source_health(snapshot, kind, now):
    source = snapshot.get('source_status',{}).get(kind,{})
    stamp = timestamp(source.get('updated_at'))
    if source.get('status') != 'ok': return '采集不可用', stamp
    if not fresh(stamp,now): return '数据已过期', stamp
    return '正常', stamp


def metric(task, name, now):
    value = task.get('metrics',{}).get(name)
    if (type(value) not in (float,int) or not math.isfinite(value) or not 0<=value<=100
            or name in task.get('failed_metrics',[])
            or not fresh(timestamp(task.get('metric_updated_at',{}).get(name)),now)):
        return None
    return round(value,2)


def task_url(site, task):
    kind = task.get('kind')
    ident = str(task.get('id',''))
    if kind not in ('ACP','CCI') or not ident.startswith(kind+':'): return None
    name = ident.split(':',1)[1]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}',name): return None
    _, console, workspace = SITES[site]
    if kind == 'CCI': return console+'/cci/app/detail?'+urlencode({'workspace':workspace,'name':name})
    return console+'/acp/list/detail?'+urlencode({'id':workspace+'/trainingJobs/'+name,'workspace':workspace,'tab':'detail'})


def task_fields(site, task, snapshot, now):
    health, stamp = source_health(snapshot,task.get('kind'),now)
    gpu = metric(task,'gpu',now) if health=='正常' else None
    memory = metric(task,'gpu_memory',now) if health=='正常' else None
    live = health=='正常' and task.get('state')=='running'
    if health=='正常' and (gpu is None or memory is None): health='指标不完整'
    url = task_url(site,task)
    count = task.get('gpu_count')
    if type(count) not in (int,float) or not math.isfinite(count) or count<0: count=None
    return {'任务名称':str(task.get('name',''))[:500], '站点':SITES[site][0],
            '状态':'运行中' if live else '待确认', 'GPU数量':count,
            'GPU利用率 (%)':gpu, '显存占用 (%)':memory, '数据状态':health,
            '采集时间':int(stamp*1000) if stamp is not None else None,
            '同步时间':int(now*1000), '集群':str(task.get('cluster') or '未知'),
            '类型':task.get('kind'), '官方任务':{'text':'打开任务','link':url} if url else None,
            '同步标识':site+'/'+str(task['id'])}


def plan_changes(snapshots, records, now):
    existing={}
    for record in records:
        key=plain(record.get('fields',{}).get('同步标识'))
        if not key: continue
        if key in existing: raise ValueError('Duplicate synchronization key in destination table')
        existing[key]=record
    desired={}
    for site,snapshot in snapshots.items():
        if site not in SITES: continue
        for task in snapshot.get('tasks',[]):
            if ('charles' not in str(task.get('name','')).casefold() or not task.get('id')
                    or task.get('kind') not in ('CCI','ACP')): continue
            fields=task_fields(site,task,snapshot,now)
            desired[fields['同步标识']]=fields
    creates=[];updates=[]
    for key,fields in desired.items():
        if key in existing: updates.append({'record_id':existing[key]['record_id'],'fields':fields})
        else: creates.append(fields)
    for key,record in existing.items():
        if key in desired: continue
        site,sep,ident=key.partition('/')
        if site not in snapshots or not sep or not ident.startswith(('ACP:','CCI:')): continue
        fields=record.get('fields',{})
        health,_=source_health(snapshots[site],ident.split(':')[0],now)
        updates.append({'record_id':record['record_id'],'fields':{
            '状态':'待确认','数据状态':'未在运行列表' if health=='正常' else health,
            'GPU利用率 (%)':None,'显存占用 (%)':None,
            '采集时间':fields.get('采集时间'),'同步时间':int(now*1000)}})
    return creates,updates


class APIError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None


def http_transport(method,path,body,token):
    if not path.startswith('/') or path.startswith('//'): raise ValueError('Invalid API path')
    headers={'Content-Type':'application/json; charset=utf-8'}
    if token: headers['Authorization']='Bearer '+token
    data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode() if body is not None else None
    req=Request(API+path,data=data,headers=headers,method=method)
    try:
        with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise APIError('Feishu HTTP '+str(exc.code)) from None
    except (URLError,TimeoutError,ValueError,OSError):
        raise APIError('Feishu transport failed; outcome may be unknown') from None


class BitableClient:
    def __init__(self,config,transport=None):
        self.config=config
        self.transport=transport or http_transport
        self.token=None;self.token_until=0
        self.base='/bitable/v1/apps/'+quote(config['app_token'],safe='')+'/tables/'+quote(config['table_id'],safe='')

    def request(self,method,path,body=None):
        if time.time()>=self.token_until:
            result=self.transport('POST','/auth/v3/tenant_access_token/internal',
                                  {'app_id':self.config['app_id'],'app_secret':self.config['app_secret']},None)
            self.check(result)
            self.token=result['tenant_access_token']
            self.token_until=time.time()+max(0,result.get('expire',0)-120)
        result=self.transport(method,path,body,self.token)
        if result.get('code') in (99991661,99991663,99991664): self.token_until=0
        self.check(result)
        return result.get('data',{})

    @staticmethod
    def check(result):
        code=result.get('code')
        if type(code) is not int or code!=0:
            raise APIError('Feishu code='+str(code if type(code) is int else 'invalid'))

    def list_items(self,path,method="GET",body=None):
        items=[];page=None;seen=set()
        while True:
            params={'page_size':100}
            if page: params['page_token']=page
            data=self.request(method,path+'?'+urlencode(params),body)
            items.extend(data.get('items',[]))
            if not data.get('has_more'): return items
            page=data.get('page_token')
            if not page or page in seen: raise APIError('Invalid pagination response')
            seen.add(page)

    def list_records(self):
        return self.list_items(self.base+'/records/search','POST',{'automatic_fields':False})

    def ensure_schema(self,schema=SCHEMA):
        fields=self.list_items(self.base+'/fields')
        by_name={f['field_name']:f for f in fields}
        primary_name=schema[0][0]
        if primary_name not in by_name:
            primary=next((f for f in fields if f.get('is_primary')),None)
            if primary and primary.get('type')==1:
                self.request('PUT',self.base+'/fields/'+quote(primary['field_id'],safe=''),
                             {'field_name':primary_name,'type':1})
                by_name[primary_name]={'type':1}
        for name,typ in schema:
            if name in by_name:
                if by_name[name]['type']!=typ: raise ValueError('Destination field has incompatible type: '+name)
                continue
            body={'field_name':name,'type':typ}
            if typ==5: body['property']={'date_formatter':'yyyy/MM/dd HH:mm'}
            self.request('POST',self.base+'/fields',body)
            time.sleep(0.5)

    def update_records(self,records):
        for offset in range(0,len(records),100):
            self.request('POST',self.base+'/records/batch_update',{'records':records[offset:offset+100]})

    def create_record(self,fields,token):
        return self.request('POST',self.base+'/records/batch_create?'+urlencode({'client_token':token}),
                            {'records':[{'fields':fields}]})


class Sync:
    """Journal unknown create outcomes so retries use identical UUIDv4 and payload."""
    def __init__(self,path,client,planner=plan_changes,site=None):
        self.client=client
        self.planner=planner
        self.site=site
        self.db=sqlite3.connect(str(path))
        self.db.execute('CREATE TABLE IF NOT EXISTS pending (key TEXT PRIMARY KEY,token TEXT NOT NULL,fields TEXT NOT NULL)')
        self.db.commit()

    def run(self,snapshots,now):
        if self.site is not None:snapshots={self.site:snapshots.get(self.site,{})}
        records=self.client.list_records()
        creates,updates=self.planner(snapshots,records,now)
        remote_keys={plain(r.get('fields',{}).get('同步标识')) for r in records}
        with self.db:
            for key in remote_keys:self.db.execute('DELETE FROM pending WHERE key=?',(self.client.base+'/'+key,))
        self.client.update_records(updates)
        for fields in creates:
            current_fields=fields
            key=self.client.base+'/'+fields['同步标识']
            row=self.db.execute('SELECT token,fields FROM pending WHERE key=?',(key,)).fetchone()
            if row is None:
                token=str(uuid.uuid4())
                with self.db:
                    self.db.execute('INSERT INTO pending VALUES(?,?,?)',(key,token,json.dumps(fields,ensure_ascii=False)))
            else:
                token,encoded=row;fields=json.loads(encoded)
            result=self.client.create_record(fields,token)
            if fields != current_fields:
                created=result.get('records',[])
                if len(created)!=1 or not created[0].get('record_id'):
                    raise APIError('Create response missing record identity')
                self.client.update_records([{'record_id':created[0]['record_id'],'fields':current_fields}])
            with self.db:self.db.execute('DELETE FROM pending WHERE key=?',(key,))
        return {'created':len(creates),'updated':len(updates),'time':now}

    def close(self):self.db.close()


def table_targets(config):
    targets=[]
    if 'site_tables' in config:
        sites=config['site_tables']
        if not isinstance(sites,dict) or set(sites)!=set(SITES):
            raise ValueError('site_tables must configure both known sites')
        for site in SITES:
            tables=sites[site]
            if not isinstance(tables,dict) or set(tables)!={'tasks','resources'}:
                raise ValueError('Each site needs task and resource tables')
            for kind in ('tasks','resources'):
                targets.append((site+'.'+kind,tables[kind],kind,site))
    else:
        targets.append(('tasks',config.get('table_id'),'tasks',None))
        if config.get('resource_table_id') is not None:
            targets.append(('resources',config['resource_table_id'],'resources',None))
    ids=[table for _,table,_,_ in targets]
    if any(not isinstance(table,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',table) for table in ids):
        raise ValueError('Invalid table ID')
    if len(set(ids))!=len(ids):raise ValueError('Every sync target must use a separate table')
    return targets


def read_config(path):
    if path.stat().st_mode & 0o077: raise ValueError('Config must have mode 0600')
    config=json.loads(path.read_text())
    for key in ('app_id','app_secret','app_token'):
        if not isinstance(config.get(key),str) or not re.fullmatch(r'[A-Za-z0-9_-]+',config[key]):
            raise ValueError('Missing or invalid '+key)
    interval=config.get('interval_seconds',300)
    if type(interval) is not int or not 60<=interval<=3600: raise ValueError('Invalid sync interval')
    table_targets(config)
    return config


def snapshots_from_disk():
    snapshots={}
    for site in SITES:
        path=MONITOR/'data'/('sensecore/' if site=='sensecore' else '')/'monitor.sqlite3'
        try:snapshots[site]=load_snapshot(path)
        except (OSError,sqlite3.Error,ValueError):
            LOG.warning('%s local snapshot unavailable',site)
            snapshots[site]={}
    return snapshots


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=HOME/'config.json')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--init',action='store_true')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    try:
        if args.dry_run:
            from feishu_resources import plan_resource_changes
            snapshots=snapshots_from_disk()
            creates,_=plan_changes(snapshots,[],time.time())
            resources,_=plan_resource_changes(snapshots,[],time.time())
            print(json.dumps({'rows':len(creates),'sites':sorted({f['站点'] for f in creates}),
                              'health':sorted({f['数据状态'] for f in creates}),
                              'resource_rows':len(resources)},ensure_ascii=False))
            return
        config=read_config(args.config)
        lock=open(args.config.parent/'sync.lock','a')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from feishu_resources import RESOURCE_SCHEMA,plan_resource_changes
        jobs=[]
        for name,table,kind,site in table_targets(config):
            client=BitableClient({**config,'table_id':table})
            schema,planner=(SCHEMA,plan_changes) if kind=='tasks' else (RESOURCE_SCHEMA,plan_resource_changes)
            if args.init:client.ensure_schema(schema)
            journal='sync.sqlite3' if name=='tasks' else name+'.sqlite3'
            status_name='status.json' if name=='tasks' else name+'.status.json'
            jobs.append((name,Sync(args.config.parent/journal,client,planner,site),status_name))
        while True:
            snapshots=snapshots_from_disk()
            for name,sync,status_name in jobs:
                try:
                    result=sync.run(snapshots,time.time())
                    status=args.config.parent/status_name
                    temporary=status.with_suffix('.tmp')
                    temporary.write_text(json.dumps(result))
                    temporary.replace(status)
                    LOG.info('%s sync completed: created=%d updated=%d',name,result['created'],result['updated'])
                except Exception as exc:
                    LOG.error('%s sync failed (%s); retry next cycle',name,type(exc).__name__)
                    if args.once or args.init:raise
            if args.once or args.init:
                for _,sync,_ in jobs:sync.close()
                return
            time.sleep(config.get('interval_seconds',300))
    except Exception as exc:
        LOG.error('Stopped (%s); check config, permissions and service status',type(exc).__name__)
        raise SystemExit(1)


if __name__=='__main__':main()
