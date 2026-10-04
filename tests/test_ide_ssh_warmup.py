import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.acp_actions import ActionError
from server.cci_ide import CCIEditor
from server.sites import MAOSHANWANG
from tests.test_ide_recovery import DATA, ORIGIN


def cold_editor(monkeypatch, *, warm_failure=False, forward_failure=False):
    import server.cci_ide as module
    opened = asyncio.Event(); warming = asyncio.Event(); cancelled = asyncio.Event(); forwards = []
    class Forward:
        def __init__(self, alias, address, port):
            self.alias, self.address, self.remote_port = alias, address, port
            self.port = 34567; self.process = SimpleNamespace(returncode=None)
            self.warmed = False; self.closed = False; forwards.append(self)
        async def warm(self):
            self.warmed = True; warming.set()
            try:
                await opened.wait()
                if warm_failure: raise ActionError('test warm failure')
            finally: cancelled.set()
        async def start(self):
            assert self.remote_port == 45678
            if self.warmed and forward_failure: raise ActionError('test control forwarding failure')
        async def close(self): self.closed = True; self.process.returncode = 0
    async def bootstrap(*args, **kwargs):
        opened.set()
        try: await asyncio.wait_for(warming.wait(), .2)
        except asyncio.TimeoutError: pytest.fail('SSH authentication did not overlap bootstrap')
        return {'port': 45678, 'password': 'p' * 48}
    async def start_proxy(proxy): proxy.port = 23456
    monkeypatch.setattr(module, 'SSHForward', Forward)
    monkeypatch.setattr(module.EditorProxy, 'start', start_proxy)
    installer = AsyncMock(side_effect=bootstrap)
    monkeypatch.setattr(module, 'run_python', installer)
    editor = CCIEditor(MAOSHANWANG, None, None)
    editor.resolve = AsyncMock(return_value=({'name': 'charles'}, '10.0.0.1'))
    editor.authenticate = AsyncMock(return_value='remote=cookie')
    return editor, forwards, installer, opened, warming, cancelled


@pytest.mark.asyncio
@pytest.mark.parametrize('warm_failure,forward_failure', [(False, False), (True, False), (False, True)])
async def test_cold_open_overlaps_ssh_and_transfers_owned_forward(monkeypatch, warm_failure, forward_failure):
    editor, forwards, installer, *_ = cold_editor(monkeypatch, warm_failure=warm_failure, forward_failure=forward_failure)
    try:
        result = await editor.open(DATA, ORIGIN)
        assert result['port'] == 23456 and installer.await_count == 1
        assert len(forwards) == (2 if warm_failure or forward_failure else 1)
        assert forwards[0].warmed
        assert editor.sessions[(*DATA.values(), ORIGIN)]['forward'] is forwards[-1]
        assert not forwards[-1].closed
        if len(forwards) == 2: assert forwards[0].closed
    finally: await editor.close()
    assert all(forward.closed for forward in forwards)


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_failed_or_cancelled_bootstrap_joins_warmup_before_cleanup(monkeypatch, cancel):
    import server.cci_ide as module
    editor, forwards, _, opened, warming, cancelled = cold_editor(monkeypatch)
    async def bootstrap(*args, **kwargs):
        try: await asyncio.wait_for(warming.wait(), .2)
        except asyncio.TimeoutError: pytest.fail('No owned SSH warmup to clean up')
        if not cancel: raise ActionError('test bootstrap failure')
        await asyncio.Event().wait()
    monkeypatch.setattr(module, 'run_python', bootstrap)
    job = asyncio.create_task(editor.open(DATA, ORIGIN))
    try:
        if cancel:
            try: await asyncio.wait_for(warming.wait(), .3)
            except asyncio.TimeoutError: pytest.fail('Cold open did not start SSH warmup')
            job.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else ActionError): await job
        assert cancelled.is_set() and all(forward.closed for forward in forwards)
        assert not editor.sessions and not editor.preparing
    finally:
        job.cancel(); await asyncio.gather(job, return_exceptions=True); await editor.close()


@pytest.mark.asyncio
async def test_warmup_does_not_precede_ownership_verification(monkeypatch):
    editor, forwards, installer, *_ = cold_editor(monkeypatch)
    editor.resolve = AsyncMock(side_effect=ActionError('not owned'))
    try:
        with pytest.raises(ActionError): await editor.open(DATA, ORIGIN)
        assert not forwards
        installer.assert_not_awaited()
    finally: await editor.close()


@pytest.mark.asyncio
async def test_authentication_failure_reclaims_warmed_master(monkeypatch):
    editor, forwards, _, *_ = cold_editor(monkeypatch)
    editor.authenticate = AsyncMock(side_effect=ActionError('test auth failure'))
    try:
        with pytest.raises(ActionError): await editor.open(DATA, ORIGIN)
        assert len(forwards) == 1 and forwards[0].closed
        assert not editor.sessions
    finally: await editor.close()


