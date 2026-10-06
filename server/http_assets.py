"""Compress bundled assets without buffering API responses or terminal traffic."""
from starlette.middleware.gzip import GZipMiddleware
from starlette.datastructures import MutableHeaders


def accepts_gzip(headers):
    qualities = {}
    for key, value in headers:
        if key != b'accept-encoding':
            continue
        for entry in value.decode('latin-1').split(','):
            name, *params = entry.strip().lower().split(';')
            name = name.strip()
            if name not in ('gzip', '*'):
                continue
            quality = 1.0
            for param in params:
                key, _, value = param.strip().partition('=')
                if key == 'q':
                    try:
                        quality = float(value)
                    except ValueError:
                        quality = 0
            qualities[name] = quality
    return 0 < qualities.get('gzip', qualities.get('*', 0)) <= 1


class StaticAssetCompression:
    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=5)

    async def __call__(self, scope, receive, send):
        static = (scope['type'] == 'http' and scope['method'] in ('GET', 'HEAD')
                  and scope['path'].startswith('/static/'))
        compress = (static and scope['method'] == 'GET'
                    and not any(key == b'range' for key, _ in scope['headers'])
                    and accepts_gzip(scope['headers']))
        if compress:
            # Starlette checks a lowercase substring rather than negotiating q values.
            scope = {**scope, 'headers': [(key, value) for key, value in scope['headers']
                                         if key != b'accept-encoding'] + [(b'accept-encoding', b'gzip')]}

        async def asset_headers(message):
            if static and message['type'] == 'http.response.start' and message['status'] in (200, 304):
                headers = MutableHeaders(scope=message)
                if 'accept-encoding' not in headers.get('vary', '').lower():
                    headers.add_vary_header('Accept-Encoding')
                if 'etag' in headers and not headers['etag'].startswith('W/'):
                    headers['etag'] = 'W/' + headers['etag']
            await send(message)

        await (self.compressed if compress else self.app)(scope, receive, asset_headers)


def revalidatable_asset(path, method, status):
    return path.startswith('/static/') and method in ('GET', 'HEAD') and status in (200, 304)
