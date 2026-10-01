import copy
import json
import sqlite3
from datetime import datetime, timezone

import pytest
from scripts.feishu_bitable import load_snapshot, task_fields, plan_changes, BitableClient, APIError

T=1790850000

def iso(t): return datetime.fromtimestamp(t,timezone.utc).isoformat()

def task(**kw):
    row={'id':'ACP:one','name':'charles-one','kind':'ACP','state':'running','gpu_count':8,
         'cluster':'test','metrics':{'gpu':0,'gpu_memory':35.2},
         'metric_updated_at':{'gpu':iso(T),'gpu_memory':iso(T)},'failed_metrics':[]}
    row.update(kw);return row

def snap(rows=None,status='ok',ts=T):
    return {'tasks':rows if rows is not None else [task()],
            'source_status':{'ACP':{'status':status,'updated_at':iso(ts)},'CCI':{'status':status,'updated_at':iso(ts)}}}

def remote(fields,ident='rec1'):
    return {'record_id':ident,'fields':fields}

def test_reads_database_without_creating_or_mutating_it(tmp_path):
    p=tmp_path/'monitor.sqlite3'
    with pytest.raises(sqlite3.OperationalError): load_snapshot(p)
    assert not p.exists()
    with sqlite3.connect(p) as db:
        db.execute('create table snapshots(id integer primary key,captured real,payload text)')
        db.execute('insert into snapshots values(1,?,?)',(T,json.dumps(snap())))
    before=p.read_bytes()
    assert load_snapshot(p)==snap()
    assert p.read_bytes()==before

def test_fields_keep_zero_metrics_and_separate_timestamps():
    fields=task_fields('sensecore',task(),snap(),T+60)
    assert fields['GPU利用率 (%)']==0
    assert fields['显存占用 (%)']==35.2
    assert fields['状态']=='运行中'
    assert fields['采集时间']==T*1000
    assert fields['同步时间']==(T+60)*1000
    assert fields['同步标识']=='sensecore/ACP:one'
    assert fields['官方任务']['link'].startswith('https://console.sensecore.cn/')

@pytest.mark.parametrize('metric',[None,float('nan'),float('inf'),-1,101,'5',True])
def test_invalid_metrics_are_empty(metric):
    fields=task_fields('sensecore',task(metrics={'gpu':metric}),snap(),T)
    assert fields['GPU利用率 (%)'] is None
    assert fields['数据状态']=='指标不完整'

def test_stale_failed_metrics_and_failed_source_are_not_live():
    failed=task(failed_metrics=['gpu'])
    assert task_fields('sensecore',failed,snap(),T)['GPU利用率 (%)'] is None
    old=task(metric_updated_at={'gpu':iso(T-600),'gpu_memory':iso(T)})
    assert task_fields('sensecore',old,snap(),T)['GPU利用率 (%)'] is None
    for snapshot in [snap(status='error'),snap(ts=T-600)]:
        fields=task_fields('sensecore',task(),snapshot,T)
        assert fields['状态']=='待确认' and fields['GPU利用率 (%)'] is None
        assert fields['数据状态']!='正常'

def test_first_sync_filter_and_site_isolation():
    rows=[task(),task(name='someone-else',id='ACP:other')]
    creates,updates=plan_changes({'sensecore':snap(rows),'maoshanwang':snap(rows)},[],T)
    assert len(creates)==2 and not updates
    assert len({c['同步标识'] for c in creates})==2

def test_existing_remote_record_is_updated_without_new_row():
    fields=task_fields('sensecore',task(),snap(),T)
    # Feishu returns text fields as rich-text arrays on some read endpoints.
    fields['同步标识']=[{'type':'text','text':fields['同步标识']}]
    creates,updates=plan_changes({'sensecore':snap()},[remote(fields)],T+60)
    assert creates==[] and len(updates)==1 and updates[0]['record_id']=='rec1'

def test_disappearance_keeps_row_and_never_fabricates_terminal_state():
    fields=task_fields('sensecore',task(),snap(),T)
    creates,updates=plan_changes({'sensecore':snap([])},[remote(fields)],T+60)
    f=updates[0]['fields']
    assert not creates and f['状态']=='待确认'
    assert f['数据状态']=='未在运行列表'
    assert f['采集时间']==T*1000 and f['GPU利用率 (%)'] is None

def test_source_failure_preserves_row_with_health_warning():
    fields=task_fields('sensecore',task(),snap(),T)
    creates,updates=plan_changes({'sensecore':{}},[remote(fields)],T+60)
    assert not creates and updates[0]['fields']['数据状态']=='采集不可用'

def test_duplicate_sync_keys_fail_before_any_write():
    f=task_fields('sensecore',task(),snap(),T)
    with pytest.raises(ValueError,match='Duplicate'):
        plan_changes({'sensecore':snap()},[remote(f),remote(f,'rec2')],T)

