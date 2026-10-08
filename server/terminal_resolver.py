"""Shared lifecycle for provider-specific terminal target resolvers."""
from abc import ABC, abstractmethod

from .acp_actions import ActionError
from .auth import load_session
from .collector import Collector
from .runtime_profile import get_runtime_profile
from .task_logs import task_identity
from .terminal_target_cache import TerminalTargetCache


class TerminalResolver(ABC):
    """Template method for resolving verified CCI/ACP terminal targets."""

    kind = None
    kind_error = '终端任务类型无效。'

    def __init__(self, site, *, transport=None, session_loader=None):
        self.site = site
        self.reader = Collector(site)
        self.transport = transport
        self.session_loader = session_loader or (lambda: load_session(site))
        self.target_cache = TerminalTargetCache(get_runtime_profile()['target_cache_seconds'])

    def task_name(self, task_id):
        kind, name = task_identity(task_id)
        if kind != self.kind:
            raise ActionError(self.kind_error, 'terminal_unavailable', 400)
        return name

    def cache_identity(self, task_id, **options):
        return task_id

    async def targets(self, task_id, *, reuse=False, **options):
        name = self.task_name(task_id)
        headers = dict(self.session_loader()['headers'])
        identity = self.cache_identity(task_id, **options)
        return await self.target_cache.resolve(
            identity,
            headers,
            lambda: self.read_targets(task_id, name, headers, **options),
            reuse=reuse,
        )

    @abstractmethod
    async def read_targets(self, task_id, name, headers, **options):
        raise NotImplementedError
