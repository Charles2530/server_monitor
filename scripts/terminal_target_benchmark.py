"""Offline CCI/ACP target contention benchmark (no real cloud or shells).

Set PYTHONPATH to a code snapshot to compare the same workload before/after.
Each mocked HTTP read has 20 ms latency. This is not a live cloud benchmark.
"""
import asyncio
import json
import statistics
import time

import httpx

from server.cci_terminal import CCITerminal
from server.acp_terminal import ACPTerminal
from server.sites import MAOSHANWANG as site


async def run(kind, callers):
    calls=[]
    async def cloud(request):
        calls.append(1)
        await asyncio.sleep(.02)
        path=request.url.path
        if path.endswith('appsOwn'):
            return httpx.Response(200,json={'apps':[{'name':'example','id':site.workspace+'/apps/example',
                'display_name':'charles example','state':'RUNNING'}]})
        if path.endswith('/instances'):
            return httpx.Response(200,json={'instances':[{'name':'pod','app_name':'example','state':'RUNNING',
                'container_infos':[{'container_name':'main','container_type':'MAIN','container_state':'RUNNING'}]}]})
        if path.endswith('trainingJobsOwn'):
            return httpx.Response(200,json={'training_jobs':[{'name':'example','id':site.workspace+'/trainingJobs/example',
                'display_name':'charles example','state':'RUNNING'}]})
        assert path.endswith('/workers')
        return httpx.Response(200,json={'workers':[{'name':'worker','phase':'Running'}]})
    cls=CCITerminal if kind=='CCI' else ACPTerminal
    manager=cls(site,transport=httpx.MockTransport(cloud),session_loader=lambda:{'headers':{}})
    started=time.perf_counter()
    results=await asyncio.gather(*(manager.targets(kind+':example') for _ in range(callers)))
    elapsed=(time.perf_counter()-started)*1000
    assert all(result['targets'] for result in results)
    return elapsed,len(calls)


async def main():
    results=[]
    for kind in ('CCI','ACP'):
        for callers in (1,8):
            measurements=[await run(kind,callers) for _ in range(7)]
            results.append({'kind':kind,'callers':callers,'mock_latency_ms':20,
                'median_batch_ms':round(statistics.median(v[0] for v in measurements[2:]),2),
                'upstream_requests':sorted(set(v[1] for v in measurements))})
    print(json.dumps(results,indent=2))


if __name__=='__main__':asyncio.run(main())
