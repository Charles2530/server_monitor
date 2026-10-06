"""Automatic IAM login in a dedicated local browser, using local configuration."""
import asyncio
import json
import logging
import os
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from .config import DATA, CONSOLE, SERVICES
from .browser import browser_launch_options

SESSION = DATA / 'session.json'
LOGIN_LOCKS = {}
SIGNIN_ORIGIN = 'https://signin.ms-sc-01.maoshanwangtech.com'
logger = logging.getLogger(__name__)

class AuthRequired(Exception):
    pass


def load_session(site=None):
    try:
        session = json.loads((site.data/"session.json" if site else SESSION).read_text())
        token = session.get('headers', {}).get('authorization')
        if token:
            return session
    except (OSError, ValueError):
        pass
    raise AuthRequired('云会话不可用，需要自动登录。')


def load_credentials(site=None):
    values={key:os.environ.get((site.credentials_prefix if site else 'MONITOR_IAM_')+key.upper(),'') for key in ('tenant','username','password')}
    if not all(values.values()):
        raise AuthRequired('本地 IAM 登录配置不完整，请检查项目 .env。')
    return values


def save_session(session, site=None):
    path = site.data/"session.json" if site else SESSION
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd,0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(session, f)
    os.replace(temp, path)


async def submit_iam_login(page,values,site=None):
    # Bind all inputs to one verified document: navigation invalidates handles.
    root=await page.locator('html').element_handle()
    if root is None or await root.evaluate('(el)=>el.ownerDocument.location.origin')!=(site.signin if site else SIGNIN_ORIGIN):
        raise AuthRequired('登录页面地址发生变化，已停止填写凭据。')
    fields=[await root.query_selector(selector) for selector in ('#tenant_code','#username','#password')]
    buttons=await root.query_selector_all('button')
    submit=None
    for button in buttons:
        if await button.is_visible() and ''.join((await button.inner_text()).split())=='登录':
            submit=button;break
    if not all(fields) or submit is None:
        raise AuthRequired('IAM 登录表单尚未就绪。')
    for field,key in zip(fields,('tenant','username','password')):
        await field.fill(values[key],timeout=10000)
    await submit.click(timeout=10000)


async def authenticate(context, credentials=None, timeout=75, site=None):
    allowed = {urlparse(url).netloc for url in (site.services if site else SERVICES).values()}
    headers = {}
    async def capture(response):
        url=urlparse(response.url)
        if response.status == 200 and url.scheme=='https' and url.netloc in allowed:
            value = await response.request.header_value('authorization')
            if value:headers['authorization'] = value
    context.on('response', capture)
    async def run():
        page=await context.new_page()
        await page.goto((site.console if site else CONSOLE)+'/home',wait_until='domcontentloaded',timeout=60000)
        submitted=False
        while not headers:
            if page.is_closed():raise AuthRequired('自动登录窗口意外关闭。')
            parsed=urlparse(page.url)
            if parsed.scheme+'://'+parsed.netloc==(site.signin if site else SIGNIN_ORIGIN) and not submitted:
                iam=page.get_by_text('IAM用户登录',exact=True)
                if not await page.locator('#tenant_code').is_visible() and await iam.is_visible():
                    await iam.click(timeout=10000)
                if await page.locator('#tenant_code').is_visible():
                    values=credentials or (load_credentials(site) if site else load_credentials())
                    await submit_iam_login(page,values,site)
                    submitted=True
            await asyncio.sleep(.5)
        return {'storage_state':await context.storage_state(),'headers':headers}
    try:
        return await asyncio.wait_for(run(),timeout=timeout)
    except AuthRequired:raise
    except Exception as error:
        reason = 'timeout' if isinstance(error, asyncio.TimeoutError) else type(error).__name__
        logger.warning('monitor_failure site=%s stage=authenticate reason=%s', site.id if site else 'default', reason)
        raise AuthRequired('自动 IAM 登录未完成，请检查本地凭据、网络或平台验证要求。') from None
    finally:
        context.remove_listener('response',capture)


async def connect_browser(headless=True, timeout=75, site=None):
    key=str(site.data if site else DATA)
    async with LOGIN_LOCKS.setdefault(key,asyncio.Lock()):
        await _connect_browser(headless,timeout,site)


async def _connect_browser(headless=True, timeout=75, site=None):
    try:
        previous = load_session(site).get('storage_state')
    except AuthRequired:
        previous = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(**browser_launch_options(headless=headless))
        try:
            context = await browser.new_context(storage_state=previous)
            save_session(await authenticate(context,timeout=timeout,site=site),site)
        finally:
            await browser.close()
