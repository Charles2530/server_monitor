import asyncio
import copy
import time
from .auth import AuthRequired, connect_browser
from .config import INTERVAL
from .normalize import iso
from .store import store_call

class Monitor:
    def __init__(self, store, collector, renew=None):
        self.store, self.collector = store, collector
        self.renew = renew
        self.last_renewal = float("-inf")
        self.lock = asyncio.Lock()
        self.state = store.latest() or {'tasks':[], 'clusters':[], 'source_status':{},'updated_at':None,'connected':False}
        self.state.setdefault('waiting_tasks',[])
        self.state.update(status='collecting',error=None,interval_seconds=INTERVAL,last_attempt_at=None,next_run_at=None,connecting=False)
        self.jobs = set()
        self.connecting = None
        self.loop_task = None

    def spawn(self, coroutine):
        task=asyncio.create_task(coroutine)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        return task

    async def collect(self):
        try:
            return await self.collector.collect(copy.deepcopy(self.state))
        except AuthRequired:
            if self.renew is None or self.state.get('connecting') or time.monotonic()-self.last_renewal < 900:
                raise
            self.last_renewal=time.monotonic()
            try:
                await self.renew(headless=True, timeout=75)
                return await self.collector.collect(copy.deepcopy(self.state))
            except Exception:
                raise AuthRequired('自动登录暂未成功。') from None

    async def refresh(self):
        if self.lock.locked():
            return False
        async with self.lock:
            self.state.update(status='collecting',last_attempt_at=iso(time.time()))
            try:
                snapshot=await asyncio.wait_for(self.collect(),timeout=150)
                await self.store_call(self.store.save,snapshot,time.time())
                self.state.update(snapshot)
            except AuthRequired:
                self.state.update(status='disconnected',connected=False,error='自动登录暂未成功，请检查本地登录配置与网络，或点击“重新连接”重试。')
            except Exception:
                self.state.update(status='error',error='采集失败，保留上次快照。请检查网络或重新连接。')
            finally:
                try:
                    await self.store_call(self.store.prune,time.time())
                except Exception:
                    self.state.update(status='error', error='本地历史清理失败，请检查磁盘空间和文件权限。')
                self.state['next_run_at']=iso(time.time()+INTERVAL)
            return True

    async def store_call(self, callback, *args):
        return await store_call(callback, *args)

    async def loop(self):
        while True:
            start=time.monotonic()
            await self.refresh()
            delay=max(1,INTERVAL-(time.monotonic()-start))
            self.state['next_run_at']=iso(time.time()+delay)
            await asyncio.sleep(delay)

    async def connect(self):
        self.state['connecting']=True
        try:
            async with self.lock:
                await (self.renew or connect_browser)(headless=True,timeout=75)
                self.state['connected']=True
            await self.refresh()
        except Exception:
            self.state.update(status='disconnected',connected=False,error='自动 IAM 登录未完成，请检查本地凭据、网络或平台验证要求后重试。')
        finally:
            self.state['connecting']=False

    async def close(self):
        pending=list(self.jobs)
        if self.loop_task:
            pending.append(self.loop_task)
        for task in pending: task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