@pytest.mark.asyncio
async def test_cancellation_at_connection_handoff_cannot_orphan_proxy_or_master(monkeypatch):
    editor, forwards, _, *_ = cold_editor(monkeypatch)
    original = editor.connect; connected = []
    async def connect(*args, **kwargs):
        session = await original(*args, **kwargs)
        connected.append(session)
        asyncio.current_task().cancel()
        return session
    monkeypatch.setattr(editor, 'connect', connect)
    opening = asyncio.create_task(editor.open(DATA, ORIGIN))
    try:
        with pytest.raises(asyncio.CancelledError): await opening
    finally: await editor.close()
    assert connected and forwards[0].closed
    assert connected[0]['proxy'].client.is_closed


def fake_ssh_processes(monkeypatch, *, control_failure=False, publish_socket=True, master_exited=False):
    import socket
    from pathlib import Path
    import server.cci_ide as module
    commands = []; sockets = []; processes = []; created = asyncio.Event()
    class Process:
        def __init__(self, returncode=None):
            self.returncode = returncode; self.done = asyncio.Event()
            if returncode is not None: self.done.set()
        def terminate(self): self.returncode = 0; self.done.set()
        def kill(self): self.returncode = -9; self.done.set()
        async def wait(self): await self.done.wait(); return self.returncode
    async def spawn(*command, **kwargs):
        commands.append(command)
        if '-M' in command:
            process = Process(1 if master_exited else None)
            if publish_socket and not master_exited:
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(command[command.index('-S') + 1]); sockets.append(listener)
            created.set()
        else: process = Process(1 if control_failure else 0)
        processes.append(process)
        return process
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
    writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
    monkeypatch.setattr(module.asyncio, 'open_connection', AsyncMock(return_value=(None, writer)))
    return commands, sockets, processes, created


@pytest.mark.asyncio
@pytest.mark.parametrize('control_failure,master_exited', [(False, False), (True, False), (False, True)])
async def test_private_master_forwarding_and_cleanup(monkeypatch, control_failure, master_exited):
    from pathlib import Path
    from server.cci_ide import SSHForward
    commands, sockets, processes, _ = fake_ssh_processes(monkeypatch, control_failure=control_failure, master_exited=master_exited)
    forward = SSHForward('test-jump', '10.0.0.1', None)
    try:
        assert hasattr(forward, 'warm'), 'A cold open needs an owned SSH preconnection'
        if master_exited:
            with pytest.raises(ActionError): await forward.warm()
        else:
            await forward.warm()
            directory = Path(commands[0][commands[0].index('-S') + 1]).parent
            assert directory.stat().st_mode & 0o777 == 0o700
            assert len(str(directory / 'ssh').encode()) < 100
            for option in ('ControlPersist=no', 'ForkAfterAuthentication=no', 'ClearAllForwardings=yes', 'StrictHostKeyChecking=yes'):
                assert option in commands[0]
            assert '-L' not in commands[0]
            forward.remote_port = 45678
            if control_failure:
                with pytest.raises(ActionError): await forward.start()
            else:
                await forward.start()
                assert forward.process is processes[0]
                control = commands[1]
                assert control[control.index('-O') + 1] == 'forward'
                assert control[control.index('-F') + 1] == '/dev/null'
                assert 'ClearAllForwardings=yes' not in control
                assert control[control.index('-L') + 1].endswith(':10.0.0.1:45678')
    finally:
        await forward.close()
        for listener in sockets: listener.close()
    assert all(process.returncode is not None for process in processes)
    directory = Path(commands[0][commands[0].index('-S') + 1]).parent
    assert not directory.exists()


@pytest.mark.asyncio
async def test_cancelled_master_authentication_removes_owned_process_and_socket_directory(monkeypatch):
    from pathlib import Path
    from server.cci_ide import SSHForward
    commands, sockets, processes, created = fake_ssh_processes(monkeypatch, publish_socket=False)
    forward = SSHForward('test-jump', '10.0.0.1', None)
    assert hasattr(forward, 'warm'), 'SSH warmup must support structured cancellation'
    job = asyncio.create_task(forward.warm())
    try:
        await asyncio.wait_for(created.wait(), .3)
        job.cancel()
        with pytest.raises(asyncio.CancelledError): await job
        assert processes[0].returncode is not None
        assert not Path(commands[0][commands[0].index('-S') + 1]).parent.exists()
    finally:
        job.cancel(); await asyncio.gather(job, return_exceptions=True); await forward.close()
