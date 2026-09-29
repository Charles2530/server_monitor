import json
import os
import pytest
from server.browser import browser_launch_options
from playwright.async_api import async_playwright
from server import auth
from server.sites import SENSECORE


def test_credentials_require_all_three_fields_without_exposing_values(monkeypatch):
    for key,value in [('TENANT','example-org'),('USERNAME','example-user'),('PASSWORD','example-secret')]:
        monkeypatch.setenv('MONITOR_IAM_'+key,value)
    assert auth.load_credentials()=={'tenant':'example-org','username':'example-user','password':'example-secret'}
    monkeypatch.delenv('MONITOR_IAM_PASSWORD')
    with pytest.raises(auth.AuthRequired) as exc:auth.load_credentials()
    assert 'example-' not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted',[True,False])
@pytest.mark.parametrize('site',[None,SENSECORE])
async def test_cold_iam_login_fills_three_fields_and_captures_session(accepted,site):
    console=site.console if site else auth.CONSOLE
    signin=site.signin if site else auth.SIGNIN_ORIGIN
    services=site.services if site else auth.SERVICES
    credentials={'tenant':'dummy-org','username':'dummy-user','password':'dummy-secret'}
    submitted=[]
    html='''<meta charset="utf-8"><button id="iam" onclick="document.querySelector('form').hidden=false;this.hidden=true">IAM用户登录</button>
    <form hidden><input id="tenant_code"><input id="username"><input id="password" type="password">
    <button>登 录</button></form><script>
    document.querySelector('form').onsubmit=async e=>{e.preventDefault();
    await fetch('/login',{method:'POST',body:JSON.stringify({tenant:tenant_code.value,username:username.value,password:password.value})});
    await fetch('''+json.dumps(services['AEC2']+'/login-test')+''',{headers:{authorization:'dummy-token'}});};</script>'''
    async def route(r):
        if r.request.url==console+'/home':
            await r.fulfill(content_type='text/html',body='<script>location.href='+json.dumps(signin+'/')+'</script>')
        elif r.request.url.endswith('/login'):
            submitted.append(r.request.post_data_json);await r.fulfill(json={})
        elif r.request.url.endswith('/login-test'):
            await r.fulfill(status=200 if accepted else 403,json={},headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'authorization'})
        else:await r.fulfill(content_type='text/html',body=html)
    async with async_playwright() as p:
        browser=await p.chromium.launch(**browser_launch_options())
        try:
            context=await browser.new_context()
            await context.route('**/*',route)
            if accepted:
                result=await auth.authenticate(context,credentials,timeout=10,site=site)
                assert result['headers']=={'authorization':'dummy-token'}
                assert 'dummy-secret' not in json.dumps(result)
            else:
                with pytest.raises(auth.AuthRequired) as exc:
                    await auth.authenticate(context,credentials,timeout=3,site=site)
                assert 'dummy-secret' not in str(exc.value)
            assert submitted==[credentials]
        finally:await browser.close()


@pytest.mark.asyncio
async def test_existing_session_does_not_need_password(monkeypatch):
    def missing_credentials():raise AssertionError('Existing session must not request credentials')
    monkeypatch.setattr(auth,'load_credentials',missing_credentials)
    async with async_playwright() as p:
        browser=await p.chromium.launch(**browser_launch_options())
        try:
            context=await browser.new_context()
            async def route(r):
                if r.request.url==auth.CONSOLE+'/home':
                    body='<script>fetch('+json.dumps(auth.SERVICES['AEC2']+'/reuse')+',{headers:{authorization:"existing-token"}})</script>'
                    await r.fulfill(content_type='text/html',body=body)
                else:await r.fulfill(json={},headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'authorization'})
            await context.route('**/*',route)
            result=await auth.authenticate(context,timeout=5)
            assert result['headers']=={'authorization':'existing-token'}
        finally:await browser.close()


@pytest.mark.asyncio
async def test_login_does_not_fill_credentials_on_unexpected_origin():
    async with async_playwright() as p:
        browser=await p.chromium.launch(**browser_launch_options())
        try:
            context=await browser.new_context()
            async def route(r):
                if r.request.url==auth.CONSOLE+'/home':
                    await r.fulfill(content_type='text/html',body='<script>location.href="https://unexpected.example/"</script>')
                else:await r.fulfill(content_type='text/html',body='<input id="tenant_code"><input id="username"><input id="password">')
            await context.route('**/*',route)
            with pytest.raises(auth.AuthRequired):
                await auth.authenticate(context,{'tenant':'org','username':'user','password':'secret'},timeout=2)
            assert await context.pages[0].locator('#password').input_value()==''
        finally:await browser.close()


def test_session_save_is_private_and_atomic(tmp_path,monkeypatch):
    path=tmp_path/'session.json';monkeypatch.setattr(auth,'SESSION',path)
    auth.save_session({'headers':{'authorization':'dummy-token'},'storage_state':{}})
    assert json.loads(path.read_text())['headers']['authorization']=='dummy-token'
    assert os.stat(path).st_mode & 0o777==0o600
    assert not path.with_suffix('.tmp').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['iam','tenant'])
async def test_cross_origin_navigation_during_login_never_fills_new_document(stage):
    async with async_playwright() as p:
        browser=await p.chromium.launch(**browser_launch_options())
        try:
            context=await browser.new_context()
            fields='<input id="tenant_code"><input id="username"><input id="password"><button>登 录</button>'
            async def route(r):
                if r.request.url==auth.CONSOLE+'/home':
                    body='<script>location.href="https://signin.ms-sc-01.maoshanwangtech.com/"</script>'
                elif r.request.url.startswith(auth.SIGNIN_ORIGIN):
                    if stage=='iam':body='<button onclick="location.href=\'https://unexpected.example/\'">IAM用户登录</button>'
                    else:body=fields+'<script>tenant_code.oninput=()=>location.href="https://unexpected.example/"</script>'
                else:body=fields
                await r.fulfill(content_type='text/html',body='<meta charset="utf-8">'+body)
            await context.route('**/*',route)
            with pytest.raises(auth.AuthRequired):
                await auth.authenticate(context,{'tenant':'dummy-org','username':'dummy-user','password':'dummy-secret'},timeout=3)
            page=context.pages[0]
            await page.wait_for_url('https://unexpected.example/')
            assert await page.locator('#password').input_value()==''
            assert await page.locator('#username').input_value()==''
        finally:await browser.close()
