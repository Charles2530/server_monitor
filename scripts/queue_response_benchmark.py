"""Offline queue API benchmark; synthetic data, temporary DB, no cloud calls."""
import asyncio
import json
from pathlib import Path
import statistics
import tempfile
import time

import httpx

from server.main import create_app
from server.normalize import iso
from server.service import Monitor
from server.store import Store


async def measure(count):
    with tempfile.TemporaryDirectory() as root:
        app = create_app(Monitor(Store(Path(root)/'db'), None), background=False)
        queue = app.state.runtimes['maoshanwang'].utilization_queue
        now = time.time()
        queue.status, queue.updated_at, queue.checked_at = 'ok', iso(now), time.monotonic()
        queue.records = [dict(id=f'ACP:bench-{i}', kind='ACP', name=f'Task {i}',
            owner='example', cluster='example', gpu_count=8, replicas=1, is_mine=False,
            gpu_utilization=i % 100, metric_updated_at=iso(now), metric_status='ok',
            created_at=iso(now-86400), started_at=iso(now-3600), url=None) for i in range(count)]
        samples = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
            for i in range(35):
                started = time.perf_counter()
                response = await client.get('/api/utilization-queue')
                elapsed = (time.perf_counter()-started)*1000
                response.raise_for_status()
                assert len(response.json()['tasks']) == count
                if i >= 5: samples.append(elapsed)
        assert queue.refresh_task is None
        return {'tasks':count, 'median_ms':round(statistics.median(samples),3),
                'response_bytes':len(response.content), 'measured_requests':len(samples)}


async def main():
    print(json.dumps([await measure(count) for count in (11,1000)], indent=2))


if __name__ == '__main__':
    asyncio.run(main())
