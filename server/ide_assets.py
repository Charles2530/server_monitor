"""Bounded disk cache for immutable editor binaries, never workspace resources."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


MAX_ASSET = 32 * 1024 * 1024
MAX_CACHE = 128 * 1024 * 1024
MAX_ENTRIES = 512
STATIC_PATH = re.compile(r'^/stable-[a-f0-9]{40}/static/(?:out|node_modules)/[A-Za-z0-9_./@+-]+\.(?:js|css|wasm|woff2?|ttf|svg|png|jpg|ico)$')
SAVED_HEADERS = {'content-type', 'content-encoding', 'cache-control', 'etag', 'last-modified', 'vary'}


class EditorAssetCache:
    def __init__(self, directory):
        self.directory = Path(directory)

    def key(self, request):
        path = request.scope['raw_path'].decode('ascii')
        encoding = request.headers.get('accept-encoding', 'identity')
        if (request.method not in ('GET', 'HEAD') or request.url.query
                or request.headers.get('range') or request.headers.get('transfer-encoding')
                or request.headers.get('content-length', '0') != '0'
                or len(encoding) > 128 or not STATIC_PATH.fullmatch(path)
                or any(part in ('.', '..', '') for part in path[1:].split('/'))):
            return None
        return hashlib.sha256((path + '\n' + encoding).encode()).hexdigest()

    async def get(self, key):
        if key is None: return None
        return await asyncio.to_thread(self._read, key)

    def _read(self, key):
        try:
            path = self.directory / (key + '.asset')
            with path.open('rb') as source:
                metadata = json.loads(source.readline(16384))
                body = source.read(MAX_ASSET + 1)
            if len(body) > MAX_ASSET or hashlib.sha256(body).hexdigest() != metadata['sha256']: return None
            headers = metadata['headers']
            if not isinstance(headers, dict) or any(k not in SAVED_HEADERS or not isinstance(v, str) for k, v in headers.items()): return None
            os.utime(path, None)
            return body, {**headers, 'content-length': str(len(body))}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def stream(self, key, upstream):
        """Relay immediately; publish a cache entry only after a complete response."""
        control = upstream.headers.get('cache-control', '').lower()
        vary = {v.strip().lower() for v in upstream.headers.get('vary', '').split(',') if v.strip()}
        store = (key is not None and upstream.status_code == 200
                 and 'set-cookie' not in upstream.headers and 'content-range' not in upstream.headers
                 and not any(token in control for token in ('no-store', 'private', 'no-cache'))
                 and vary <= {'accept-encoding'})
        data = bytearray()
        try:
            async for chunk in upstream.aiter_raw():
                if store:
                    if len(data) + len(chunk) <= MAX_ASSET: data.extend(chunk)
                    else: store = False; data.clear()
                yield chunk
            if store and upstream.headers.get('content-length', str(len(data))) == str(len(data)):
                headers = {k: v for k, v in upstream.headers.items() if k in SAVED_HEADERS}
                await asyncio.to_thread(self._write, key, bytes(data), headers)
        finally:
            await upstream.aclose()

    def _write(self, key, body, headers):
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            metadata = json.dumps({'headers': headers, 'sha256': hashlib.sha256(body).hexdigest()}).encode() + b'\n'
            if len(metadata) > 16384: return
            # One file makes replacing and reading entries atomic across sessions.
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix='pending-', delete=False) as target:
                temporary = Path(target.name)
                target.write(metadata); target.write(body)
            temporary.replace(self.directory / (key + '.asset'))
            entries = sorted(((p.stat().st_mtime, p.stat().st_size, p) for p in self.directory.glob('*.asset')), reverse=True)
            size = 0
            for index, (_, length, path) in enumerate(entries):
                size += length
                if size > MAX_CACHE or index >= MAX_ENTRIES: path.unlink(missing_ok=True)
        except OSError:
            # A full/read-only cache disk must not make the editor unavailable.
            pass
        finally:
            if temporary is not None:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass
