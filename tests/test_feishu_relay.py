import base64
import hashlib
import hmac
from datetime import datetime, timezone

import pytest
from scripts.feishu_relay import Relay, signed_payload, check_response, official_url

T = 1790838000.0

def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()

def task(name='charles-one', ident='ACP:one', **kw):
    row = dict(id=ident, kind='ACP', name=name, state='running', gpu_count=8,
               cluster='test', metrics={'gpu': 0}, metric_updated_at={'gpu': iso(T)},
               failed_metrics=[], url='https://console.sensecore.cn/task/one')
    row.update(kw)
    return row

def snap(rows=(), now=T, status='ok'):
    return {'tasks': list(rows), 'source_status': {k: {'status': status, 'updated_at': iso(now)} for k in ('CCI', 'ACP')}}

def make(tmp_path, **kw):
    return Relay(tmp_path / 'relay.sqlite3', dict(low_gpu_enabled=True, low_gpu_percent=10,
                 low_gpu_seconds=1800, cooldown_seconds=21600, daily_hour=9, **kw))

def texts(r):
    return [x['text'] for x in r.pending()]

def test_signed_payload_and_application_errors():
    p = signed_payload('测试', 'secret', T)
    expected = base64.b64encode(hmac.new((str(int(T))+'\nsecret').encode(), b'', hashlib.sha256).digest()).decode()
    assert p['sign'] == expected
    assert p['content']['text'] == '测试'
    check_response({'code': 0})
    check_response({'StatusCode': 0})
    for response in ({'code':19021}, {}, {'code':1,'StatusCode':0}):
        with pytest.raises(RuntimeError): check_response(response)

def test_startup_silent_and_restart_dedup(tmp_path):
    r = make(tmp_path)
    a = task()
    r.observe('sensecore', snap([a]), snap([task(ident='ACP:old',status='failed')]), T)
    assert not r.pending()
    b = task(ident='ACP:new')
    r.observe('sensecore', snap([a,b],T+60), snap(now=T+60), T+60)
    assert len(r.pending()) == 1
    r.close()
    r = make(tmp_path)
    r.observe('sensecore', snap([a,b],T+120), snap(now=T+120), T+120)
    assert len(r.pending()) == 1
    r.flush(lambda text: None, T+120)
    assert not r.pending()

def test_terminal_requires_confirmation_and_fresh_source(tmp_path):
    r = make(tmp_path); a=task()
    r.observe('sensecore',snap([a]),snap(),T)
    r.observe('sensecore',snap([],T+60),snap(now=T+60),T+60)
    assert not r.pending()
    end=task(status='failed')
    r.observe('sensecore',snap([],T+120),snap([end],T+120,'error'),T+120)
    r.observe('sensecore',snap([],T+500),snap([end],T),T+500)
    assert not r.pending()
    r.observe('sensecore',snap([],T+560),snap([end],T+560),T+560)
    assert len(r.pending()) == 1 and '失败' in texts(r)[0]

def test_late_history_baseline_can_finish_known_running_task(tmp_path):
    r=make(tmp_path)
    r.observe('sensecore',snap([task()]),snap(status='loading'),T)
    r.observe('sensecore',snap([],T+60),snap([task(status='succeeded'),task(ident='ACP:old',status='failed')],T+60),T+60)
    assert len(r.pending()) == 1 and '成功' in texts(r)[0]

def test_two_sites_and_name_filter(tmp_path):
    r=make(tmp_path)
    for site in ['sensecore','maoshanwang']:
        r.observe(site,snap(),snap(),T)
        r.observe(site,snap([task(),task(name='other',ident='ACP:other')],T+60),snap(now=T+60),T+60)
    assert len(r.pending())==2
    assert 'sensecore' in texts(r)[0] and 'maoshanwang' in texts(r)[1]

def test_low_gpu_continuity_and_cooldown(tmp_path):
    r=make(tmp_path)
    for elapsed in range(0,1860,60):
        a=task(metric_updated_at={'gpu':iso(T+elapsed)})
        r.observe('sensecore',snap([a],T+elapsed),snap(now=T+elapsed),T+elapsed)
    assert len(r.pending())==1 and '低利用率' in texts(r)[0]
    for elapsed in range(1860,3660,60):
        r.observe('sensecore',snap([task(metric_updated_at={'gpu':iso(T+elapsed)})],T+elapsed),snap(now=T+elapsed),T+elapsed)
    assert len(r.pending())==1