def test_unrelated_remote_rows_untouched():
    assert plan_changes({},[remote({'任务名称':'manual note'})],T)==([],[])

def test_client_checks_application_errors_and_redacts_body():
    def transport(method,path,body,token):
        if path.endswith('internal'): return {'code':0,'tenant_access_token':'sensitive','expire':7200}
        return {'code':1254302,'msg':'sensitive detail'}
    client=BitableClient({'app_id':'test','app_secret':'secret','app_token':'app','table_id':'tbl'},transport=transport)
    with pytest.raises(APIError) as error: client.list_records()
    assert '1254302' in str(error.value) and 'sensitive' not in str(error.value)

def test_client_reads_all_pages():
    calls=[]
    def transport(method,path,body,token):
        calls.append(path)
        if path.endswith('internal'):return {'code':0,'tenant_access_token':'token','expire':7200}
        if 'page_token=p2' in path:return {'code':0,'data':{'items':[remote({'同步标识':'second'},'rec2')],'has_more':False}}
        return {'code':0,'data':{'items':[remote({'同步标识':'first'})],'has_more':True,'page_token':'p2'}}
    client=BitableClient({'app_id':'test','app_secret':'secret','app_token':'app','table_id':'tbl'},transport=transport)
    assert len(client.list_records())==2
    assert len(calls)==3

from scripts.feishu_bitable import Sync

class MemoryClient:
    base='/bitable/v1/apps/app/tables/tbl'
    def __init__(self):
        self.rows=[];self.tokens=[];self.bodies=[];self.fail_after_create=False;self.fail_before_create=False
    def list_records(self):return copy.deepcopy(self.rows)
    def update_records(self,records):
        for record in records:
            for row in self.rows:
                if row['record_id']==record['record_id']:row['fields'].update(record['fields'])
    def create_record(self,fields,token):
        self.tokens.append(token);self.bodies.append(copy.deepcopy(fields))
        if self.fail_before_create:
            self.fail_before_create=False;raise TimeoutError()
        self.rows.append(remote(copy.deepcopy(fields),'rec'+str(len(self.rows)+1)))
        if self.fail_after_create:
            self.fail_after_create=False;raise TimeoutError()
        return {'records':[copy.deepcopy(self.rows[-1])]}


def test_repeated_sync_and_restart_update_same_row(tmp_path):
    client=MemoryClient();p=tmp_path/'sync.sqlite3';sync=Sync(p,client)
    sync.run({'sensecore':snap()},T)
    sync.close();sync=Sync(p,client)
    sync.run({'sensecore':snap()},T+60)
    assert len(client.rows)==1 and len(client.tokens)==1
    assert client.rows[0]['fields']['同步时间']==(T+60)*1000


def test_unknown_create_result_is_reconciled_without_duplicate(tmp_path):
    client=MemoryClient();client.fail_after_create=True
    sync=Sync(tmp_path/'sync.sqlite3',client)
    with pytest.raises(TimeoutError):sync.run({'sensecore':snap()},T)
    sync.run({'sensecore':snap()},T+60)
    assert len(client.rows)==1 and len(client.tokens)==1
    assert sync.db.execute('SELECT count(*) FROM pending').fetchone()[0]==0


def test_create_retry_reuses_uuid_and_identical_body_after_restart(tmp_path):
    import uuid
    client=MemoryClient();client.fail_before_create=True
    p=tmp_path/'sync.sqlite3';sync=Sync(p,client)
    with pytest.raises(TimeoutError):sync.run({'sensecore':snap()},T)
    sync.close();sync=Sync(p,client)
    sync.run({'sensecore':snap()},T+60)
    assert len(client.rows)==1
    assert client.tokens[0]==client.tokens[1] and uuid.UUID(client.tokens[0]).version==4
    assert client.bodies[0]==client.bodies[1]

def test_replayed_create_is_refreshed_before_sync_reports_success(tmp_path):
    client=MemoryClient();client.fail_before_create=True
    sync=Sync(tmp_path/'sync.sqlite3',client)
    with pytest.raises(TimeoutError):sync.run({'sensecore':snap()},T)
    sync.run({'sensecore':snap()},T+3600)
    f=client.rows[0]['fields']
    assert f['状态']=='待确认' and f['数据状态']=='数据已过期'
    assert f['GPU利用率 (%)'] is None and f['同步时间']==(T+3600)*1000


def test_record_search_uses_documented_post_endpoint():
    calls=[]
    def transport(method,path,body,token):
        calls.append((method,path,body))
        if path.endswith('internal'):return {'code':0,'tenant_access_token':'token','expire':7200}
        return {'code':0,'data':{'items':[],'has_more':False}}
    client=BitableClient({'app_id':'test','app_secret':'secret','app_token':'app','table_id':'tbl'},transport=transport)
    client.list_records()
    assert calls[-1][0]=='POST' and '/records/search?' in calls[-1][1]
    assert calls[-1][2]=={'automatic_fields':False}
