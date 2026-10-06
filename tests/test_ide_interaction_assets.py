"""Editing must reuse packaged grammars/icons without caching workspace content."""
from urllib.parse import urlencode
import httpx
import pytest
from server.ide_proxy import EditorProxy
from tests.test_ide_assets import Wire

PREFIX='/stable-'+'a'*40
BUILTIN='/root/.local/share/ilab-cci-editor/code-server-4.139.1-linux-amd64/lib/vscode/extensions'
APPEARANCE='/root/.local/share/ilab-cci-editor/extensions/ilab.workspace-appearance-1.0.0'
PRE=PREFIX+'/static/out/vs/workbench/contrib/webview/browser/pre/'

def resource(path):return PREFIX+'/vscode-remote-resource?'+urlencode({'path':path})

async def get(cache,transport,path,*,roots=None,authenticated=True):
 proxy=EditorProxy(12345,'remote=SECRET','http://localhost:8766',transport=transport,cache_dir=cache)
 proxy.port=23456
 proxy.assets.resource_roots=[BUILTIN,APPEARANCE] if roots is None else roots
 try:
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app),base_url=proxy.origin) as client:
   if authenticated:await client.get('/_ilab_open/'+proxy.ticket())
   return await client.get(path)
 finally:await proxy.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('path',[
 resource(BUILTIN+'/markdown-basics/syntaxes/markdown.tmLanguage.json'),
 resource(APPEARANCE+'/icons/folder-python.svg'),
 resource(BUILTIN+'/markdown-basics/syntaxes/markdown.tmLanguage.json')+'&tkn=',
 resource(APPEARANCE+'/icons/folder-python.svg')+'&tkn=',
 resource(BUILTIN+'/javascript/syntaxes/Regular Expressions (JavaScript).tmLanguage')+'&tkn=',
 PRE+'service-worker.js?v=4&remoteAuthority=localhost%3A12345&vscode-resource-base-authority=vscode-cdn.net',
 PRE+'index.html?id=one&parentId=p&origin=o&swVersion=4&extensionId=vscode.markdown-language-features&platform=browser&parentOrigin=http%3A%2F%2Flocalhost%3A12345&remoteAuthority=localhost%3A12345&vscode-resource-base-authority=vscode-cdn.net',
 PRE+'fake.html',
])
async def test_packaged_resources_reused_across_editor_origins(tmp_path,path):
 calls=[]
 def upstream(request):
  assert 'origin' not in request.headers
  calls.append(request)
  return httpx.Response(200,stream=Wire(b'packaged bytes'),headers={'cache-control':'public, max-age=31536000','vary':'Origin, Accept-Encoding','content-security-policy':"default-src 'none'; script-src 'self'; frame-ancestors 'self'"})
 transport=httpx.MockTransport(upstream)
 first=await get(tmp_path,transport,path)
 second=await get(tmp_path,transport,path.replace('id=one','id=two').replace('localhost%3A12345','localhost%3A34567'))
 assert first.content==second.content==b'packaged bytes'
 assert len(calls)==1
 assert "default-src 'none'" in second.headers['content-security-policy']
 assert "script-src 'self'" in second.headers['content-security-policy']
 assert (await get(tmp_path,transport,path,authenticated=False)).status_code==403
 assert len(calls)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('path',[
 resource('/mnt/project/extensions/markdown-basics/syntaxes/markdown.tmLanguage.json'),
 resource(BUILTIN+'/../private.json'),
 resource(BUILTIN+'-other/private.json'),
 resource(BUILTIN+'/%2e%2e/private.json'),
 resource(BUILTIN+'/markdown-basics/custom.html'),
 resource(BUILTIN+'/markdown-basics/syntaxes/markdown.tmLanguage.json')+'&path=/tmp/private.json',
 resource(BUILTIN+'/markdown-basics/syntaxes/markdown.tmLanguage.json')+'&token=secret',
 resource(BUILTIN+'/markdown-basics/syntaxes/markdown.tmLanguage.json')+'&tkn=secret',
 resource(BUILTIN+'/markdown-basics/syntaxes/markdown.tmLanguage.json')+'&tkn=&tkn=',
 PRE+'other.html',PRE+'index.html?secret=private',PRE+'service-worker.js?token=private',
])
async def test_user_files_ambiguous_queries_and_other_html_not_cached(tmp_path,path):
 calls=[]
 def upstream(request):
  calls.append(request);return httpx.Response(200,stream=Wire(b'dynamic'),headers={'cache-control':'public'})
 transport=httpx.MockTransport(upstream)
 await get(tmp_path,transport,path);await get(tmp_path,transport,path)
 assert len(calls)==2

@pytest.mark.asyncio
async def test_resource_roots_are_required_and_isolate_other_installations(tmp_path):
 calls=[]
 def upstream(request):
  calls.append(request);return httpx.Response(200,stream=Wire(b'packaged'),headers={'cache-control':'public'})
 transport=httpx.MockTransport(upstream);path=resource(BUILTIN+'/markdown-basics/syntaxes/markdown.tmLanguage.json')
 await get(tmp_path,transport,path)
 await get(tmp_path,transport,path,roots=['/home/other/.local/share/ilab-cci-editor/extensions'])
 assert len(calls)==2

@pytest.mark.asyncio
async def test_empty_resource_roots_never_reads_or_populates_cache(tmp_path):
 calls=[]
 def upstream(request):
  calls.append(request);return httpx.Response(200,stream=Wire(b'packaged'),headers={'cache-control':'public'})
 transport=httpx.MockTransport(upstream);path=resource(BUILTIN+'/markdown-basics/syntaxes/markdown.tmLanguage.json')
 await get(tmp_path,transport,path)
 await get(tmp_path,transport,path,roots=[])
 await get(tmp_path,transport,path,roots=[])
 assert len(calls)==3

@pytest.mark.asyncio
async def test_optional_install_refreshes_live_session_resource_roots(monkeypatch):
 import asyncio
 from types import SimpleNamespace
 import server.cci_ide as module
 from server.sites import MAOSHANWANG
 editor=module.CCIEditor(MAOSHANWANG,None,None)
 data={'task_id':'CCI:test','instance':'pod','container':'main'}
 identity=(*data.values(),'http://localhost:8766')
 assets=SimpleNamespace(resource_roots=[BUILTIN])
 session={'address':'10.0.0.1','proxy':SimpleNamespace(assets=assets),
          'appearance':{'version':module.APPEARANCE_VERSION,'installed':False}}
 editor.sessions[identity]=session
 async def bootstrap(*args,**kwargs):
  return {'appearance':{'version':module.APPEARANCE_VERSION,'installed':True},'asset_roots':[BUILTIN,APPEARANCE]}
 monkeypatch.setattr(module,'run_python',bootstrap)
 editor.prepare_optional(identity,session,data)
 await asyncio.gather(*editor.optional_jobs.values())
 assert session['appearance']['installed']
 assert assets.resource_roots==[BUILTIN,APPEARANCE]