@pytest.mark.parametrize('fault',['failed','stale','missing','gap','nan'])
def test_bad_metric_breaks_low_gpu_continuity(tmp_path,fault):
    r=make(tmp_path)
    for elapsed in range(0,1800,60):
        r.observe('sensecore',snap([task(metric_updated_at={'gpu':iso(T+elapsed)})],T+elapsed),snap(now=T+elapsed),T+elapsed)
    bad=task(metric_updated_at={'gpu':iso(T+1800)})
    if fault=='failed': bad['failed_metrics']=['gpu']
    if fault=='stale': bad['metric_updated_at']={'gpu':iso(T)}
    if fault=='nan': bad['metrics']={'gpu':float('nan')}
    elapsed=2400 if fault=='gap' else 1800
    r.observe('sensecore',snap([] if fault=='missing' else [bad],T+elapsed),snap(now=T+elapsed),T+elapsed)
    r.observe('sensecore',snap([task(metric_updated_at={'gpu':iso(T+elapsed+60)})],T+elapsed+60),snap(now=T+elapsed+60),T+elapsed+60)
    assert not r.pending()

def test_retry_persists_and_does_not_repeat_success(tmp_path):
    r=make(tmp_path);r.observe('sensecore',snap(),snap(),T)
    r.observe('sensecore',snap([task()],T+60),snap(now=T+60),T+60)
    calls=[]
    def fail(text): calls.append(text); raise RuntimeError('network error')
    r.flush(fail,T+60);r.flush(fail,T+61)
    assert len(calls)==1 and len(r.pending())==1
    r.close();r=make(tmp_path)
    r.flush(lambda text:calls.append(text),T+4000)
    r.flush(lambda text:calls.append(text),T+5000)
    assert len(calls)==2 and not r.pending()

def test_daily_once_after_nine_in_shanghai(tmp_path):
    r=make(tmp_path)
    # 08:59 and 09:00 in UTC+8
    before=datetime(2026,10,2,0,59,tzinfo=timezone.utc).timestamp()
    r.daily({'sensecore':snap([task()],before)},before)
    assert not r.pending()
    r.daily({'sensecore':snap([task()],before+60)},before+60)
    r.daily({'sensecore':snap([task()],before+120)},before+120)
    assert len(r.pending())==1 and '每日汇总' in texts(r)[0]

def test_url_allowlist():
    assert official_url('https://console.sensecore.cn/task')
    for u in ['http://127.0.0.1:8766','https://console.sensecore.cn.evil.test','https://evil.test','https://u:p@console.sensecore.cn/']:
        assert not official_url(u)

def test_large_daily_summary_fits_webhook_limit(tmp_path):
    r=make(tmp_path)
    r.state['daily_day']='2026-09-30'
    rows=[task(name='charles-'+('中文'*150), ident='ACP:'+str(i),cluster='测'*100,
               url='https://console.sensecore.cn/task?name='+('x'*900)) for i in range(20)]
    r.daily({s:snap(rows) for s in ['sensecore','maoshanwang']},T)
    import json
    assert len(json.dumps(signed_payload(texts(r)[0],'secret',T),ensure_ascii=False).encode()) < 19000

def test_terminal_does_not_revert_from_older_running_snapshot(tmp_path):
    r=make(tmp_path)
    r.observe('sensecore',snap([task()]),snap(),T)
    r.observe('sensecore',snap([],T+120),snap([task(status='stopped')],T+120),T+120)
    r.observe('sensecore',snap([task()],T+60),snap([task(status='stopped')],T+120),T+130)
    assert len(r.pending())==1

def test_late_old_history_never_announced_as_new_finish(tmp_path):
    r=make(tmp_path);r.observe('sensecore',snap(),snap(),T)
    r.observe('sensecore',snap([],T+60),snap([task(status='failed',ended_at=iso(T-86400))],T+60),T+60)
    assert not r.pending()

def test_delivery_expires_after_one_day(tmp_path):
    r=make(tmp_path);r.observe('sensecore',snap(),snap(),T)
    r.observe('sensecore',snap([task()],T+60),snap(now=T+60),T+60)
    sent=[];r.flush(sent.append,T+90000)
    assert sent==[] and not r.pending()

def test_new_terminal_during_initial_history_outage_is_not_lost(tmp_path):
    r=make(tmp_path)
    r.observe('sensecore',snap(),snap(status='error'),T)
    ended=task(status='succeeded',ended_at=iso(T+100))
    r.observe('sensecore',snap([],T+120),snap([ended],T+120),T+120)
    assert len(r.pending())==1 and '成功' in texts(r)[0]

def test_retry_preserves_task_event_order(tmp_path):
    r=make(tmp_path);r.observe('sensecore',snap(),snap(),T)
    r.observe('sensecore',snap([task()],T+60),snap(now=T+60),T+60)
    def fail(text): raise OSError('offline')
    r.flush(fail,T+60)
    r.observe('sensecore',snap([],T+120),snap([task(status='succeeded')],T+120),T+120)
    r.flush(fail,T+120)
    sent=[];r.flush(sent.append,T+180)
    assert sent==[]
    r.flush(sent.append,T+240)
    assert len(sent)==2 and '开始运行' in sent[0] and '成功' in sent[1]
