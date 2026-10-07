"""Read bounded Codex session metadata inside a CCI container."""
from __future__ import annotations

import json
import os
from pathlib import PurePosixPath
from typing import Any

MAX_HISTORY_RECORDS = 200
MAX_TITLE_LENGTH = 180
MAX_ID_LENGTH = 200
MAX_CWD_LENGTH = 4096
MAX_UPDATED_LENGTH = 80


def _normal_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.replace("\\", "/").strip()
    if not value.startswith("/"):
        return None
    try:
        normalized = str(PurePosixPath(value))
    except Exception:
        return None
    if not normalized.startswith("/") or len(normalized) > MAX_CWD_LENGTH:
        return None
    return normalized if normalized != "." else "/"


def workspace_contains(root: str, cwd: str) -> bool:
    normalized_root = _normal_path(root)
    normalized_cwd = _normal_path(cwd)
    if not normalized_root or not normalized_cwd:
        return False
    return normalized_cwd == normalized_root or normalized_cwd.startswith(normalized_root.rstrip("/") + "/")


def _sort_key(value: Any) -> tuple[int, str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, f"{value:030.6f}")
    if isinstance(value, str):
        return (1, value[:MAX_UPDATED_LENGTH])
    return (2, "")


def sanitize_history_result(value: Any, workspace: str) -> dict[str, Any]:
    reported_root = value.get("workspace") if isinstance(value, dict) else None
    root = _normal_path(reported_root) or _normal_path(workspace) or workspace
    raw_records = value.get("records") if isinstance(value, dict) else []
    records: list[dict[str, Any]] = []
    if isinstance(raw_records, list):
        for raw in raw_records:
            if not isinstance(raw, dict):
                continue
            thread_id = raw.get("thread_id")
            cwd = _normal_path(raw.get("cwd"))
            if not isinstance(thread_id, str) or not thread_id or len(thread_id) > MAX_ID_LENGTH or not cwd:
                continue
            if not workspace_contains(root, cwd):
                continue
            title = raw.get("display_title")
            if not isinstance(title, str):
                title = "未命名聊天"
            title = " ".join(title.split())[:MAX_TITLE_LENGTH] or "未命名聊天"
            updated = raw.get("updated_at")
            if isinstance(updated, bool) or not isinstance(updated, (int, float, str)):
                updated = ""
            if isinstance(updated, str):
                updated = updated[:MAX_UPDATED_LENGTH]
            record = {
                "thread_id": thread_id,
                "display_title": title,
                "updated_at": updated,
                "cwd": cwd,
            }
            records.append(record)
    records.sort(key=lambda row: _sort_key(row["updated_at"]), reverse=True)
    return {"workspace": root, "records": records[:MAX_HISTORY_RECORDS], "error": None}


def history_script(workspace: str) -> str:
    """Return a self-contained script executed by ``run_python`` in the CCI shell."""
    encoded_workspace = json.dumps(workspace)
    return f'''
import glob
from functools import lru_cache
import json
import os
import pathlib
import sqlite3

ROOT = {encoded_workspace}
MAX_RECORDS = {MAX_HISTORY_RECORDS}
MAX_TITLE = {MAX_TITLE_LENGTH}
MAX_ID = {MAX_ID_LENGTH}
MAX_CWD = {MAX_CWD_LENGTH}

@lru_cache(maxsize=4096)
def normal(value):
    if not isinstance(value, str):
        return None
    value = value.replace("\\\\", "/").strip()
    if not value.startswith("/"):
        return None
    try:
        value = os.path.realpath(value)
    except Exception:
        return None
    if not value.startswith("/") or len(value) > MAX_CWD:
        return None
    return value.rstrip("/") or "/"

def inside(root, cwd):
    root, cwd = normal(root), normal(cwd)
    return bool(root and cwd and (cwd == root or cwd.startswith(root.rstrip("/") + "/")))

def add(records, row):
    if not isinstance(row, dict):
        return
    thread_id = row.get("thread_id")
    cwd = normal(row.get("cwd"))
    if not isinstance(thread_id, str) or not thread_id or len(thread_id) > MAX_ID or not inside(ROOT, cwd):
        return
    title = row.get("display_title")
    if not isinstance(title, str):
        title = "未命名聊天"
    title = " ".join(title.split())[:MAX_TITLE] or "未命名聊天"
    updated = row.get("updated_at")
    if isinstance(updated, bool) or not isinstance(updated, (int, float, str)):
        updated = ""
    if isinstance(updated, str):
        updated = updated[:80]
    records[thread_id] = {{"thread_id": thread_id, "display_title": title, "updated_at": updated, "cwd": cwd}}

records = {{}}
home = pathlib.Path(os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex")))
if not home.is_absolute():
    raise RuntimeError("CODEX_HOME must be absolute")
def quoted_identifier(value):
    return chr(34) + value.replace(chr(34), chr(34) * 2) + chr(34)

def read_table(db, table):
    try:
        if not db.is_file() or db.stat().st_size > 256 * 1024 * 1024:
            return
        with sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True) as connection:
            columns = {{row[1] for row in connection.execute("pragma table_info(" + quoted_identifier(table) + ")")}}
            id_column = "id" if "id" in columns else "thread_id" if "thread_id" in columns else None
            title_column = "title" if "title" in columns else "display_title" if "display_title" in columns else None
            updated_column = "updated_at" if "updated_at" in columns else "source_updated_at" if "source_updated_at" in columns else None
            if not id_column or not title_column or not updated_column or "cwd" not in columns:
                return
            connection.create_function("ilab_inside", 1, lambda cwd: inside(ROOT, cwd))
            query = (
                "select " + ", ".join(quoted_identifier(column) for column in (id_column, title_column, updated_column, "cwd"))
                + " from " + quoted_identifier(table)
                + " where ilab_inside(" + quoted_identifier("cwd") + ")"
                + " order by " + quoted_identifier(updated_column) + " desc limit " + str(MAX_RECORDS)
            )
            for thread_id, title, updated, cwd in connection.execute(query):
                add(records, {{"thread_id": thread_id, "display_title": title, "updated_at": updated, "cwd": cwd}})
        return True
    except Exception:
        return False

# A valid empty index is authoritative; do not scan rollouts for other projects.
indexed = read_table(home / "state_5.sqlite", "threads")
if not indexed:
    indexed = read_table(home / "sqlite" / "codex-dev.db", "local_thread_catalog")

if not indexed:
    files = sorted(glob.iglob(str(home / "sessions" / "**" / "rollout-*.jsonl"), recursive=True), reverse=True)[:1000]
    for filename in files:
        try:
            if os.path.getsize(filename) > 128 * 1024 * 1024:
                continue
            with open(filename, "r", encoding="utf-8", errors="ignore") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if event.get("type") != "session_meta" or not isinstance(event.get("payload"), dict):
                        continue
                    payload = event["payload"]
                    add(records, {{"thread_id": payload.get("id") or payload.get("session_id"), "display_title": payload.get("thread_name") or "未命名聊天", "updated_at": payload.get("timestamp"), "cwd": payload.get("cwd")}})
                    break
        except Exception:
            continue

items = list(records.values())
def sort_key(item):
    value = item.get("updated_at")
    return (0, value) if isinstance(value, (int, float)) and not isinstance(value, bool) else (1, str(value)[:80])
items.sort(key=sort_key, reverse=True)
RESULT = {{"workspace": normal(ROOT), "records": items[:MAX_RECORDS], "error": None}}
'''
