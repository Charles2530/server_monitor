"""Dedicated TokenHub session; never persist GitHub credentials or cookies."""
import asyncio
import json
import math
import os
import tempfile
import time
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

from .browser import browser_launch_options
from .config import DATA

ORIGIN = 'https://tokenhub.ilab.beer'
API = ORIGIN + '/api/v1'


class TokenHubError(Exception):
    def __init__(self, message='TokenHub 读取失败，请稍后重试。', code='source_unavailable', status=503):
        super().__init__(message)
        self.code, self.status = code, status


class TokenHubAuthRequired(TokenHubError):
    def __init__(self):
        super().__init__('TokenHub 登录已失效，请在本机重新连接。', 'auth_required', 401)


class TokenHubSession:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else DATA / 'tokenhub' / 'session.json'

    @staticmethod
    def validate(data):
        if not isinstance(data, dict):
            raise TokenHubAuthRequired()
        for key in ('access_token', 'refresh_token'):
            value = data.get(key)
            if not isinstance(value, str) or not value or len(value) > 16384 or any(c.isspace() for c in value):
                raise TokenHubAuthRequired()
        expiry = data.get('expires_at')
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
            raise TokenHubAuthRequired()
        return {key: data[key] for key in ('access_token', 'refresh_token', 'expires_at')}

    def load(self):
        try:
            return self.validate(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            raise TokenHubAuthRequired() from None

    def save(self, data):
        data = self.validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        fd, name = tempfile.mkstemp(prefix='.session-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def clear(self):
        self.path.unlink(missing_ok=True)


async def browser_login(timeout=300):
    """The user signs in interactively in an isolated, temporary browser context."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**browser_launch_options(headless=False))
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(ORIGIN + '/login?redirect=/dashboard', wait_until='domcontentloaded', timeout=45000)
            async def capture():
                while not page.is_closed():
                    try:
                        data = await page.evaluate('''(origin) => {
                            if (location.origin !== origin) return null;
                            const access = localStorage.getItem('auth_token');
                            const refresh = localStorage.getItem('refresh_token');
                            const expiry = Number(localStorage.getItem('token_expires_at'));
                            return access && refresh && expiry ? {access_token: access, refresh_token: refresh, expires_at: expiry / 1000} : null;
                        }''', ORIGIN)
                        if data:
                            data = TokenHubSession.validate(data)
                            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                                response = await client.get(API + '/auth/me', headers={'Authorization': 'Bearer ' + data['access_token']})
                            payload = response.json()
                            if response.status_code == 200 and payload.get('code') == 0 and isinstance(payload.get('data'), dict) and payload['data'].get('id') is not None:
                                return data
                    except Exception:
                        # Navigation changes the execution context. Never log page or token content.
                        pass
                    await asyncio.sleep(1)
                raise TokenHubError('登录窗口已关闭，请重新连接。', 'login_cancelled', 409)
            return await asyncio.wait_for(capture(), timeout)
        finally:
            await browser.close()
