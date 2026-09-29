from server.normalize import task_from_raw, metric_series, cluster_from_raw

def test_cci_json_device_and_allowlist():
    raw={'name':'app-1','uid':'uid','display_name':'xcharles','state':'RUNNING','replicas':2,'template':{'resource_spec':{'requests':{'device':'{"number": 2}'}},'containers':[{'password':'never'}]}}
    t=task_from_raw(raw,'CCI',0)
    assert t['gpu_count']==4 and t['replicas']==2
    assert 'never' not in str(t)

def test_acp_specs_are_totalled_once():
    t=task_from_raw({'name':'pt-1','roles':[{'total_replicas':9,'resource_spec':[{'replicas':2,'limits':{'device':'8'}},{'replicas':1,'limits':{'device':'4'}}]}]},'ACP',0)
    assert t['gpu_count']==20 and t['replicas']==3

def test_metrics_fractions_average_not_extrema():
    result={'metrics_result':[{'metrics_attrs':{'__name__':n},'values':[{'metric_time':'2026-09-29T07:00:00Z','values':v}]} for n,v in [('deployment_gpu_util',.5),('deployment_gpu_util_max',1),('deployment_gpu_util_min',0)]]}
    s=metric_series(result,'gpu','CCI')
    assert len(s)==1 and s[0]['points'][0][1]==50

def test_public_na_and_memory_units():
    assert cluster_from_raw({'name':'public','type':'public','reserved_number':'0'},0)['available_gpus'] is None
    c=cluster_from_raw({'name':'a','reserved_number':'0','reserved_cpu':'2','reserved_memory':'6608.00Gi'},0)
    assert c['available_gpus']==0 and c['available_memory_gib']==6608

def test_waiting_single_spec_uses_requested_replicas_before_allocation():
    raw={'name':'pt-q','state':'QUEUEING','roles':[{'total_replicas':4,'resource_spec':[{'replicas':0,'limits':{'device':'8'}}]}]}
    t=task_from_raw(raw,'ACP',0)
    assert t['replicas']==4 and t['gpu_count']==32
