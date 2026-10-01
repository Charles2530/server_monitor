"""Nominal GPU inventory for the user's two AEC2 clusters, independently of quotas."""
import asyncio
import copy
import math
from collections import Counter
from .domain import finite_number
from .normalize import iso

TARGET_CLUSTERS = frozenset(('modeltoolchain','modeltoolchain-test'))
INVENTORY_TIMEOUT_SECONDS = 20
ALLOCATION_TIMEOUT_SECONDS = 8


def nonnegative_integer(value):
    number=finite_number(value)
    if number is None or number < 0 or not number.is_integer():
        raise ValueError('Invalid inventory count')
    return int(number)


def summarize_nodes(nodes, now):
    identities=[node.get('uid') or node.get('id') for node in nodes]
    if any(not uid for uid in identities) or len(set(identities)) != len(nodes):
        raise ValueError('Incomplete or duplicate node identities')
    cards=[]
    for node in nodes:
        device=node.get('properties',{}).get('resources',{}).get('device') or {}
        cards.append(nonnegative_integer(device.get('number')))
    states=[node.get('state') for node in nodes]
    availability=[node.get('properties',{}).get('available_status') for node in nodes]
    return {
        'total_gpus':sum(cards), 'node_count':len(nodes),
        'abnormal_nodes':sum(state=='ERROR' for state in states) if all(states) else None,
        'abnormal_node_gpus':sum(count for count,state in zip(cards,states) if state=='ERROR') if all(states) else None,
        'disabled_nodes':sum(value=='DISABLED' for value in availability) if all(value in ('ENABLED','DISABLED') for value in availability) else None,
        'updated_at':iso(now),
    }


async def read_nodes(request, client, cluster, targets=TARGET_CLUSTERS):
    rid=cluster['id']
    if not rid.startswith('/subscriptions/') or rid.rsplit('/',1)[-1] not in targets:
        raise ValueError('Unexpected cluster resource ID')
    nodes,token,seen,expected=[], '1',set(),None
    while token:
        if token in seen:
            raise ValueError('Repeated inventory page')
        seen.add(token)
        payload=await request(client,'AEC2','/compute/aec2/data/v1'+rid+'/acns',params={'page_size':100,'page_token':token})
        total=nonnegative_integer(payload.get('total_size'))
        if not isinstance(payload.get('acns'),list) or (expected is not None and total!=expected):
            raise ValueError('Inventory changed during pagination')
        expected=total
        nodes.extend(payload['acns'])
        if len(nodes)>expected:
            raise ValueError('Unexpected inventory page size')
        token=str(payload.get('next_page_token') or '')
    if len(nodes)!=expected:
        raise ValueError('Incomplete inventory pages')
    return nodes


def summarize_allocation(nodes, observations, now):
    """Use scheduler allocations, never GPU utilization, to determine free cards."""
    identities=[n.get('uid') for n in nodes]
    observed=[o.get('uid') for o in observations]
    if (any(not uid for uid in identities) or len(set(identities))!=len(identities)
            or len(set(observed))!=len(observed) or set(observed)!=set(identities)):
        raise ValueError('Incomplete node allocation identities')
    by_uid={o['uid']:o for o in observations}
    available=[];idle=Counter()
    for node in nodes:
        props=node.get('properties') or {}
        if node.get('state')=='ERROR' or props.get('available_status')=='DISABLED' or props.get('status')=='UNSCHEDULABLE':
            continue
        if node.get('state')!='ACTIVE' or props.get('available_status')!='ENABLED' or props.get('status')!='READY':
            raise ValueError('Unknown scheduling health')
        resources=props.get('resources') or {};device=resources.get('device') or {}
        nominal=nonnegative_integer(device.get('number'))
        if not nominal:continue
        obs=by_uid[node['uid']];capacity=obs.get('capacity') or {};allocated=obs.get('allocated') or {}
        free={}
        for key in ('device','cpu','memory'):
            cap=finite_number(capacity.get(key));used=finite_number(allocated.get(key))
            if cap is None or used is None or not 0<=used<=cap:
                raise ValueError('Invalid node allocation')
            if key=='device' and (not cap.is_integer() or not used.is_integer() or cap!=nominal):
                raise ValueError('Invalid GPU allocation')
            free[key]=cap-used
        gpu_type=device.get('type')
        if not isinstance(gpu_type,str) or not gpu_type:raise ValueError('Unknown GPU type')
        available.append({'gpu_type':gpu_type,'free_gpus':int(free['device']),
                          'free_vcpu':free['cpu'],'free_memory_gib':free['memory']})
        if free['device']==nominal:idle[nominal]+=1
    return {'status':'ok','nodes':available,'idle_gpu_groups':[
        {'gpus_per_node':size,'nodes':count} for size,count in sorted(idle.items())],
        'updated_at':iso(now)}


