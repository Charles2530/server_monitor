"""A new editor origin must not download the same immutable bundle again."""
import gzip

import httpx
import pytest

from server.ide_proxy import EditorProxy


ASSET = '/stable-' + 'a' * 40 + '/static/out/vs/code/browser/workbench/workbench.js'


class Wire(httpx.AsyncByteStream):
    def __init__(self, body, broken=False): self.body, self.broken = body, broken
    async def __aiter__(self):
        yield self.body
        if self.broken: raise httpx.ReadError('interrupted')


async def fetch(cache_dir, transport, path=ASSET, *, authenticated=True, encoding='gzip', method='GET', port=23456):
    proxy = EditorProxy(12345, 'remote=SECRET', 'http://localhost:8766',
                        transport=transport, cache_dir=cache_dir)
    proxy.port = port
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url=proxy.origin) as client:
            if authenticated: await client.get('/_ilab_open/' + proxy.ticket())
            return await client.request(method, path, headers={'accept-encoding': encoding})
    finally: await proxy.close()


@pytest.mark.asyncio
async def test_new_proxy_and_port_reuse_compressed_bundle_from_disk(tmp_path):
    calls = []
    body = b'editor bundle' * 100
    encoded = gzip.compress(body)
    def remote(request):
        calls.append(request)
        return httpx.Response(200, stream=Wire(encoded), headers={
            'content-encoding': 'gzip', 'content-length': str(len(encoded)),
            'content-type': 'application/javascript', 'cache-control': 'public, max-age=31536000',
            'vary': 'Accept-Encoding', 'etag': '"bundle"'})
    transport = httpx.MockTransport(remote)
    first = await fetch(tmp_path, transport)
    second = await fetch(tmp_path, transport, port=34567)
    assert first.content == second.content == body
    assert len(calls) == 1
    assert second.headers['content-encoding'] == 'gzip'
    assert second.headers['content-length'] == str(len(encoded))
    assert 'SECRET' not in ''.join(p.read_bytes().decode(errors='replace') for p in tmp_path.rglob('*') if p.is_file())
    assert (await fetch(tmp_path, transport, authenticated=False)).status_code == 403
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('path', [
    '/', '/proxy/8080/file.js', '/_static/out/browser/serviceWorker.js',
    '/stable-' + 'a' * 40 + '/vscode-remote-resource?path=/home/private.js',
    ASSET + '?secret=user', ASSET.replace('.js', '.html'),
    ASSET.replace('workbench.js', '%2e%2e%2fprivate.js'),
])
async def test_dynamic_or_ambiguous_paths_are_never_cached(tmp_path, path):
    calls = []
    def remote(request):
        calls.append(request)
        return httpx.Response(200, stream=Wire(str(len(calls)).encode()), headers={'cache-control': 'public'})
    transport = httpx.MockTransport(remote)
    await fetch(tmp_path, transport, path)
    await fetch(tmp_path, transport, path)
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [
    {'cache-control': 'no-store'}, {'cache-control': 'private'},
    {'set-cookie': 'secret=private'}, {'vary': 'Cookie'},
    {'content-length': '999'},
])
async def test_private_or_incomplete_responses_are_not_stored(tmp_path, headers):
    calls = []
    def remote(request):
        calls.append(request)
        return httpx.Response(200, stream=Wire(b'bundle'), headers=headers)
    transport = httpx.MockTransport(remote)
    await fetch(tmp_path, transport)
    await fetch(tmp_path, transport)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_encoding_variants_and_sites_do_not_mix(tmp_path):
    calls = []
    def remote(request):
        calls.append(request)
        return httpx.Response(200, stream=Wire(b'bundle'), headers={'content-type': 'application/javascript'})
    transport = httpx.MockTransport(remote)
    await fetch(tmp_path / 'site-a', transport, encoding='gzip')
    await fetch(tmp_path / 'site-a', transport, encoding='identity')
    await fetch(tmp_path / 'site-b', transport, encoding='gzip')
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_cache_disk_failure_does_not_break_editor(tmp_path):
    blocked = tmp_path / 'file'; blocked.write_text('not a directory')
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Wire(b'bundle')))
    assert (await fetch(blocked / 'cache', transport)).content == b'bundle'


@pytest.mark.asyncio
async def test_interrupted_download_is_not_published(tmp_path):
    from server.ide_assets import EditorAssetCache
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Wire(b'partial', broken=True)))
    cache = EditorAssetCache(tmp_path)
    async with httpx.AsyncClient(transport=transport) as client:
        async with client.stream('GET', 'http://editor.test/resource') as upstream:
            with pytest.raises(httpx.ReadError):
                _ = [chunk async for chunk in cache.stream('a' * 64, upstream)]
    assert not list(tmp_path.rglob('*.asset'))


@pytest.mark.asyncio
async def test_corrupt_cache_refetches_and_head_keeps_size(tmp_path):
    calls = []
    def remote(request):
        calls.append(request)
        return httpx.Response(200, stream=Wire(b'bundle'), headers={'etag': '"v1"'})
    transport = httpx.MockTransport(remote)
    await fetch(tmp_path, transport)
    asset = next(tmp_path.glob('*.asset'))
    asset.write_bytes(asset.read_bytes()[:-1] + b'X')
    assert (await fetch(tmp_path, transport)).content == b'bundle'
    head = await fetch(tmp_path, transport, method='HEAD')
    assert head.content == b'' and head.headers['content-length'] == '6'
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_oversized_response_is_streamed_without_caching(tmp_path, monkeypatch):
    monkeypatch.setattr('server.ide_assets.MAX_ASSET', 4)
    calls = []
    def remote(request):
        calls.append(request)
        return httpx.Response(200, stream=Wire(b'large-bundle'))
    transport = httpx.MockTransport(remote)
    assert (await fetch(tmp_path, transport)).content == b'large-bundle'
    assert (await fetch(tmp_path, transport)).content == b'large-bundle'
    assert len(calls) == 2 and not list(tmp_path.glob('*.asset'))


@pytest.mark.asyncio
async def test_cache_quota_evicts_old_entries(tmp_path, monkeypatch):
    monkeypatch.setattr('server.ide_assets.MAX_ENTRIES', 1)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Wire(b'bundle')))
    await fetch(tmp_path, transport)
    await fetch(tmp_path, transport, ASSET.replace('workbench.js', 'other.js'))
    assert len(list(tmp_path.glob('*.asset'))) == 1
