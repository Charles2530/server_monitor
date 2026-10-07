"""Exercise the history reader through the actual shell framing/parser."""
import asyncio
import base64
import contextlib
import io
import json
import re
import sqlite3

import pytest

from server.acp_actions import ActionError
from server.cci_ide import CCIEditor, run_python
from server.sites import MAOSHANWANG


class ScriptBridge:
    def __init__(self, response=None):
        self.commands = []
        self.response = response
        self.pending = None
        self.closed = False
        self.reads = 0
        self.started = asyncio.Event()

    async def open(self, data, owner):
        return {'session_id': 'owned'}

    async def write(self, key, seq, data, size, owner):
        self.commands.append(data)

    async def read(self, key, cursor, owner):
        self.reads += 1
        self.started.set()
        if self.pending is None:
            parts = re.findall(r"'([A-Za-z0-9+/=]+)'", ''.join(self.commands))
            payload = base64.b64decode(''.join(parts)).decode()
            tag = re.search(r"chr\(30\)\+'([a-f0-9]+)'", payload).group(1)
            if self.response:
                self.pending = self.response(tag)
            else:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exec(payload, {})
                self.pending = output.getvalue().encode()
        if not self.pending:
            await asyncio.Event().wait()
        chunk, self.pending = self.pending[:4096], self.pending[4096:]
        return {'chunks': [base64.b64encode(chunk).decode()], 'cursor': cursor + 1, 'closed': False}

    async def disconnect(self, key, owner):
        self.closed = True


class Closeable:
    async def close(self):
        pass


def history_editor(bridge, workspace='/workspace/project'):
    editor = CCIEditor(MAOSHANWANG, bridge, object())
    data = {'task_id': 'CCI:job', 'instance': 'pod', 'container': 'main'}
    origin = 'http://localhost:8766'
    editor.sessions[('CCI:job', 'pod', 'main', origin)] = {
        'workspace': workspace, 'proxy': Closeable(), 'forward': Closeable()}
    return editor, data, origin


@pytest.mark.asyncio
@pytest.mark.parametrize('title', ['Ordinary project discussion', '项目讨论' * 45])
async def test_full_history_passes_real_transport_above_bootstrap_limit(tmp_path, monkeypatch, title):
    home = tmp_path / '.codex'
    home.mkdir()
    with sqlite3.connect(home / 'state_5.sqlite') as db:
        db.execute('create table threads (id text, cwd text, title text, updated_at integer)')
        db.executemany('insert into threads values (?, ?, ?, ?)',
                       [(str(i), '/workspace/project', title, i) for i in range(200)])
    monkeypatch.setenv('CODEX_HOME', str(home))
    bridge = ScriptBridge()
    editor, data, origin = history_editor(bridge)
    try:
        result = await editor.history(data, origin)
        assert len(result['records']) == 200
        assert len(json.dumps(result).encode()) > 16384
        assert result['truncated'] is False
    finally:
        await editor.close()
    assert bridge.closed


@pytest.mark.asyncio
async def test_default_bootstrap_limit_is_unchanged_and_cleans_up():
    bridge = ScriptBridge(lambda tag: b'\x1e' + tag.encode() + json.dumps({'large': 'x' * 17000}).encode() + b'\x1f')
    with pytest.raises(ActionError):
        await run_python(bridge, {}, 'RESULT = {}')
    assert bridge.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('terminated', [True, False])
async def test_history_rejects_oversize_frames_promptly_and_redacts(terminated):
    bridge = ScriptBridge(lambda tag: b'\x1e' + tag.encode() + b'x' * (512 * 1024 + 1) + (b'\x1f' if terminated else b''))
    editor, data, origin = history_editor(bridge)
    try:
        with pytest.raises(ActionError) as raised:
            await asyncio.wait_for(editor.history(data, origin), 1)
        assert raised.value.code == 'history_unavailable'
        assert bridge.closed
    finally:
        await editor.close()


@pytest.mark.asyncio
async def test_history_cancellation_closes_transport_and_releases_lock():
    bridge = ScriptBridge(lambda tag: b'\x1e' + tag.encode() + b'{')
    editor, data, origin = history_editor(bridge)
    task = asyncio.create_task(editor.history(data, origin))
    await bridge.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bridge.closed
    assert not editor.locks
    await editor.close()


@pytest.mark.asyncio
async def test_malformed_json_frame_is_redacted_and_closes():
    bridge = ScriptBridge(lambda tag: b'\x1e' + tag.encode() + b'{private malformed text}\x1f')
    editor, data, origin = history_editor(bridge)
    try:
        with pytest.raises(ActionError) as raised:
            await editor.history(data, origin)
        assert raised.value.code == 'history_unavailable'
        assert 'private' not in str(raised.value)
        assert bridge.closed
    finally:
        await editor.close()


@pytest.mark.asyncio
async def test_history_limit_does_not_relax_progress_frame_limit():
    bridge = ScriptBridge(lambda tag: b'\x1e' + tag.encode() + json.dumps({
        '__ilab_progress__': {'stage': 'checking'}, 'padding': 'x' * 17000}).encode() + b'\x1f')
    with pytest.raises(ActionError):
        await run_python(bridge, {}, 'RESULT = {}', max_result_bytes=512 * 1024)
    assert bridge.closed


@pytest.mark.asyncio
async def test_unframed_output_remains_bounded_and_closes():
    bridge = ScriptBridge(lambda tag: b'x' * (1024 * 1024 + 1))
    with pytest.raises(ActionError, match='输出过长'):
        await run_python(bridge, {}, 'RESULT = {}', max_result_bytes=512 * 1024)
    assert bridge.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['missing_home', 'missing_sources', 'corrupt_index', 'corrupt_index_empty_sessions', 'invalid_rollout', 'unreadable_sessions'])
async def test_unavailable_history_sources_fail_open_without_caching(tmp_path, monkeypatch, source):
    home = tmp_path / '.codex'
    if source != 'missing_home':
        home.mkdir()
    if source in ('corrupt_index', 'corrupt_index_empty_sessions'):
        (home / 'state_5.sqlite').write_bytes(b'not a sqlite database')
    if source == 'corrupt_index_empty_sessions':
        (home / 'sessions').mkdir()
    if source in ('invalid_rollout', 'unreadable_sessions'):
        sessions = home / 'sessions'
        sessions.mkdir()
        (sessions / 'rollout-bad.jsonl').write_text('invalid metadata\n')
        if source == 'unreadable_sessions':
            import os
            original = os.scandir
            def denied(path):
                if str(path) == str(sessions):
                    raise PermissionError('private directory')
                return original(path)
            monkeypatch.setattr(os, 'scandir', denied)
    monkeypatch.setenv('CODEX_HOME', str(home))
    bridge = ScriptBridge()
    editor, data, origin = history_editor(bridge)
    try:
        with pytest.raises(ActionError) as raised:
            await editor.history(data, origin)
        assert raised.value.code == 'history_unavailable'
        assert 'private' not in str(raised.value)
        assert not editor.sessions[('CCI:job', 'pod', 'main', origin)]['history_caches']
    finally:
        await editor.close()
    assert bridge.closed
