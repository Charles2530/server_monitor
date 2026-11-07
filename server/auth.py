"""Automatic IAM login in a dedicated local browser, using local configuration."""
import asyncio
import json
import logging
import os
import re
import time
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from .config import DATA, CONSOLE, SERVICES
from .browser import browser_launch_options

SESSION = DATA / 'session.json'
LOGIN_LOCKS = {}
SIGNIN_ORIGIN = 'https://signin.ms-sc-01.maoshanwangtech.com'
logger = logging.getLogger(__name__)
AUTH_TIMEOUT = 180
PAGE_NAVIGATION_TIMEOUT = 30000
PAGE_RETRY_INTERVAL = 15
SCRIPT_CHUNK_SIZE = 65536

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


async def authenticate(context, credentials=None, timeout=AUTH_TIMEOUT, site=None):
    allowed = {urlparse(url).netloc for url in (site.services if site else SERVICES).values()}
    headers = {}
    async def capture(response):
        url=urlparse(response.url)
        if response.status == 200 and url.scheme=='https' and url.netloc in allowed:
            value = await response.request.header_value('authorization')
            if value:headers['authorization'] = value
    context.on('response', capture)
    route_page=None
    route_handler=None
    asset_tasks={}
    async def run():
        nonlocal route_page, route_handler
        page=await context.new_page()
        route_page=page
        home=(site.console if site else CONSOLE)+'/home'
        asset_hosts={urlparse(site.console if site else CONSOLE).netloc,
                     urlparse(site.signin if site else SIGNIN_ORIGIN).netloc}
        async def curl_bytes(*args, timeout=20):
            process=await asyncio.create_subprocess_exec(
                '/usr/bin/curl','--noproxy','*','-skS','--http1.1',
                '--connect-timeout','5','--max-time',str(timeout),*args,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                output,error=await asyncio.wait_for(process.communicate(),timeout+2)
            except BaseException:
                process.kill()
                await process.communicate()
                raise
            if process.returncode:
                raise OSError(error.decode(errors='replace')[:120])
            return output

        async def download_script(url):
            head=await curl_bytes('-I',url)
            match=re.search(rb'(?im)^content-length:\s*(\d+)',head)
            if match is None:
                return await curl_bytes(url,timeout=30)
            total=int(match.group(1))
            if total <= SCRIPT_CHUNK_SIZE:
                return await curl_bytes(url,timeout=20)
            semaphore=asyncio.Semaphore(6)
            async def piece(start):
                end=min(total-1,start+SCRIPT_CHUNK_SIZE-1)
                expected=end-start+1
                async with semaphore:
                    for attempt in range(4):
                        try:
                            body=await curl_bytes('-r',f'{start}-{end}',url)
                            if len(body)==expected:
                                return start,body
                        except (OSError,asyncio.TimeoutError):
                            pass
                        await asyncio.sleep(.2*(attempt+1))
                raise OSError('script range download failed')
            parts=await asyncio.gather(*(piece(start) for start in range(0,total,SCRIPT_CHUNK_SIZE)))
            return b''.join(body for _,body in sorted(parts))

        async def local_script(url):
            task=asset_tasks.get(url)
            if task is None:
                task=asset_tasks[url]=asyncio.create_task(download_script(url))
            try:
                return await task
            finally:
                if task.done(): asset_tasks.pop(url,None)

        async def retry_script(route):
            if urlparse(route.request.url).netloc not in asset_hosts:
                try: await route.continue_()
                except Exception: pass
                return
            try:
                body=await local_script(route.request.url)
                await route.fulfill(status=200,headers={'content-type':'application/javascript'},body=body)
                return
            except Exception:
                pass
            try: await route.continue_()
            except Exception: pass
        route_handler=retry_script
        await page.route('**/*.js',route_handler)
        await page.goto(home,wait_until='commit',timeout=PAGE_NAVIGATION_TIMEOUT)
        submitted=False
        retry_at=time.monotonic()+PAGE_RETRY_INTERVAL
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
            if not submitted and not asset_tasks and time.monotonic() >= retry_at:
                # The console entry bundle can stall part-way through a download.
                # Retry the document while keeping the overall auth window alive.
                try:
                    await page.reload(wait_until='commit',timeout=PAGE_NAVIGATION_TIMEOUT)
                except Exception:
                    pass
                retry_at=time.monotonic()+PAGE_RETRY_INTERVAL
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
        if route_page and route_handler:
            try:
                await route_page.unroute_all(behavior='ignoreErrors')
            except Exception:
                pass
        pending=list(asset_tasks.values())
        for task in pending: task.cancel()
        if pending: await asyncio.gather(*pending,return_exceptions=True)
        context.remove_listener('response',capture)


async def connect_browser(headless=True, timeout=AUTH_TIMEOUT, site=None):
    timeout=max(timeout, AUTH_TIMEOUT)
    key=str(site.data if site else DATA)
    async with LOGIN_LOCKS.setdefault(key,asyncio.Lock()):
        await _connect_browser(headless,timeout,site)


async def _connect_browser(headless=True, timeout=AUTH_TIMEOUT, site=None):
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
