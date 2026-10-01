import copy
from datetime import datetime, timezone

import pytest
from scripts.feishu_bitable import BitableClient, Sync
from scripts.feishu_resources import RESOURCE_SCHEMA, resource_fields, plan_resource_changes

T=1790850000
def iso(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
def cluster(**changes):
    row={'id':'/subscriptions/ours/aec2s/h100','name':'H100','available_gpus':0,
         'updated_at':iso(T),'inventory_error':None,
         'inventory':{'total_gpus':32,'node_count':4,'abnormal_nodes':1,
                      'abnormal_node_gpus':8,'updated_at':iso(T)}}
    row.update(changes);return row
def snapshot(rows=None,status='ok',stamp=T):
    return {'clusters':[cluster()] if rows is None else rows,
            'source_status':{'resources':{'status':status,'updated_at':iso(stamp)}}}

def test_resource_zero_and_inventory_are_separate():
    f=resource_fields('sensecore',cluster(),snapshot(),T+60)
    assert f['可用卡数']==0 and f['总卡数']==32 and f['异常节点卡数']==8
    assert f['余量采集时间']==f['总量采集时间']==T*1000
    assert f['同步时间']==(T+60)*1000
    assert f['数据状态']=='正常' and '已用卡数' not in f

@pytest.mark.parametrize('bad',[None,True,-1,float('nan'),'5'])
def test_invalid_capacity_is_unknown_not_zero(bad):
    f=resource_fields('sensecore',cluster(available_gpus=bad),snapshot(),T)
    assert f['可用卡数'] is None and f['数据状态']!='正常'

def test_inventory_error_and_staleness_do_not_hide_good_capacity():
    for c in [cluster(inventory_error='failed'),cluster(inventory=None),
              cluster(inventory={**cluster()['inventory'],'updated_at':iso(T-600)})]:
        f=resource_fields('sensecore',c,snapshot(),T)
        assert f['总卡数'] is None and f['异常节点卡数'] is None
        assert f['可用卡数']==0 and f['数据状态']!='正常'

def test_failed_or_stale_capacity_does_not_republish_cached_numbers():
    for s in [snapshot(status='error'),snapshot(stamp=T-600)]:
        f=resource_fields('sensecore',cluster(),s,T)
        assert f['可用卡数'] is None and f['数据状态']!='正常'
    f=resource_fields('sensecore',cluster(updated_at=iso(T-600)),snapshot(),T)
    assert f['可用卡数'] is None

def test_site_keys_are_isolated_and_public_pool_is_excluded():
    s=snapshot([cluster(),cluster(id='/subscriptions/public/aec2s/public',name='Public')])
    creates,updates=plan_resource_changes({'sensecore':s,'maoshanwang':s},[],T)
    assert len(creates)==2 and len({f['同步标识'] for f in creates})==2 and not updates

def test_missing_cluster_clears_counts_and_preserves_row():
    fields=resource_fields('sensecore',cluster(),snapshot(),T)
    row={'record_id':'r1','fields':fields}
    creates,updates=plan_resource_changes({'sensecore':snapshot([])},[row],T+60)
    assert not creates and updates[0]['record_id']=='r1'
    assert updates[0]['fields']['数据状态']=='未在集群列表'
    for name,kind in RESOURCE_SCHEMA:
        if kind==2:assert updates[0]['fields'][name] is None

def test_duplicate_remote_keys_abort_and_rich_text_keys_work():
    f=resource_fields('sensecore',cluster(),snapshot(),T)
    row={'record_id':'r1','fields':{**f,'同步标识':[{'text':f['同步标识']}]}}
    creates,updates=plan_resource_changes({'sensecore':snapshot()},[row],T)
    assert not creates and len(updates)==1
    with pytest.raises(ValueError,match='Duplicate'):
        plan_resource_changes({'sensecore':snapshot()},[row,row],T)

def test_generic_schema_renames_primary_to_cluster():
    calls=[]
    def transport(method,path,body,token):
        calls.append((method,path,body))
        if path.endswith('internal'):return {'code':0,'tenant_access_token':'token','expire':7200}
        if method=='GET':return {'code':0,'data':{'items':[{'field_id':'f','field_name':'文本','is_primary':True,'type':1}]}}
        return {'code':0,'data':{}}
    client=BitableClient({'app_id':'test','app_secret':'secret','app_token':'app','table_id':'tbl'},transport)
    client.ensure_schema([('集群',1)])
    assert calls[-1][2]=={'field_name':'集群','type':1}

def test_resource_sync_restarts_without_duplicate_rows(tmp_path):
    class Client:
        base='resource-table'
        rows=[]
        def list_records(self):return copy.deepcopy(self.rows)
        def update_records(self,rows):
            for r in rows:self.rows[0]['fields'].update(r['fields'])
        def create_record(self,fields,token):
            self.rows.append({'record_id':'r1','fields':copy.deepcopy(fields)})
            return {'records':self.rows}
    client=Client();path=tmp_path/'sync.sqlite3'
    sync=Sync(path,client,planner=plan_resource_changes)
    assert sync.run({'sensecore':snapshot()},T)['created']==1
    sync.close();sync=Sync(path,client,planner=plan_resource_changes)
    assert sync.run({'sensecore':snapshot()},T+60)['updated']==1
    assert len(client.rows)==1
    sync.close()
