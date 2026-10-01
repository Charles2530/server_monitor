"""Private SQLite storage. Values are indexed by task, worker, metric and time."""
import copy
import json
import os
import sqlite3
from pathlib import Path


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
                CREATE TABLE IF NOT EXISTS task_runs (task TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS operations (
                    key TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
                    display_name TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT, error TEXT, created REAL NOT NULL
                );
            """)
            db.execute("INSERT OR IGNORE INTO task_runs SELECT DISTINCT task FROM samples")
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(str(self.path), timeout=10)

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
        compact = copy.deepcopy(snapshot)
        for task in compact.get("tasks", []):
            task["series"] = []
        with self.connect() as db:
            db.execute("DELETE FROM snapshots")
            db.execute("INSERT INTO snapshots(captured,payload) VALUES (?,?)",
                       (now, json.dumps(compact, ensure_ascii=False, allow_nan=False)))
            for task in snapshot.get("tasks", []):
                if task.get("state")=="running":db.execute("INSERT OR IGNORE INTO task_runs VALUES (?)",(task["id"],))
                for item in task.get("series", []):
                    db.executemany("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?)", [
                        (task["id"], item["metric"], item["label"], t, v)
                        for t, v in item["points"]
                    ])

    def history(self, task_id, since):
        with self.connect() as db:
            rows = db.execute("SELECT metric,label,timestamp,value FROM samples WHERE task=? AND timestamp>=? ORDER BY metric,label,timestamp",
                              (task_id, since)).fetchall()
        groups = {}
        for metric, label, timestamp, value in rows:
            groups.setdefault((metric, label), []).append([timestamp, value])
        return [{"metric": m, "label": label, "points": points}
                for (m, label), points in groups.items()]

    def prune(self, now, days=7):
        with self.connect() as db:
            db.execute("DELETE FROM samples WHERE timestamp < ?", (now-days*86400,))
            db.execute("DELETE FROM snapshots WHERE captured < ? AND id != (SELECT MAX(id) FROM snapshots)",
                       (now-days*86400,))
