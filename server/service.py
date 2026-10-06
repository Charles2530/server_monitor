import asyncio
import copy
import logging
import time
from .auth import AuthRequired, connect_browser
from .config import INTERVAL
from .normalize import iso
from .store import store_call

CONNECT_TIMEOUT = 90
RENEW_TIMEOUT = 90
logger = logging.getLogger(__name__)

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
        self.refreshing = None
        self.renewing = None
        self.loop_task = None

    def spawn(self, coroutine):
        task=asyncio.create_task(coroutine)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        return task

    def log_failure(self, stage, error):
        site = getattr(getattr(self.collector, 'site', None), 'id', 'default')
        reason = 'timeout' if isinstance(error, asyncio.TimeoutError) else type(error).__name__
        # Exception messages/tracebacks may contain credentials or upstream bodies.
        logger.warning('monitor_failure site=%s stage=%s reason=%s', site, stage, reason)

    def request_refresh(self):
        if self.state.get('connecting'):
            return {'status':'reconnecting', 'started':False, 'message':'正在重连，完成后会自动采集。'}
        if self.lock.locked() or (self.refreshing is not None and not self.refreshing.done()):
            return {'status':'collecting', 'started':False, 'message':'正在采集，完成后页面会自动更新。'}
        self.refreshing = self.spawn(self.refresh())
        return {'status':'started', 'started':True, 'message':'刷新已启动，完成后页面会自动更新。'}

    def request_connect(self):
        if self.state.get('connecting') or (self.connecting is not None and not self.connecting.done()):
            return {'status':'reconnecting', 'started':False, 'message':'正在重连，请等待当前连接完成。'}
        self.state['connecting'] = True
        self.connecting = self.spawn(self.connect())
        def finished(task):
            if self.connecting is task:
                self.state['connecting'] = False
        self.connecting.add_done_callback(finished)
        return {'status':'reconnecting', 'started':True, 'message':'正在重连，连接成功后会自动采集。'}

    async def renew_session(self):
        if self.renewing is not None and not self.renewing.done():
            raise AuthRequired('上次登录仍在清理，请稍后重试。')
        self.state['connecting'] = True
        self.last_renewal = time.monotonic()
        task = self.renewing = self.spawn((self.renew or connect_browser)(headless=True, timeout=75))
        # Observe late cleanup failures without exposing exception text or leaving
        # an unhandled task. The login lock prevents overlapping browser sessions.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            done, _ = await asyncio.wait({task}, timeout=RENEW_TIMEOUT)
            if not done:
                raise asyncio.TimeoutError()
            return task.result()
        except (Exception, asyncio.CancelledError) as error:
            self.log_failure('renew', error)
            raise
        finally:
            if not task.done():
                task.cancel()
            # Browser cleanup must not retain the monitor's collection lock.
            self.state['connecting'] = False

    async def collect(self):
        try:
            return await self.collector.collect(copy.deepcopy(self.state))
        except AuthRequired:
            self.state['connected'] = False
            if self.renew is None or self.state.get('connecting') or time.monotonic()-self.last_renewal < 900:
                raise
            try:
                await self.renew_session()
            except asyncio.TimeoutError:
                raise
            except Exception:
                raise AuthRequired('自动登录暂未成功。') from None
            return await self.collector.collect(copy.deepcopy(self.state))

    async def refresh(self):
        if self.lock.locked():
            return False
        async with self.lock:
            self.state.update(status='collecting',last_attempt_at=iso(time.time()))
            stage = 'collect'
            try:
                snapshot=await asyncio.wait_for(self.collect(),timeout=150)
                stage = 'save'
                await self.store_call(self.store.save,snapshot,time.time())
                self.state.update(snapshot)
            except asyncio.CancelledError as error:
                self.log_failure(stage, error)
                self.state.update(status='error',error='采集已取消，保留上次快照，可重新刷新。')
                raise
            except AuthRequired as error:
                self.log_failure(stage, error)
                self.state.update(status='disconnected',connected=False,error='自动登录暂未成功，请检查本地登录配置与网络，或点击“重新连接”重试。')
            except asyncio.TimeoutError as error:
                self.log_failure(stage, error)
                self.state.update(status='error',error='采集或自动重连超时，保留上次快照，将自动重试。')
            except Exception as error:
                self.log_failure(stage, error)
                self.state.update(status='error',error='采集失败，保留上次快照。请检查网络或重新连接。')
            finally:
                try:
                    await self.store_call(self.store.prune,time.time())
                except Exception as error:
                    self.log_failure('prune', error)
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
        acquired = False
        async def login():
            nonlocal acquired
            async with self.lock:
                acquired = True
                await self.renew_session()
                self.state['connected']=True
        try:
            # Includes waiting for the collector, browser startup and login cleanup.
            await asyncio.wait_for(login(), timeout=CONNECT_TIMEOUT)
            await self.refresh()
        except asyncio.CancelledError as error:
            self.log_failure('connect' if acquired else 'connect_wait', error)
            if acquired:
                self.state.update(status='disconnected',connected=False,error='重连已取消，可重新连接。')
            raise
        except asyncio.TimeoutError as error:
            self.log_failure('connect' if acquired else 'connect_wait', error)
            if acquired:
                self.state.update(status='disconnected',connected=False,error='自动重连超时，保留上次快照，可重新连接。')
            else:
                self.state['error']='等待当前采集结束超时，请稍后重试。'
        except Exception as error:
            self.log_failure('connect', error)
            self.state.update(status='disconnected',connected=False,error='自动 IAM 登录未完成，请检查本地凭据、网络或平台验证要求后重试。')
        finally:
            self.state['connecting']=False

    async def close(self):
        pending=list(self.jobs)
        if self.loop_task:
            pending.append(self.loop_task)
        for task in pending: task.cancel()
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=5)
            if unfinished:
                self.log_failure('shutdown_cleanup', asyncio.TimeoutError())
        self.state['connecting'] = False
