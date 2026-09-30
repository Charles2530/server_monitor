"""Allowlisted normalization of the console's read-only API responses."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode
from .domain import finite_number, merge_series
from .acp_states import STOPPABLE_STATES, is_charles
from .queue_states import QUEUE_LABELS


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def device_count(spec):
    resources = spec.get('limits') or spec.get('requests') or {}
    value = resources.get('device', resources.get('nvidia.com/gpu'))
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        if isinstance(parsed, dict):
            value = parsed.get('number')
    except (ValueError, TypeError):
        pass
    return finite_number(value)


def task_from_raw(raw, kind, now):
    if kind == 'CCI':
        replicas = finite_number(raw.get('replicas'))
        count = device_count(raw.get('template', {}).get('resource_spec', {}))
        gpus = None if count is None or replicas is None else count * replicas
    else:
        counts=[]
        for role in raw.get('roles',[]):
            specs=role.get('resource_spec',[])
            for spec in specs:
                assigned=finite_number(spec.get('replicas'))
                if len(specs)==1 and assigned in (None,0):
                    assigned=finite_number(role.get('total_replicas')) or assigned
                counts.append((assigned,device_count(spec)))
        replicas = sum(r for r, _ in counts) if counts and all(r is not None for r, _ in counts) else None
        gpus = sum(r*g for r,g in counts) if counts and all(r is not None and g is not None for r,g in counts) else None
    return {'id': kind + ':' + raw['name'], 'source_id': raw['name'], 'uid': raw.get('uid'),
            'kind': kind, 'name': raw.get('display_name', raw['name']),
            'cluster': raw.get('resource_pool', {}).get('display_name') or raw.get('resource_pool', {}).get('name'),
            'state': 'running', 'gpu_count': gpus, 'replicas': replicas,
            'can_stop': kind=='ACP' and raw.get('state') in STOPPABLE_STATES,
            'created_at':raw.get('create_time'),
            'updated_at': None, 'observed_at': iso(now), 'url': None,
            'metrics': dict.fromkeys(('cpu','gpu','memory','gpu_memory')),
            'series': [], 'workers': []}


def waiting_from_raw(raw, now, kind='ACP'):
    task=task_from_raw(raw,kind,now)
    task['can_stop'] = task['can_stop'] and is_charles(task['name'])
    owner=(raw.get('ownership') or {}).get('user_name')
    return {**{key:task[key] for key in ('id','kind','name','cluster','gpu_count','replicas','created_at','observed_at','can_stop')},
            'owner':owner if isinstance(owner,str) else None,
            'state':raw['state'], 'state_label':QUEUE_LABELS[kind][raw['state']]}


def metric_series(payload, metric, kind):
    result = []
    titles = {'cpu':'CPU','gpu':'GPU','memory':'内存','gpu_memory':'显存'}
    for item in payload.get('metrics_result', []):
        attrs = item.get('metrics_attrs', {})
        name = attrs.get('__name__', '')
        # CCI deployment panels emit avg/min/max. Only the plain series is the mean.
        if kind == 'CCI' and name.endswith(('_min','_max')):
            continue
        label = attrs.get('exported_pod') or '应用平均'
        if attrs.get('gpu') is not None:
            label += ' / GPU ' + str(attrs['gpu'])
        points = []
        for p in item.get('values', []):
            try:
                timestamp = datetime.fromisoformat(p['metric_time'].replace('Z','+00:00')).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            value = finite_number(p.get('values'))
            points.append([timestamp, None if value is None else value*100])
        result.append({'metric':metric, 'label':titles[metric]+' · '+label, 'points':points})
    return merge_series(result)


def cluster_from_raw(raw, now):
    public = raw.get('type','').lower() == 'public' or raw.get('name') == 'public'
    memory = str(raw.get('reserved_memory',''))
    match = re.fullmatch(r'([\d.]+)Gi', memory)
    return {'id':raw.get('id',raw['name']), 'name':raw.get('display_name') or raw['name'],
            'available_gpus': None if public else finite_number(raw.get('reserved_number')),
            'available_vcpu': None if public else finite_number(raw.get('reserved_cpu')),
            'available_memory_gib': None if public or not match else finite_number(match[1]),
            'updated_at':iso(now)}


def task_console_url(task,site):
    kind=task.get('kind')
    task_id=task.get('id','')
    if kind not in ('CCI','ACP') or not isinstance(task_id,str) or not task_id.startswith(kind+':'):
        return None
    name=task_id.split(':',1)[1]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}',name):return None
    if kind=='CCI':
        return site.console+'/cci/app/detail?'+urlencode({'workspace':site.workspace,'name':name})
    return site.console+'/acp/list/detail?'+urlencode({'id':site.workspace+'/trainingJobs/'+name,'workspace':site.workspace,'tab':'detail'})
