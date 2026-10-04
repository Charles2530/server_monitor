"""Offline hot-path benchmark using synthetic tasks and a temporary database.

Run from the repository root: .venv/bin/python -m scripts.runtime_benchmark
No cloud requests, credentials or live databases are used.
"""
import asyncio
import json
from pathlib import Path
from dataclasses import replace
import statistics
import tempfile
import time
from unittest.mock import patch

from server.service import Monitor
from server.store import Store


def fixture():
    now = int(time.time() // 60) * 60
    tasks = [
        {'id': f'ACP:bench-{i}', 'kind': 'ACP', 'state': 'running', 'series': [
            {'metric': metric, 'label': f'worker-{worker}',
             'points': [[now - (59 - j) * 60, float(j % 100)] for j in range(60)]}
            for worker in range(8) for metric in ('cpu', 'gpu', 'memory', 'gpu_memory')
        ]}
        for i in range(20)
    ]
    return {'tasks': tasks, 'clusters': [], 'status': 'ok', 'connected': True}


async def main():
    state = fixture()
    with tempfile.TemporaryDirectory() as root:
        # Keep the API's module-level initialization inside the temporary root.
        from server.sites import SITES
        isolated = {key: replace(site, data=Path(root) / key) for key, site in SITES.items()}
        with patch('server.sites.SITES', isolated), patch('server.config.DATA', Path(root)):
            from server.main import create_app
        import httpx
        store = Store(Path(root) / 'benchmark.sqlite3')
        store.save(state, time.time())
        durations = []
        for _ in range(7):
            start = time.perf_counter()
            store.save(state, time.time())
            durations.append((time.perf_counter() - start) * 1000)
        monitor = Monitor(store, None)
        monitor.state.update(state)
        app = create_app(monitor, background=False)
        timings = []
        histories = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
            for _ in range(12):
                start = time.perf_counter()
                response = await client.get('/api/state')
                response.raise_for_status()
                timings.append((time.perf_counter() - start) * 1000)
            state_bytes = len(response.content)
            for _ in range(12):
                start = time.perf_counter()
                response = await client.get('/api/history', params={'task_id': 'ACP:bench-0', 'hours': 168})
                response.raise_for_status()
                histories.append((time.perf_counter() - start) * 1000)
        print(json.dumps({
            'synthetic_tasks': 20, 'workers_per_task': 8, 'samples': 38400,
            'repeat_save_median_ms': round(statistics.median(durations), 3),
            'state_api_median_ms': round(statistics.median(timings), 3),
            'state_api_bytes': state_bytes,
            'history_api_median_ms': round(statistics.median(histories), 3),
        }, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
