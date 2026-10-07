"""A shared editor preparation must not share a one-use browser entry ticket."""
import asyncio

import httpx
import pytest

from server.acp_actions import ActionError
from tests.test_ide_recovery import DATA, ORIGIN, existing_editor


@pytest.mark.asyncio
@pytest.mark.parametrize('delay', [0, 120])
async def test_shared_preparation_delivers_fresh_independent_browser_tickets(monkeypatch, delay):
    editor, session, _ = existing_editor(monkeypatch)
    gate = asyncio.Event()
    original = editor.open
    async def open_editor(*args, **kwargs):
        await gate.wait()
        return await original(*args, **kwargs)
    editor.open = open_editor
    proxy = session['proxy']
    try:
        first = await editor.begin('open', DATA, ORIGIN)
        assert await editor.begin('open', DATA, ORIGIN) == first
        gate.set()
        await editor.requests[first['request_id']]['task']
        # A background tab may not poll the completed preparation for minutes.
        for ticket in proxy.tickets:
            proxy.tickets[ticket] -= delay
        response = await editor.status(first['request_id'], ORIGIN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as browser_a:
            assert (await browser_a.get('/_ilab_open/' + response['result']['ticket'])).status_code == 303
        second = await editor.status(first['request_id'], ORIGIN)
        assert second['result']['ticket'] != response['result']['ticket']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as browser_b:
            assert (await browser_b.get('/_ilab_open/' + second['result']['ticket'])).status_code == 303
        with pytest.raises(ActionError):
            await editor.status(first['request_id'], 'http://localhost:9000')
        editor.sessions.clear()
        with pytest.raises(ActionError):
            await editor.status(first['request_id'], ORIGIN)
    finally:
        await proxy.close()
        await editor.close()


@pytest.mark.asyncio
async def test_authenticated_browser_can_restore_consumed_entry_without_reauthorizing(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch)
    proxy = session['proxy']
    try:
        ticket = proxy.ticket()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as browser:
            assert (await browser.get('/_ilab_open/' + ticket)).status_code == 303
            restored = await browser.get('/_ilab_open/' + ticket)
            assert restored.status_code == 303
            assert restored.headers['location'] == '/'
            assert 'set-cookie' not in restored.headers
            assert restored.headers['cache-control'] == 'no-store'
            assert (await browser.get('/_ilab_open/' + ticket, headers={'origin': 'http://evil.test'})).status_code == 403
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as outsider:
            expired = await outsider.get('/_ilab_open/' + ticket)
            assert expired.status_code == 403
            assert 'ilab-editor-entry-expired' in expired.text
            assert 'no-store' == expired.headers['cache-control']
    finally:
        await editor.close()


@pytest.mark.asyncio
async def test_reauthentication_ticket_sets_cookie_without_loading_another_workbench(monkeypatch):
    editor, session, _ = existing_editor(monkeypatch)
    proxy = session['proxy']
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as browser:
            ticket = proxy.ticket()
            result = await browser.get('/_ilab_open/' + ticket + '?reauth=1')
            assert result.status_code == 200
            assert 'ilab-editor-authenticated' in result.text
            assert 'HttpOnly' in result.headers['set-cookie']
            assert result.headers['cache-control'] == 'no-store'
            assert "script-src 'nonce-" in result.headers['content-security-policy']
            assert "frame-ancestors 'self' " + ORIGIN in result.headers['content-security-policy']
            assert 'vscode-workbench' not in result.text
            assert 'remote=old' not in result.text
            assert (await browser.get('/_ilab_frame_status.js')).status_code == 200
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as outsider:
            denied = await outsider.get('/_ilab_open/' + ticket + '?reauth=1')
            assert denied.status_code == 403
            assert 'ilab-editor-authenticated' not in denied.text
            denied = await outsider.get('/_ilab_open/' + proxy.ticket() + '?reauth=1', headers={'origin':'http://evil.test'})
            assert denied.status_code == 403 and 'set-cookie' not in denied.headers
    finally:
        await editor.close()
