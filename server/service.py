import asyncio
import copy
import logging
import time
from .auth import AUTH_TIMEOUT, AuthRequired, connect_browser
from .config import INTERVAL
from .normalize import iso
from .store import store_call
from .runtime_profile import get_runtime_profile

RENEW_TIMEOUT = AUTH_TIMEOUT + 30
CONNECT_TIMEOUT = AUTH_TIMEOUT + 60
COLLECT_TIMEOUT = AUTH_TIMEOUT + 30
CONNECTION_LOG_LIMIT = 40
logger = logging.getLogger(__name__)

class Monitor:
    def __init__(self, store, collector, renew=None):
        self.store, self.collector = store, collector
        self.renew = renew
        self.last_renewal = float("-inf")
        self.lock = asyncio.Lock()
        self.state = store.latest() or {'tasks':[], 'clusters':[], 'source_status':{},'updated_at':None,'connected':False}
        self.state.setdefault('waiting_tasks',[])
        if not isinstance(self.state.get('connection_log'), list):
            self.state['connection_log'] = []
        self.state['runtime_profile'] = get_runtime_profile()
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

    def site_id(self):
        return getattr(getattr(self.collector, 'site', None), 'id', 'default')

    def log_connection(self, stage, result, elapsed=None):
        # Connection-stage trace for fault diagnosis. Only records the site id,
        # stage, result and elapsed time; never credentials, tokens, URLs or
        # upstream bodies. Reuses the same logger as log_failure below.
        fields = {'site': self.site_id(), 'stage': stage, 'result': result}
        if elapsed is not None:
            fields['elapsed_ms'] = int(elapsed * 1000)
        self.record_connection(stage, result, elapsed=elapsed)
        logger.info('monitor_connection ' + ' '.join('%s=%s' % item for item in fields.items()))

    def log_failure(self, stage, error, elapsed=None):
        site = self.site_id()
        reason = 'timeout' if isinstance(error, asyncio.TimeoutError) else type(error).__name__
        # Exception messages/tracebacks may contain credentials or upstream bodies.
        fields = {'site': site, 'stage': stage, 'reason': reason}
        if elapsed is not None:
            fields['elapsed_ms'] = int(elapsed * 1000)
        self.record_connection(stage, 'error', reason=reason, elapsed=elapsed)
        logger.warning('monitor_failure ' + ' '.join('%s=%s' % item for item in fields.items()))

    def record_connection(self, stage, result, *, reason=None, elapsed=None):
        event = {'at': iso(time.time()), 'stage': stage, 'result': result}
        if reason: event['reason'] = reason
        if elapsed is not None: event['elapsed_ms'] = int(max(0, elapsed) * 1000)
        events = self.state.setdefault('connection_log', [])
        events.append(event)
        del events[:-CONNECTION_LOG_LIMIT]

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
        started = time.monotonic()
        task = self.renewing = self.spawn((self.renew or connect_browser)(headless=True, timeout=75))
        # Observe late cleanup failures without exposing exception text or leaving
        # an unhandled task. The login lock prevents overlapping browser sessions.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            done, _ = await asyncio.wait({task}, timeout=RENEW_TIMEOUT)
            if not done:
                raise asyncio.TimeoutError()
            self.log_connection('renew', 'ok', time.monotonic() - started)
            return task.result()
        except (Exception, asyncio.CancelledError) as error:
            self.log_failure('renew', error, time.monotonic() - started)
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
            started = time.monotonic()
            stage = 'collect'
            try:
                self.log_connection('collect', 'start', 0)
                snapshot=await asyncio.wait_for(self.collect(),timeout=COLLECT_TIMEOUT)
                stage = 'save'
                await self.store_call(self.store.save,snapshot,time.time())
                self.state.update(snapshot)
                self.log_connection('collect', 'ok', time.monotonic() - started)
            except asyncio.CancelledError as error:
                self.log_failure(stage, error, time.monotonic() - started)
                self.state.update(status='error',error='采集已取消，保留上次快照，可重新刷新。')
                raise
            except AuthRequired as error:
                self.log_failure(stage, error, time.monotonic() - started)
                self.state.update(status='disconnected',connected=False,error='自动登录暂未成功，请检查本地登录配置与网络，或点击“重新连接”重试。')
            except asyncio.TimeoutError as error:
                self.log_failure(stage, error, time.monotonic() - started)
                self.state.update(status='error',error='采集或自动重连超时，保留上次快照，将自动重试。')
            except Exception as error:
                self.log_failure(stage, error, time.monotonic() - started)
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
        started = time.monotonic()
        acquired = False
        self.log_connection('connect', 'start', 0)
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
            self.log_connection('connect', 'ok' if self.state.get('connected') else 'error', time.monotonic() - started)
        except asyncio.CancelledError as error:
            self.log_failure('connect' if acquired else 'connect_wait', error, time.monotonic() - started)
            if acquired:
                self.state.update(status='disconnected',connected=False,error='重连已取消，可重新连接。')
            raise
        except asyncio.TimeoutError as error:
            self.log_failure('connect' if acquired else 'connect_wait', error, time.monotonic() - started)
            if acquired:
                self.state.update(status='disconnected',connected=False,error='自动重连超时，保留上次快照，可重新连接。')
            else:
                self.state['error']='等待当前采集结束超时，请稍后重试。'
        except Exception as error:
            self.log_failure('connect', error, time.monotonic() - started)
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
