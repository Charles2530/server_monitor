"""Bounded, session-scoped reuse of read-only terminal target lookups."""
import asyncio
from collections import OrderedDict
import copy
from dataclasses import dataclass
import hashlib
import json
import time


@dataclass
class _Flight:
    task: asyncio.Task
    users: int = 0


class TerminalTargetCache:
    def __init__(self, ttl, limit=128):
        self.ttl, self.limit = ttl, limit
        self.entries = OrderedDict()
        self.pending = {}

    async def resolve(self, identity, headers, read, *, reuse=False):
        # Neither cached results nor pending reads may cross login sessions.
        context = hashlib.sha256(json.dumps(dict(headers), sort_keys=True).encode()).digest()
        key = (identity, context)
        now = time.monotonic()
        for old, (created, _) in list(self.entries.items()):
            if not 0 <= now-created < self.ttl: self.entries.pop(old)
        flight = self.pending.get(key)
        if flight is None or flight.task.done():
            if reuse and key in self.entries:
                self.entries.move_to_end(key)
                return copy.deepcopy(self.entries[key][1])
            # A failed explicit refresh must not resurrect an older success.
            self.entries.pop(key, None)
            async def fetch():
                result = await read()
                return time.monotonic(), result
            flight = _Flight(asyncio.create_task(fetch()))
            self.pending[key] = flight
        flight.users += 1
        try:
            created, result = await asyncio.shield(flight.task)
            if self.pending.get(key) is flight:
                self.entries[key] = (created, result)
                self.entries.move_to_end(key)
                while len(self.entries) > self.limit: self.entries.popitem(last=False)
            return copy.deepcopy(result)
        finally:
            flight.users -= 1
            if not flight.users:
                if self.pending.get(key) is flight: self.pending.pop(key)
                # One departing page leaves the other readers intact; when all
                # leave, drain HTTP cleanup rather than orphaning cloud work.
                if not flight.task.done(): flight.task.cancel()
                await asyncio.gather(flight.task, return_exceptions=True)
