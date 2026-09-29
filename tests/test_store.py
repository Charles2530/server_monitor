from server.store import Store


def snapshot(at=100):
    return {"updated_at": at, "status": "ok", "tasks": [{"id": "pt-1", "kind": "ACP", "series": [
        {"metric": "cpu", "label": "master-0", "points": [[90, 20], [100, 30]]}
    ]}], "clusters": []}


def test_store_survives_restart_and_history_upsert(tmp_path):
    path = tmp_path / "monitor.sqlite3"
    store = Store(path)
    store.save(snapshot(), now=100)
    assert Store(path).latest()["tasks"][0]["id"] == "pt-1"
    changed = snapshot(110)
    changed["tasks"][0]["series"][0]["points"] = [[100, 31], [110, 40]]
    store.save(changed, now=110)
    points = store.history("pt-1", since=0)[0]["points"]
    assert points == [[90.0, 20.0], [100.0, 31.0], [110.0, 40.0]]


def test_retention_removes_old_metrics_but_not_latest_snapshot(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    store.save(snapshot(), now=100)
    store.prune(now=8*86400, days=7)
    assert store.history("pt-1", since=0) == []
    assert store.latest()["tasks"][0]["id"] == "pt-1"


def test_gpu_utilization_and_memory_remain_independent_after_restart(tmp_path):
    path=tmp_path/'metrics.sqlite3'
    state=snapshot()
    state['tasks'][0]['series']=[
        {'metric':'gpu','label':'GPU · worker-0','points':[[100,91.5]]},
        {'metric':'gpu_memory','label':'显存 · worker-0','points':[[100,39.4]]},
    ]
    Store(path).save(state,now=100)
    history={item['metric']:item['points'] for item in Store(path).history('pt-1',since=0)}
    assert history=={'gpu':[[100,91.5]],'gpu_memory':[[100,39.4]]}
