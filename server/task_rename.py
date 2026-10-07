"""Rename verified personal cloud tasks; writes are never automatically retried."""
import asyncio
import re
import time
import uuid

import httpx

from .acp_actions import ActionError
from .acp_states import is_charles
from .auth import load_session
from .collector import Collector
from .normalize import iso
from .store import store_call
from .task_logs import task_identity

NAME_PATTERN = re.compile(r'[\u4e00-\u9fa5a-zA-Z0-9][\u4e00-\u9fa5a-zA-Z0-9_\-.:]{0,255}')
UNKNOWN = '云端改名结果尚未确认，请点击“核对结果”或在云控制台查看；不会重复发送修改。'


def validate_display_name(value):
    if not isinstance(value, str) or not NAME_PATTERN.fullmatch(value.strip()) or not is_charles(value):
        raise ActionError('名称须包含 charles，最多 256 字符，以中英文或数字开头，仅支持中英文、数字、下划线、连字符、点和冒号。')
    return value.strip()


class TaskRenamer:
    def __init__(self, store, *, site, transport=None, session_loader=None):
        self.store, self.site, self.transport = store, site, transport
        self.session_loader = session_loader or (lambda: load_session(site))
        self.reader = Collector(site)
        self._lock = None

    @property
    def lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def identity(self, task_id):
        kind, name = task_identity(task_id)
        collection = 'apps' if kind == 'CCI' else 'trainingJobs'
        path = '/compute/' + kind.lower() + '/data/v2' + self.site.workspace + '/' + collection
        return kind, name, collection, path, 'CCI' if kind == 'CCI' else 'AEC2'

    def validate_task(self, raw, name, collection):
        if (not isinstance(raw, dict) or raw.get('name') != name
                or raw.get('id') != self.site.workspace + '/' + collection + '/' + name
                or not is_charles(raw.get('display_name'))):
            raise ActionError('只能修改当前工作空间本人且名称包含 charles 的任务。', 'target_mismatch', 403)
        return raw

    async def own_task(self, client, task_id):
        kind, name, collection, path, service = self.identity(task_id)
        rows = await self.reader.paginate(client, service, path + 'Own',
            'apps' if kind == 'CCI' else 'training_jobs', {'filter': "name='" + name + "'"})
        raw = next((row for row in rows if isinstance(row, dict) and row.get('name') == name), None)
        self.validate_task(raw, name, collection)
        return await self.detail(client, task_id)

    async def detail(self, client, task_id):
        _, name, collection, path, service = self.identity(task_id)
        raw = await self.reader.request(client, service, path + '/' + name)
        return self.validate_task(raw, name, collection)

    async def confirmed(self, client, task_id, expected_name, new_name):
        try:
            raw = await self.detail(client, task_id)
            if raw['display_name'] == new_name:
                return {'site': self.site.id, 'task_id': task_id, 'name': new_name,
                        'expected_name': expected_name, 'verified_at': iso(time.time())}
        except Exception:
            pass
        raise ActionError(UNKNOWN, 'outcome_unknown', 409)

    async def previous(self, client, record, task_id, expected_name, new_name):
        context = record.get('result') or {}
        if (record['kind'] != 'rename' or record['target'] != task_id
                or record['display_name'] != new_name or context.get('expected_name') != expected_name):
            raise ActionError('请求标识已用于其他修改，请重新打开重命名。', 'request_conflict', 409)
        if record['status'] == 'succeeded':
            return record['result']
        if record['status'] == 'failed':
            raise ActionError(record['error'], 'cloud_rejected', 409)
        try:
            await self.own_task(client, task_id)
        except Exception:
            raise ActionError(UNKNOWN, 'outcome_unknown', 409) from None
        result = await self.confirmed(client, task_id, expected_name, new_name)
        await store_call(self.store.finish_operation, record['key'], 'succeeded', result=result)
        return result

    async def rename(self, task_id, expected_name, display_name, request_id, check_only=False):
        if type(check_only) is not bool:
            raise ActionError('核对参数无效。')
        _, name, _, path, service = self.identity(task_id)
        new_name = validate_display_name(display_name)
        if not isinstance(expected_name, str) or not expected_name or len(expected_name) > 256:
            raise ActionError('原任务名称无效，请刷新任务后重试。')
        try:
            key = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise ActionError('无效的重命名请求标识。') from None
        async with self.lock:
            old = await store_call(self.store.operation, key)
            if check_only and not old:
                raise ActionError('上次请求未提交到云端，可检查名称后重新保存。', 'not_submitted', 409)
            async with httpx.AsyncClient(headers=self.session_loader()['headers'], timeout=25,
                    follow_redirects=False, transport=self.transport) as client:
                if old:
                    return await self.previous(client, old, task_id, expected_name, new_name)
                raw = await self.own_task(client, task_id)
                if raw['display_name'] != expected_name:
                    raise ActionError('任务名称已被修改，请关闭弹窗并刷新后重试。', 'name_changed', 409)
                if new_name == expected_name:
                    return await self.confirmed(client, task_id, expected_name, new_name)
                context = {'expected_name': expected_name}
                if not await store_call(self.store.begin_operation, key, 'rename', task_id, new_name, result=context):
                    return await self.previous(client, await store_call(self.store.operation, key), task_id, expected_name, new_name)
                try:
                    try:
                        response = await client.patch(self.site.services[service] + path + '/' + name,
                            json={'display_name': new_name}, headers={'x-ui-valid': 'x-ui-valid'})
                    except httpx.HTTPError:
                        raise ActionError(UNKNOWN, 'outcome_unknown', 502) from None
                    if response.status_code >= 500 or response.status_code in (408, 409):
                        raise ActionError(UNKNOWN, 'outcome_unknown', 502)
                    if response.status_code in (401, 403):
                        raise ActionError('云会话已失效或没有重命名权限，请重新连接。', 'auth_required', 403)
                    if not 200 <= response.status_code < 300:
                        raise ActionError('云平台拒绝了重命名，请核对名称和任务权限。', 'cloud_rejected', 400)
                    result = await self.confirmed(client, task_id, expected_name, new_name)
                    await store_call(self.store.finish_operation, key, 'succeeded', result=result)
                    return result
                except ActionError as exc:
                    await store_call(self.store.finish_operation, key,
                        'unknown' if exc.code == 'outcome_unknown' else 'failed', result=context, error=str(exc))
                    raise
                # Cancellation leaves the durable pending record for read-only reconciliation.
