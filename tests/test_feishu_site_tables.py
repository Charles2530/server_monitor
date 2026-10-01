import copy
import json

import pytest
from scripts.feishu_bitable import Sync, read_config, table_targets

CONFIG={'app_id':'app','app_secret':'secret','app_token':'base','interval_seconds':60,
        'site_tables':{'maoshanwang':{'tasks':'tbl_m_tasks','resources':'tbl_m_gpu'},
                       'sensecore':{'tasks':'tbl_s_tasks','resources':'tbl_s_gpu'}}}

def test_four_targets_are_separate_and_site_scoped():
    targets=table_targets(CONFIG)
    assert len(targets)==4
    assert {(kind,site) for name,table,kind,site in targets}=={
        ('tasks','maoshanwang'),('resources','maoshanwang'),
        ('tasks','sensecore'),('resources','sensecore')}
    assert len({table for name,table,kind,site in targets})==4

def test_legacy_two_tables_still_work():
    targets=table_targets({'table_id':'tasks','resource_table_id':'gpu'})
    assert targets==[('tasks','tasks','tasks',None),('resources','gpu','resources',None)]

@pytest.mark.parametrize('change',['duplicate','missing','unknown','bad_id','empty'])
def test_unsafe_target_mappings_are_rejected(change):
    config=copy.deepcopy(CONFIG)
    if change=='duplicate':config['site_tables']['sensecore']['tasks']='tbl_m_tasks'
    if change=='missing':del config['site_tables']['sensecore']['resources']
    if change=='unknown':config['site_tables']['other']=config['site_tables'].pop('sensecore')
    if change=='bad_id':config['site_tables']['sensecore']['tasks']='../bad'
    if change=='empty':config['site_tables']={}
    with pytest.raises(ValueError):table_targets(config)

def test_private_config_can_use_four_targets_without_legacy_table_id(tmp_path):
    path=tmp_path/'config.json';path.write_text(json.dumps(CONFIG));path.chmod(0o600)
    assert read_config(path)==CONFIG

@pytest.mark.parametrize('snapshots',[{'sensecore':{'tasks':[]},'maoshanwang':{'tasks':[]}},{}])
def test_sync_scopes_both_tables_and_preserves_failed_site_signal(tmp_path,snapshots):
    seen=[]
    class Client:
        base='table'
        def list_records(self):return []
        def update_records(self,records):assert records==[]
    def planner(value,records,now):seen.append(value);return [],[]
    sync=Sync(tmp_path/'sync.sqlite3',Client(),planner,site='sensecore')
    sync.run(snapshots,123)
    assert seen==[{'sensecore':snapshots.get('sensecore',{})}]
    sync.close()
