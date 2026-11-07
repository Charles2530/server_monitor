"""Private SQLite storage. Values are indexed by task, worker, metric and time."""
import asyncio
from contextlib import contextmanager
import json
import os
import sqlite3
from pathlib import Path


async def store_call(callback, *args, **kwargs):
    """Run SQLite off-loop, draining started transactions before cancellation."""
    task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Cancelling to_thread cannot stop its transaction. Keep caller locks
        # until it finishes, and preserve cancellation even if the disk fails.
        drained = asyncio.gather(task, return_exceptions=True)
        while not drained.done():
            try: await asyncio.shield(drained)
            except asyncio.CancelledError: pass
        raise


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY, captured REAL NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS samples (
                    task TEXT NOT NULL, metric TEXT NOT NULL, label TEXT NOT NULL,
                    timestamp REAL NOT NULL, value REAL,
                    PRIMARY KEY (task, metric, label, timestamp)
                );
                CREATE INDEX IF NOT EXISTS samples_time ON samples(timestamp);
                CREATE INDEX IF NOT EXISTS samples_task_time ON samples(task,timestamp);
                CREATE TABLE IF NOT EXISTS task_runs (task TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS operations (
                    key TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
                    display_name TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT, error TEXT, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wandb_projects (
                    entity TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL,
                    cached REAL NOT NULL, PRIMARY KEY(entity, name)
                );
                CREATE TABLE IF NOT EXISTS wandb_runs (
                    entity TEXT NOT NULL, project TEXT NOT NULL, run_id TEXT NOT NULL,
                    payload TEXT NOT NULL, cached REAL NOT NULL,
                    PRIMARY KEY(entity, project, run_id)
                );
                CREATE TABLE IF NOT EXISTS wandb_history (
                    entity TEXT NOT NULL, project TEXT NOT NULL, run_id TEXT NOT NULL,
                    metric TEXT NOT NULL, payload TEXT NOT NULL, cached REAL NOT NULL,
                    PRIMARY KEY(entity, project, run_id, metric)
                );
                CREATE TABLE IF NOT EXISTS wandb_logs (
                    entity TEXT NOT NULL, project TEXT NOT NULL, run_id TEXT NOT NULL,
                    payload TEXT NOT NULL, cached REAL NOT NULL,
                    PRIMARY KEY(entity, project, run_id)
                );
                CREATE TABLE IF NOT EXISTS wandb_task_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL,
                    task_id TEXT NOT NULL, entity TEXT NOT NULL, project TEXT NOT NULL,
                    run_id TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    UNIQUE(site_id, task_id, entity, project, run_id)
                );
            """)
            db.execute("INSERT OR IGNORE INTO task_runs SELECT DISTINCT task FROM samples")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def operation(self, key):
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM operations WHERE key=?',(key,)).fetchone()
        if row is None:return None
        result=dict(row)
        result['result']=json.loads(result['result']) if result['result'] else None
        return result

    def release_operation(self,target):
        with self.connect() as db:
            row=db.execute("SELECT key FROM operations WHERE kind='release_restart' AND target=? AND status!='failed' LIMIT 1",(target,)).fetchone()
        return self.operation(row[0]) if row else None

    def begin_operation(self,key,kind,target,display_name,result=None):
        import time
        with self.connect() as db:
            cursor=db.execute('INSERT OR IGNORE INTO operations(key,kind,target,display_name,status,created,result) VALUES (?,?,?,?,?,?,?)',
                              (key,kind,target,display_name,'pending',time.time(),json.dumps(result,ensure_ascii=False) if result else None))
            return cursor.rowcount==1

    def finish_operation(self,key,status,result=None,error=None):
        with self.connect() as db:
            db.execute('UPDATE operations SET status=?,result=?,error=? WHERE key=?',
                       (status,json.dumps(result,ensure_ascii=False) if result else None,error,key))

    def running_task_ids(self):
        with self.connect() as db:
            return {row[0] for row in db.execute("SELECT task FROM task_runs")}

    def latest(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def save(self, snapshot, now):
        # Do not copy the large curves that aren't stored in the snapshot JSON.
        compact = {**snapshot, "tasks": [
            {**{k: v for k, v in task.items() if k != "series"}, "series": []}
            for task in snapshot.get("tasks", [])
        ]}
        with self.connect() as db:
            db.execute("DELETE FROM snapshots")
            db.execute("INSERT INTO snapshots(captured,payload) VALUES (?,?)",
                       (now, json.dumps(compact, ensure_ascii=False, allow_nan=False)))
            for task in snapshot.get("tasks", []):
                if task.get("state")=="running":db.execute("INSERT OR IGNORE INTO task_runs VALUES (?)",(task["id"],))
                for item in task.get("series", []):
                    db.executemany("""INSERT INTO samples VALUES (?,?,?,?,?)
                        ON CONFLICT(task,metric,label,timestamp) DO UPDATE SET value=excluded.value
                        WHERE samples.value IS NOT excluded.value""", (
                        (task["id"], item["metric"], item["label"], t, v)
                        for t, v in item["points"]
                    ))

    def history(self, task_id, since):
        groups = {}
        with self.connect() as db:
            rows = db.execute("SELECT metric,label,timestamp,value FROM samples WHERE task=? AND timestamp>=? ORDER BY metric,label,timestamp",
                              (task_id, since))
            for metric, label, timestamp, value in rows:
                groups.setdefault((metric, label), []).append([timestamp, value])
        return [{"metric": m, "label": label, "points": points}
                for (m, label), points in groups.items()]

    def prune(self, now, days=7):
        with self.connect() as db:
            db.execute("DELETE FROM samples WHERE timestamp < ?", (now-days*86400,))
            db.execute("DELETE FROM snapshots WHERE captured < ? AND id != (SELECT MAX(id) FROM snapshots)",
                       (now-days*86400,))

    def wandb_upsert_projects(self, items, now):
        with self.connect() as db:
            db.executemany("""INSERT INTO wandb_projects(entity,name,payload,cached) VALUES (?,?,?,?)
                ON CONFLICT(entity,name) DO UPDATE SET payload=excluded.payload,cached=excluded.cached""",
                [(item.get('entity') or '', item.get('name') or '', json.dumps(item, ensure_ascii=False), now) for item in items])

    def wandb_projects(self, entity):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM wandb_projects WHERE entity=? ORDER BY name", (entity,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def wandb_upsert_runs(self, items, now):
        with self.connect() as db:
            db.executemany("""INSERT INTO wandb_runs(entity,project,run_id,payload,cached) VALUES (?,?,?,?,?)
                ON CONFLICT(entity,project,run_id) DO UPDATE SET payload=excluded.payload,cached=excluded.cached""",
                [(item.get('entity') or '', item.get('project') or '', item.get('id') or '', json.dumps(item, ensure_ascii=False), now) for item in items])

    def wandb_runs(self, entity, project):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM wandb_runs WHERE entity=? AND project=? ORDER BY json_extract(payload, '$.created_at') DESC", (entity, project)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def wandb_run(self, entity, project, run_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM wandb_runs WHERE entity=? AND project=? AND run_id=?", (entity, project, run_id)).fetchone()
        return json.loads(row[0]) if row else None

    def wandb_save_history(self, entity, project, run_id, series, now):
        with self.connect() as db:
            db.executemany("""INSERT INTO wandb_history(entity,project,run_id,metric,payload,cached) VALUES (?,?,?,?,?,?)
                ON CONFLICT(entity,project,run_id,metric) DO UPDATE SET payload=excluded.payload,cached=excluded.cached""",
                [(entity, project, run_id, item.get('metric') or '', json.dumps(item, ensure_ascii=False), now) for item in series])

    def wandb_history(self, entity, project, run_id):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM wandb_history WHERE entity=? AND project=? AND run_id=? ORDER BY metric", (entity, project, run_id)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def wandb_save_logs(self, entity, project, run_id, lines, now):
        with self.connect() as db:
            db.execute("""INSERT INTO wandb_logs(entity,project,run_id,payload,cached) VALUES (?,?,?,?,?)
                ON CONFLICT(entity,project,run_id) DO UPDATE SET payload=excluded.payload,cached=excluded.cached""",
                (entity, project, run_id, json.dumps(lines, ensure_ascii=False), now))

    def wandb_logs(self, entity, project, run_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM wandb_logs WHERE entity=? AND project=? AND run_id=?", (entity, project, run_id)).fetchone()
        return json.loads(row[0]) if row else []

    def wandb_add_link(self, site_id, task_id, entity, project, run_id, now):
        with self.connect() as db:
            db.execute("""INSERT INTO wandb_task_links(site_id,task_id,entity,project,run_id,created,updated)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(site_id,task_id,entity,project,run_id)
                DO UPDATE SET updated=excluded.updated""", (site_id, task_id, entity, project, run_id, now, now))
            row = db.execute("SELECT * FROM wandb_task_links WHERE site_id=? AND task_id=? AND entity=? AND project=? AND run_id=?", (site_id, task_id, entity, project, run_id)).fetchone()
            return dict(zip(('id', 'site_id', 'task_id', 'entity', 'project', 'run_id', 'created', 'updated'), row))

    def wandb_links(self, site_id, task_id=None):
        with self.connect() as db:
            if task_id is None:
                rows = db.execute("SELECT * FROM wandb_task_links WHERE site_id=? ORDER BY updated DESC", (site_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM wandb_task_links WHERE site_id=? AND task_id=? ORDER BY updated DESC", (site_id, task_id)).fetchall()
            columns = ('id', 'site_id', 'task_id', 'entity', 'project', 'run_id', 'created', 'updated')
            return [dict(zip(columns, row)) for row in rows]

    def wandb_delete_link(self, site_id, link_id):
        with self.connect() as db:
            db.execute("DELETE FROM wandb_task_links WHERE site_id=? AND id=?", (site_id, link_id))
