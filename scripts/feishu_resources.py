"""Cluster GPU counts from the same read-only local monitor snapshots."""
import math

try:
    from .feishu_bitable import SITES, fresh, plain, source_health, timestamp
except ImportError:
    from feishu_bitable import SITES, fresh, plain, source_health, timestamp

RESOURCE_SCHEMA=[('集群',1),('站点',3),('可用卡数',2),('总卡数',2),
                 ('异常节点卡数',2),('节点数',2),('异常节点数',2),('数据状态',3),
                 ('余量采集时间',5),('总量采集时间',5),('同步时间',5),('同步标识',1)]
INVENTORY_FIELDS={'总卡数':'total_gpus','异常节点卡数':'abnormal_node_gpus',
                  '节点数':'node_count','异常节点数':'abnormal_nodes'}


def count(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        return None
    return value


def resource_fields(site,cluster,snapshot,now):
    health,_=source_health(snapshot,'resources',now)
    stamp=timestamp(cluster.get('updated_at'))
    available=count(cluster.get('available_gpus')) if health=='正常' and fresh(stamp,now) else None
    inventory=cluster.get('inventory') or {}
    inventory_stamp=timestamp(inventory.get('updated_at'))
    inventory_ok=not cluster.get('inventory_error') and fresh(inventory_stamp,now)
    values={name:count(inventory.get(key)) if inventory_ok else None
            for name,key in INVENTORY_FIELDS.items()}
    if available is None:
        if health=='正常':health='余量不可用' if fresh(stamp,now) else '余量已过期'
    if any(v is None for v in values.values()):
        health='总量不可用' if health=='正常' else '数据不完整'
    return {'集群':str(cluster.get('name') or cluster['id'].rsplit('/',1)[-1]),
            '站点':SITES[site][0],'可用卡数':available,**values,'数据状态':health,
            '余量采集时间':int(stamp*1000) if stamp is not None else None,
            '总量采集时间':int(inventory_stamp*1000) if inventory_stamp is not None else None,
            '同步时间':int(now*1000),'同步标识':site+'/'+cluster['id']}


def plan_resource_changes(snapshots,records,now):
    existing={}
    for row in records:
        key=plain(row.get('fields',{}).get('同步标识'))
        if not key:continue
        if key in existing:raise ValueError('Duplicate synchronization key in destination table')
        existing[key]=row
    desired={}
    for site,snapshot in snapshots.items():
        if site not in SITES:continue
        for cluster in snapshot.get('clusters',[]):
            ident=cluster.get('id','')
            if not ident.startswith('/subscriptions/') or ident.startswith('/subscriptions/public/'):continue
            fields=resource_fields(site,cluster,snapshot,now)
            if fields['同步标识'] in desired:raise ValueError('Duplicate source cluster identity')
            desired[fields['同步标识']]=fields
    creates=[];updates=[]
    for key,fields in desired.items():
        if key in existing:updates.append({'record_id':existing[key]['record_id'],'fields':fields})
        else:creates.append(fields)
    for key,row in existing.items():
        site,_,ident=key.partition('/')
        if key in desired or site not in snapshots or not ident.startswith('/subscriptions/'):continue
        health,_=source_health(snapshots[site],'resources',now)
        fields={name:None for name,typ in RESOURCE_SCHEMA if typ==2}
        fields.update({'数据状态':'未在集群列表' if health=='正常' else health,'同步时间':int(now*1000)})
        updates.append({'record_id':row['record_id'],'fields':fields})
    return creates,updates
