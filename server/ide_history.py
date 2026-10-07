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
    root = _normal_path(workspace) or workspace
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
    return f'''\nimport glob\nimport json\nimport os\nimport pathlib\nimport sqlite3\n\nROOT = {encoded_workspace}\nMAX_RECORDS = {MAX_HISTORY_RECORDS}\nMAX_TITLE = {MAX_TITLE_LENGTH}\nMAX_ID = {MAX_ID_LENGTH}\nMAX_CWD = {MAX_CWD_LENGTH}\n\ndef normal(value):\n    if not isinstance(value, str):\n        return None\n    value = value.replace("\\\\", "/").strip()\n    if not value.startswith("/"):\n        return None\n    try:\n        value = os.path.realpath(value)\n    except Exception:\n        return None\n    if not value.startswith("/") or len(value) > MAX_CWD:\n        return None\n    return value.rstrip("/") or "/"\n\ndef inside(root, cwd):\n    root, cwd = normal(root), normal(cwd)\n    return bool(root and cwd and (cwd == root or cwd.startswith(root.rstrip("/") + "/")))\n\ndef add(records, row):\n    if not isinstance(row, dict):\n        return\n    thread_id = row.get("thread_id")\n    cwd = normal(row.get("cwd"))\n    if not isinstance(thread_id, str) or not thread_id or len(thread_id) > MAX_ID or not inside(ROOT, cwd):\n        return\n    title = row.get("display_title")\n    if not isinstance(title, str):\n        title = "未命名聊天"\n    title = " ".join(title.split())[:MAX_TITLE] or "未命名聊天"\n    updated = row.get("updated_at")\n    if isinstance(updated, bool) or not isinstance(updated, (int, float, str)):\n        updated = ""\n    if isinstance(updated, str):\n        updated = updated[:80]\n    records[thread_id] = {{"thread_id": thread_id, "display_title": title, "updated_at": updated, "cwd": cwd}}\n\nrecords = {{}}\nhome = pathlib.Path(os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex")))\nif not home.is_absolute():\n    raise RuntimeError("CODEX_HOME must be absolute")\ntry:\n    db = home / "sqlite" / "codex-dev.db"\n    if db.is_file() and db.stat().st_size <= 256 * 1024 * 1024:\n        with sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True) as connection:\n            columns = {{row[1] for row in connection.execute("pragma table_info(local_thread_catalog)")}}\n            required = {{"thread_id", "display_title", "source_updated_at", "cwd"}}\n            if required <= columns:\n                query = "select thread_id, display_title, source_updated_at, cwd from local_thread_catalog order by source_updated_at desc limit 1000"\n                for thread_id, title, updated, cwd in connection.execute(query):\n                    add(records, {{"thread_id": thread_id, "display_title": title, "updated_at": updated, "cwd": cwd}})\nexcept Exception:\n    pass\n\nif not records:\n    files = sorted(glob.iglob(str(home / "sessions" / "**" / "rollout-*.jsonl"), recursive=True), reverse=True)[:1000]\n    for filename in files:\n        try:\n            if os.path.getsize(filename) > 128 * 1024 * 1024:\n                continue\n            with open(filename, "r", encoding="utf-8", errors="ignore") as stream:\n                for line in stream:\n                    try:\n                        event = json.loads(line)\n                    except Exception:\n                        continue\n                    if event.get("type") != "session_meta" or not isinstance(event.get("payload"), dict):\n                        continue\n                    payload = event["payload"]\n                    add(records, {{"thread_id": payload.get("id") or payload.get("session_id"), "display_title": payload.get("thread_name") or "未命名聊天", "updated_at": payload.get("timestamp"), "cwd": payload.get("cwd")}})\n                    break\n        except Exception:\n            continue\n\nitems = list(records.values())\ndef sort_key(item):\n    value = item.get("updated_at")\n    return (0, value) if isinstance(value, (int, float)) and not isinstance(value, bool) else (1, str(value)[:80])\nitems.sort(key=sort_key, reverse=True)\nRESULT = {{"workspace": normal(ROOT), "records": items[:MAX_RECORDS], "error": None}}\n'''
