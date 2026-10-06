"""Exercise real login lifecycle with in-memory browser doubles only."""
import asyncio
from functools import partial
from types import SimpleNamespace

import pytest

from server import auth, service
from server.store import Store
from tests.test_service import Fake


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['launch', 'context', 'close', 'driver_exit'])
async def test_browser_lifecycle_stalls_do_not_block_monitor(tmp_path, monkeypatch, stage):
    entered, release = asyncio.Event(), asyncio.Event()
    async def stall(at):
        if at == stage:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Simulate delayed process/transport cleanup following cancellation.
                await release.wait()
                raise
    class Browser:
        async def new_context(self, **kwargs):
            await stall('context')
            return object()
        async def close(self): await stall('close')
    class Driver:
        async def launch(self, **kwargs):
            await stall('launch')
            return Browser()
        async def __aenter__(self): return SimpleNamespace(chromium=self)
        async def __aexit__(self, *args): await stall('driver_exit')
    async def authenticate(*args, **kwargs): return {'headers':{}}
    site = SimpleNamespace(id='test', data=tmp_path)
    monkeypatch.setattr(auth, 'async_playwright', Driver)
    monkeypatch.setattr(auth, 'load_session', lambda *args: {})
    monkeypatch.setattr(auth, 'save_session', lambda *args: None)
    monkeypatch.setattr(auth, 'authenticate', authenticate)
    monkeypatch.setattr(service, 'RENEW_TIMEOUT', .02)
    monkeypatch.setattr(service, 'CONNECT_TIMEOUT', .05)
    monitor = service.Monitor(Store(tmp_path/'db'), Fake(), partial(auth.connect_browser, site=site))
    task = asyncio.create_task(monitor.connect())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        done, _ = await asyncio.wait({task}, timeout=.2)
        assert task in done
        assert not monitor.state['connecting'] and not monitor.lock.locked()
        assert monitor.state['status'] == 'disconnected'
        assert await monitor.refresh()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await monitor.close()
        auth.LOGIN_LOCKS.pop(str(tmp_path), None)


@pytest.mark.asyncio
async def test_authentication_diagnostics_do_not_log_upstream_error_body(caplog):
    class Context:
        def on(self, *args): pass
        def remove_listener(self, *args): pass
        async def new_page(self): raise RuntimeError('SECRET token=private password=private')
    with pytest.raises(auth.AuthRequired):
        await auth.authenticate(Context())
    assert 'stage=authenticate' in caplog.text and 'RuntimeError' in caplog.text
    assert 'SECRET' not in caplog.text and 'private' not in caplog.text
