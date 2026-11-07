"""Small, read-only adapter around the W&B Public API."""
import asyncio
import math
import os
import tempfile
from typing import Any, Callable, Iterable, Optional


class WandbError(Exception):
    def __init__(self, message="W&B 读取失败，请稍后重试。", code="source_unavailable", status=503):
        super().__init__(message)
        self.code = code
        self.status = status


def _value(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _finite(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _safe_dict(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            if not isinstance(item, float) or math.isfinite(item):
                result[key] = item
    return result


def normalize_project(project):
    return {
        "name": _value(project, "name"),
        "entity": _value(project, "entity"),
        "url": _value(project, "url"),
        "description": _value(project, "description"),
    }


def normalize_run(run):
    user = _value(run, "user")
    username = _value(user, "username") if user is not None else _value(run, "username")
    return {
        "id": _value(run, "id"),
        "name": _value(run, "name"),
        "display_name": _value(run, "display_name") or _value(run, "name"),
        "state": _value(run, "state"),
        "entity": _value(run, "entity"),
        "project": _value(run, "project"),
        "url": _value(run, "url"),
        "created_at": _value(run, "created_at"),
        "updated_at": _value(run, "updated_at"),
        "user": username,
        "tags": [tag for tag in (_value(run, "tags", []) or []) if isinstance(tag, str)][:100],
        "group": _value(run, "group"),
        "job_type": _value(run, "job_type"),
        "config": _safe_dict(_value(run, "config", {})),
        "summary": _safe_dict(_value(run, "summary", _value(run, "summary_metrics", {}))),
        "notes": _value(run, "notes"),
        "last_history_step": _value(run, "lastHistoryStep", _value(run, "last_history_step")),
    }


def normalize_history(rows: Iterable[dict], keys=None):
    selected = set(keys or [])
    grouped = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        step = _finite(row.get("_step"))
        if step is None:
            step = _finite(row.get("_timestamp"))
        if step is None:
            continue
        for key, value in row.items():
            if not isinstance(key, str) or key.startswith("_") or (selected and key not in selected):
                continue
            number = _finite(value)
            if number is None:
                continue
            grouped.setdefault(key, []).append([float(step), float(number)])
    return [{"metric": key, "points": grouped[key]} for key in sorted(grouped)]


def normalize_logs(lines):
    result = []
    for line in lines or []:
        content = line if isinstance(line, str) else _value(line, "content")
        if not isinstance(content, str):
            continue
        timestamp = _value(line, "timestamp")
        result.append({"timestamp": timestamp if isinstance(timestamp, str) else None, "content": content[-20000:]})
    return result[-2000:]


class WandbClient:
    def __init__(self, *, api_key=None, base_url=None, entity=None, api_factory: Optional[Callable] = None):
        self.api_key = api_key if api_key is not None else os.getenv("WANDB_API_KEY", "").strip()
        self.base_url = (base_url or os.getenv("WANDB_BASE_URL") or "https://api.wandb.ai").strip().rstrip("/")
        self.entity = (entity or os.getenv("WANDB_ENTITY") or "").strip()
        self.api_factory = api_factory or self._default_api
        self._api = None

    @staticmethod
    def _default_api(**kwargs):
        try:
            import wandb
        except ImportError as exc:
            raise WandbError("未安装 W&B SDK，请先更新依赖。", "dependency_missing", 503) from exc
        return wandb.Api(**kwargs)

    def status(self):
        return {
            "configured": bool(self.api_key),
            "base_url": self.base_url,
            "entity": self.entity or None,
            "status": "configured" if self.api_key else "not_configured",
        }

    def _get_api(self):
        if not self.api_key:
            raise WandbError("未配置 W&B API Key。", "not_configured", 503)
        if self._api is None:
            try:
                self._api = self.api_factory(
                    overrides={"base_url": self.base_url},
                    api_key=self.api_key,
                    timeout=60,
                )
            except WandbError:
                raise
            except Exception as exc:
                raise WandbError() from exc
        return self._api

    async def _call(self, callback):
        try:
            return await asyncio.to_thread(callback)
        except WandbError:
            raise
        except Exception as exc:
            raise WandbError() from exc

    async def projects(self, entity=None, limit=200):
        entity = (entity or self.entity).strip()
        if not entity:
            raise WandbError("请配置 WANDB_ENTITY。", "entity_required", 400)
        result = await self._call(lambda: self._get_api().projects(entity=entity, per_page=min(max(limit, 1), 200)))
        return [normalize_project(item) for item in result][:limit]

    async def runs(self, entity, project, *, state=None, search=None, limit=100):
        if not entity or not project:
            raise WandbError("Entity 和 Project 不能为空。", "invalid_query", 400)
        filters = {}
        if state:
            filters["state"] = state
        if search:
            filters["$or"] = [{"displayName": {"$regex": search}}, {"name": {"$regex": search}}]
        result = await self._call(lambda: self._get_api().runs(
            f"{entity}/{project}", filters=filters, order="-created_at", per_page=min(max(limit, 1), 200)
        ))
        return [normalize_run(item) for item in result][:limit]

    async def run(self, entity, project, run_id):
        if not all(isinstance(value, str) and value and len(value) <= 300 for value in (entity, project, run_id)):
            raise WandbError("Run 参数无效。", "invalid_query", 400)
        item = await self._call(lambda: self._get_api().run(f"{entity}/{project}/{run_id}"))
        return normalize_run(item)

    async def history(self, entity, project, run_id, keys=None, samples=500):
        def read():
            item = self._get_api().run(f"{entity}/{project}/{run_id}")
            rows = item.history(samples=min(max(samples, 1), 2000), keys=keys or None, pandas=False)
            return normalize_history(rows, keys)
        return await self._call(read)

    async def logs(self, entity, project, run_id, tail=1000):
        def read():
            item = self._get_api().run(f"{entity}/{project}/{run_id}")
            files = list(item.files(names=["output.log"], per_page=1))
            if not files:
                return []
            with tempfile.TemporaryDirectory(prefix="wandb-log-") as root:
                with files[0].download(root=root, replace=True) as stream:
                    lines = stream.read().splitlines()
            return normalize_logs(lines[-min(max(tail, 1), 2000):])
        return await self._call(read)