def allocation_fit(allocation, spec, replicas):
    if allocation.get('status')!='ok':
        return {'status':'unknown','replica_slots':None,'message':'节点分配数据不可用，无法确认单节点资源。'}
    slots=0
    for node in allocation['nodes']:
        if node['gpu_type']!=spec['device']['type']:continue
        slots+=min(math.floor(node['free_gpus']/spec['device']['number']),
                   math.floor(node['free_vcpu']/spec['cpu']['vcpu_allocatable']),
                   math.floor(node['free_memory_gib']/spec['memory']['allocatable']))
    return {'status':'sufficient' if slots>=replicas else 'insufficient','replica_slots':slots,
            'message':f'节点 GPU / CPU / 内存可容纳 {slots} 个副本，需要 {replicas} 个副本。',
            'updated_at':allocation['updated_at']}


async def read_allocation(request, client, cluster, nodes, now):
    async def read():
        observations=[]
        # The console requests observations for one 20-node page at a time.
        for start in range(0,len(nodes),20):
            batch=nodes[start:start+20]
            payload=await request(client,'AEC2','/compute/aec2/data/v1'+cluster['id']+'/observe',
                                  params=[('acn_uid',n['uid']) for n in batch])
            rows=(payload.get('observations') or {}).get('acns')
            if not isinstance(rows,list) or payload.get('next_page_token') or nonnegative_integer(payload.get('total_size'))!=len(batch):
                raise ValueError('Incomplete node observations')
            if {o.get('uid') for o in rows}!={n['uid'] for n in batch} or len(rows)!=len(batch):
                raise ValueError('Mismatched node observations')
            observations.extend(rows)
        return summarize_allocation(nodes,observations,now)
    try:
        return await asyncio.wait_for(read(),timeout=ALLOCATION_TIMEOUT_SECONDS)
    except Exception:
        return {'status':'unknown','nodes':[],'idle_gpu_groups':None,'updated_at':iso(now),
                'error':'节点分配数据暂不可用，整组空闲数量未知'}


async def read_inventory(request, client, cluster, now, targets=TARGET_CLUSTERS, *, include_allocation=False):
    nodes=await read_nodes(request,client,cluster,targets)
    inventory=summarize_nodes(nodes,now)
    if inventory['abnormal_nodes'] is None or inventory['disabled_nodes'] is None:
        raise ValueError('Node health fields missing')
    if include_allocation:
        inventory['allocation']=await read_allocation(request,client,cluster,nodes,now)
    return inventory


async def enrich_clusters(request, client, state, previous, now, targets=None, all_bound=False):
    allowed = ({c["id"].rsplit("/",1)[-1] for c in state["clusters"] if not c["id"].startswith("/subscriptions/public/")} if all_bound else targets or TARGET_CLUSTERS)
    targets=[c for c in state['clusters'] if c.get('id','').rsplit('/',1)[-1] in allowed]
    old={c['id']:c.get('inventory') for c in previous.get('clusters',[])}
    results=await asyncio.gather(*(asyncio.wait_for(read_inventory(request,client,c,now,allowed,
        include_allocation=not all_bound and c['id'].rsplit('/',1)[-1] in TARGET_CLUSTERS),timeout=INVENTORY_TIMEOUT_SECONDS) for c in targets),return_exceptions=True)
    failed=[]
    for cluster,result in zip(targets,results):
        if isinstance(result,Exception):
            cluster['inventory']=copy.deepcopy(old.get(cluster['id']))
            cluster['inventory_error']='总量或节点状态采集失败；保留旧值' if cluster['inventory'] else '总量或节点状态暂不可用'
            failed.append(cluster['id'].rsplit('/',1)[-1])
        else:
            cluster['inventory']=result
            cluster['inventory_error']=None
            if result.get('allocation',{}).get('status')=='unknown':
                failed.append(cluster['id'].rsplit('/',1)[-1])
    state['source_status']['inventory']={
        'status':'error' if failed else 'ok',
        'updated_at':iso(now),
        'error':'集群总量或节点分配获取失败：'+', '.join(failed) if failed else None,
    }
