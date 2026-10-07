"""Appearance stays local, declarative and subordinate to editor user settings."""
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
import pytest


def test_bundle_contains_icons_and_defaults_without_executable_code():
    from server.ide_appearance import appearance_bundle
    bundle = appearance_bundle()
    assert bundle == appearance_bundle()
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(bundle['payload']))) as archive:
        manifest = json.loads(archive.read('extension/package.json'))
        assert not any(key in manifest for key in ('main', 'browser', 'activationEvents'))
        defaults = manifest['contributes']['configurationDefaults']
        assert defaults['editor.fontFamily'].startswith("'iLab JetBrains Mono'")
        assert defaults['workbench.iconTheme'] == 'ilab-material-icons'
        icons = json.loads(archive.read('extension/icons.json'))
        for key in ('py', 'ipynb', 'cu', 'safetensors'):
            definition = icons['iconDefinitions'][icons['fileExtensions'][key]]
            assert 'extension/' + definition['iconPath'].removeprefix('./') in archive.namelist()
        assert icons['folderNames']['datasets'] != icons['folderNames']['configs']
        assert 'extension/LICENSE' in archive.namelist()


@pytest.mark.parametrize('obsolete', [None, '', '  \n', '{}'])
def test_install_is_idempotent_and_never_rewrites_jsonc_settings(tmp_path, monkeypatch, obsolete):
    from server.ide_appearance import appearance_bundle, prepare_appearance
    bundle = appearance_bundle()
    user = tmp_path / 'user-data/User/settings.json'
    user.parent.mkdir(parents=True)
    original = '// personal settings\n{"editor.fontSize": 19, "workbench.iconTheme": "seti",}\n'
    user.write_text(original)
    if obsolete is not None:
        (tmp_path / 'extensions').mkdir()
        (tmp_path / 'extensions/.obsolete').write_text(obsolete)
    calls = []
    def install(command, **kwargs):
        calls.append(command)
        with zipfile.ZipFile(command[command.index('--install-extension') + 1]) as archive:
            for member in archive.namelist():
                if member.startswith('extension/') and not member.endswith('/'):
                    target = tmp_path / ('extensions/ilab.workspace-appearance-' + bundle['version']) / member[len('extension/'):]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('subprocess.run', install)
    first = prepare_appearance(tmp_path, {}, Path('/fake/editor'), bundle)
    second = prepare_appearance(tmp_path, {}, Path('/fake/editor'), bundle)
    assert first == second == {'installed': True, 'version': bundle['version']}
    assert len(calls) == 1
    assert user.read_text() == original
    assert not list(tmp_path.glob('appearance-install-*'))


def test_optional_install_failure_does_not_block_editor(tmp_path, monkeypatch):
    from server.ide_appearance import appearance_bundle, prepare_appearance
    monkeypatch.setattr('subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=1))
    result = prepare_appearance(tmp_path, {}, Path('/fake/editor'), appearance_bundle())
    assert result['installed'] is False
    assert not (tmp_path / 'appearance.json').exists()


@pytest.mark.asyncio
async def test_fonts_require_editor_cookie_and_only_workbench_html_is_decorated():
    from server.ide_proxy import EditorProxy
    calls = []
    html = '<html><head><meta id="vscode-workbench-web-configuration" data-settings="{}"></head><body>Editor</body></html>'
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self): yield html.encode()
    async def upstream(request):
        calls.append(request.url.path)
        if request.url.path == '/': assert request.headers['accept-encoding'] == 'gzip'
        return httpx.Response(200, stream=Stream(), headers={'content-type': 'text/html', 'etag': 'original'})
    proxy = EditorProxy(12345, 'secret=x', 'http://localhost:8766', transport=httpx.MockTransport(upstream))
    proxy.port = 23456
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as client:
            assert (await client.get('/_ilab_appearance/fonts.css')).status_code == 403
            await client.get('/_ilab_open/' + proxy.ticket())
            response = await client.get('/')
            assert '/_ilab_appearance/fonts.css' in response.text
            assert 'etag' not in response.headers
            assert len(response.content) == int(response.headers['content-length'])
            response = await client.get('/_ilab_appearance/fonts.css')
            assert response.status_code == 200 and '@font-face' in response.text
            response = await client.get('/_ilab_appearance/fonts/JetBrainsMono-Regular.woff2')
            assert response.content.startswith(b'wOF2') and response.headers['content-type'] == 'font/woff2'
            assert (await client.head('/_ilab_appearance/fonts.css')).content == b''
            assert (await client.post('/_ilab_appearance/fonts.css')).status_code == 405
            assert (await client.get('/_ilab_appearance/package.json')).status_code == 404
            assert calls == ['/']
            response = await client.get('/webview/index.html')
            assert '/_ilab_appearance/fonts.css' not in response.text
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_workbench_decodes_gzip_and_preserves_unrelated_pages():
    import gzip
    from server.ide_proxy import workbench_response
    html = b'<html><head><meta id="vscode-workbench-web-configuration"></head><body>editor</body></html>'
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self): yield gzip.compress(html)
    response = httpx.Response(200, stream=Stream(), headers={'content-encoding': 'gzip'})
    decorated = await workbench_response(response, {'content-encoding': 'gzip', 'content-length': '12', 'content-type': 'text/html', 'etag': 'old'}, 'http://localhost:8766')
    assert b'/_ilab_appearance/fonts.css' in decorated.body
    assert 'content-encoding' not in decorated.headers and 'etag' not in decorated.headers
    assert response.is_closed
    class Other(httpx.AsyncByteStream):
        async def __aiter__(self): yield b'<html><head></head><body>other app</body></html>'
    response = httpx.Response(200, stream=Other())
    untouched = await workbench_response(response, {'content-type': 'text/html'}, 'http://localhost:8766')
    assert untouched.body == b'<html><head></head><body>other app</body></html>'


def test_context_menus_add_explorer_files_and_folders_without_duplicating_native_menus():
    from server.ide_appearance import appearance_bundle
    bundle = appearance_bundle()
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(bundle['payload']))) as archive:
        manifest = json.loads(archive.read('extension/package.json'))
        assert manifest['version'] == bundle['version']
        assert f'Version="{bundle["version"]}"' in archive.read('extension.vsixmanifest').decode()
        menus = manifest['contributes'].get('menus', {})
        explorer = menus.get('explorer/context', [])
        assert any(item['command'] == 'chatgpt.addFileToThread'
                   and 'file' in item['when'] and 'vscode-remote' in item['when']
                   and 'explorerResourceIsFolder' not in item['when'] for item in explorer)
        # code-server exposes Codex's native editor/selection and tab menus.
        # Adding remote-scheme copies produces duplicate actions in the browser.
        assert 'editor/context' not in menus
        assert 'editor/title/context' not in menus
