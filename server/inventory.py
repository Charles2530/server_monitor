"""Nominal GPU inventory for the user's two AEC2 clusters, independently of quotas."""
import asyncio
import copy
from .domain import finite_number
from .normalize import iso

TARGET_CLUSTERS = frozenset(('modeltoolchain','modeltoolchain-test'))
INVENTORY_TIMEOUT_SECONDS = 20


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


async def read_inventory(request, client, cluster, now, targets=TARGET_CLUSTERS):
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
    inventory=summarize_nodes(nodes,now)
    if inventory['abnormal_nodes'] is None or inventory['disabled_nodes'] is None:
        raise ValueError('Node health fields missing')
    return inventory


async def enrich_clusters(request, client, state, previous, now, targets=None, all_bound=False):
    allowed = ({c["id"].rsplit("/",1)[-1] for c in state["clusters"] if not c["id"].startswith("/subscriptions/public/")} if all_bound else targets or TARGET_CLUSTERS)
    targets=[c for c in state['clusters'] if c.get('id','').rsplit('/',1)[-1] in allowed]
    old={c['id']:c.get('inventory') for c in previous.get('clusters',[])}
    results=await asyncio.gather(*(asyncio.wait_for(read_inventory(request,client,c,now,allowed),timeout=INVENTORY_TIMEOUT_SECONDS) for c in targets),return_exceptions=True)
    failed=[]
    for cluster,result in zip(targets,results):
        if isinstance(result,Exception):
            cluster['inventory']=copy.deepcopy(old.get(cluster['id']))
            cluster['inventory_error']='总量或节点状态采集失败；保留旧值' if cluster['inventory'] else '总量或节点状态暂不可用'
            failed.append(cluster['id'].rsplit('/',1)[-1])
        else:
            cluster['inventory']=result
            cluster['inventory_error']=None
    state['source_status']['inventory']={
        'status':'error' if failed else 'ok',
        'updated_at':iso(now),
        'error':'集群总量获取失败：'+', '.join(failed) if failed else None,
    }
