"""Local API inventory. Imported credentials stay in initial; probes never follow redirects."""
import asyncio
import hashlib
import json
import math
import os
import random
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import tomli

from .config import DATA
from .normalize import iso
from .tokenhub_auth import TokenHubError


SUITE = 'reasoning-v2 · 3 类 × 2 轮'


def candy_minimum(counts):
    """Minimum fixed shape quotas; each rectangle is a maximal nonmatching draw."""
    a, p, w, sa, sp, sw = counts
    bounds = ((a + w, sa + sw), (p + w, sp + sw), (a + p + w, sw), (w, sa + sp + sw))
    return min(r + s for r in range(a + p + w + 1) for s in range(sa + sp + sw + 1)
               if all(r > br or s > bs for br, bs in bounds))


def quality_cases(rng=None):
    # Candy source: https://github.com/haowang02/codex-candy-eval
    # Instruction cases use verifiable constraints inspired by IFEval, not its official benchmark.
    rng = rng or random.SystemRandom()
    cases = []
    for repeat in (1, 2):
        counts = (7, 9, 8, 7, 6, 4)
        if repeat == 2:
            counts = tuple(value + rng.randint(1, 4) for value in counts)
        prompt = ('不使用外部工具。黑袋里有苹果、桃子、西瓜三种口味的糖果，各有圆形和五角星形。'
                  f'圆形数量依次为 {counts[:3]}，五角星形数量依次为 {counts[3:]}。'
                  '可用手感辨别形状，不能辨别口味；允许按形状挑选。事先确定取 r 个圆形、s 个五角星形，'
                  '每种形状内的口味由最坏情况决定。最少取多少个才能保证有圆形苹果配五角星桃子，'
                  '或圆形桃子配五角星苹果？求最小 r+s。只返回 JSON 对象，唯一键 minimum，值为整数。')
        cases.append(('糖果推理', repeat, prompt, {'minimum': candy_minimum(counts)}))
        values = rng.sample(range(1, 20), 6)
        steps = rng.randint(4, 7)
        a, output = values[:], []
        for i in range(steps):
            a = a[1:] + a[:1]
            a[i % len(a)] += i
            output.append(sum(a[::2]) - sum(a[1::2]))
        code = (f'a={values}; out=[]\nfor i in range({steps}):\n'
                ' a=a[1:]+a[:1]\n a[i%len(a)]+=i\n out.append(sum(a[::2])-sum(a[1::2]))\nprint(out)')
        cases.append(('代码推演', repeat, '不运行代码，推演下列 Python 的输出。只返回 JSON 整数数组，不加代码围栏。\n' + code, output))
        words = rng.sample(['amber', 'birch', 'cedar', 'daisy', 'elm', 'fern', 'grove', 'hazel'], 4)
        words += [words[0], words[1]]
        rng.shuffle(words)
        excluded = words[0]
        kept = sorted(set(words) - {excluded}, reverse=True)
        prompt = (f'输入单词列表 {json.dumps(words)}。排除所有 {excluded}，剩余单词去重后按字母降序排列。'
                  '只返回 JSON 对象，恰好有 items、count、status 三个键；items 为结果数组，count 为结果数量整数，'
                  'status 固定为小写 ok。不添加解释或代码围栏。')
        cases.append(('指令遵循', repeat, prompt, {'items': kept, 'count': len(kept), 'status': 'ok'}))
    return cases
STATUS = {'available': '推理请求成功', 'auth_error': '密钥无效或无权限', 'rate_limited': '限流或额度不足',
          'model_error': '模型或接口不匹配', 'redirect': '来源返回重定向，未转发密钥',
          'upstream_error': '上游服务异常', 'network_error': '网络或 TLS 连接失败',
          'tls_error': 'HTTPS 证书校验失败，请检查来源证书是否过期', 'timeout': '请求超时，当前可用性无法确认',
          'incomplete': '输出预算耗尽或响应未完成', 'invalid_response': '响应格式异常或没有文本',
          'needs_model': '请先发现模型或填写模型名称'}


def valid_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
        raise TokenHubError('请填写 HTTPS Base URL（本机服务可使用 HTTP）。', 'invalid_url', 422)
    return value.rstrip('/')


class ApiSources:
    def __init__(self, initial=None, path=None, transport=None):
        self.initial = Path(initial or os.environ.get('MONITOR_INITIAL_DIR', str(Path.home() / 'initial')))
        self.path = Path(path) if path else DATA / 'tokenhub' / 'api-sources.json'
        self.transport = transport
        self.busy = set()
        self.limit = asyncio.Semaphore(2)

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or not isinstance(data.get('custom'), list) or not isinstance(data.get('results'), dict):
                raise ValueError()
            return data
        except FileNotFoundError:
            return {'custom': [], 'results': {}}
        except (OSError, ValueError):
            raise TokenHubError('本机来源数据无法读取，请检查数据文件。', 'invalid_source_data') from None

    def write(self, data):
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.api-sources-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def inventory(self):
        rows, errors = [], []
        for path in sorted((self.initial / 'codex').glob('config.toml.*')):
            name = path.name.removeprefix('config.toml.')
            if name == 'codex':
                continue
            try:
                config = tomli.loads(path.read_text())
                provider = config.get('model_providers', {}).get(config.get('model_provider'), {})
                auth = json.loads((path.parent / ('auth.' + name + '.json')).read_text())
                if not provider.get('base_url'):
                    continue
                rows.append({'id': 'codex-' + name, 'name': name, 'client': 'Codex', 'origin': 'initial',
                             'file': str(path.relative_to(self.initial)), 'base_url': valid_url(provider['base_url']),
                             'key': auth.get('OPENAI_API_KEY') or '', 'protocol': provider.get('wire_api', 'responses'),
                             'model': config.get('model', ''), 'effort': config.get('model_reasoning_effort', '')})
            except (OSError, ValueError, TypeError, AttributeError, TokenHubError):
                errors.append('Codex / ' + name + ' 配置或凭据读取失败')
        for path in sorted((self.initial / 'claude').glob('settings.*.json')):
            name = path.name[len('settings.'):-len('.json')]
            try:
                config = json.loads(path.read_text())
                env = config.get('env', {})
                if not env.get('ANTHROPIC_BASE_URL'):
                    continue
                rows.append({'id': 'claude-' + name, 'name': name, 'client': 'Claude Code', 'origin': 'initial',
                             'file': str(path.relative_to(self.initial)), 'base_url': valid_url(env['ANTHROPIC_BASE_URL']),
                             'key': env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY') or '',
                             'protocol': 'anthropic', 'model': env.get('ANTHROPIC_MODEL') or config.get('model') or '', 'effort': ''})
            except (OSError, ValueError, TypeError, AttributeError, TokenHubError):
                errors.append('Claude Code / ' + name + ' 配置或凭据读取失败')
        rows.extend(self.load()['custom'])
        for row in rows:
            row['fingerprint'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:24]
        return rows, errors

    def find(self, source_id, fingerprint=None):
        row = next((row for row in self.inventory()[0] if row['id'] == source_id), None)
        if row is None:
            raise TokenHubError('来源不存在，请刷新列表。', 'source_missing', 404)
        if fingerprint is not None and row['fingerprint'] != fingerprint:
            raise TokenHubError('来源配置已变化，请刷新后重试。', 'source_changed', 409)
        return row

    def snapshot(self):
        rows, errors = self.inventory()
        results = self.load()['results']
        items = []
        for row in rows:
            key = row['key']
            result = results.get(row['fingerprint'], {})
            items.append({**{k: v for k, v in row.items() if k != 'key'},
                          'key_hint': ('••••' + key[-4:]) if len(key) > 8 else '••••' if key else '未配置',
                          'has_key': bool(key), 'busy': row['id'] in self.busy,
                          'availability': result.get('availability'), 'quality': result.get('quality'),
                          'models_status': result.get('models_status'),
                          'models': result.get('models', []), 'probe_model': result.get('probe_model', row['model']),
                          'history': result.get('history', [])})
        if not self.initial.is_dir():
            errors.append('未找到 initial 目录，可设置 MONITOR_INITIAL_DIR 或添加自定义来源。')
        return {'items': items, 'errors': errors, 'initial_dir': str(self.initial), 'suite': SUITE}

    def copy_key(self, source_id, fingerprint):
        row = self.find(source_id, fingerprint)
        if not row['key']:
            raise TokenHubError('该来源尚未配置 API key。', 'key_missing', 409)
        return {'key': row['key']}

    def save_source(self, *, name, base_url, key, protocol, model, effort='', source_id=None):
        data = self.load()
        old = self.find(source_id) if source_id else None
        if old and old['origin'] != 'custom':
            raise TokenHubError('initial 配置只读，请在源文件中修改。', 'source_readonly', 409)
        key = key or (old['key'] if old else '')
        if not name.strip() or not key or any(c.isspace() for c in key) or protocol not in ('responses', 'chat', 'anthropic'):
            raise TokenHubError('请填写来源名称、有效密钥和协议。', 'invalid_source', 422)
        if effort not in ('', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'):
            raise TokenHubError('推理档位无效。', 'invalid_effort', 422)
        row = {'id': source_id or 'custom-' + uuid.uuid4().hex[:12], 'name': name.strip(), 'base_url': valid_url(base_url.strip()),
               'key': key, 'protocol': protocol, 'model': model.strip(), 'effort': effort, 'client': '自定义', 'origin': 'custom', 'file': '本机配置'}
        data['custom'] = [item for item in data['custom'] if item['id'] != row['id']] + [row]
        self.write(data)
        return {'id': row['id']}

    def delete_source(self, source_id):
        row = self.find(source_id)
        if row['origin'] != 'custom':
            raise TokenHubError('initial 配置只读。', 'source_readonly', 409)
        data = self.load()
        data['custom'] = [item for item in data['custom'] if item['id'] != source_id]
        data['results'].pop(row['fingerprint'], None)
        self.write(data)
        return {'deleted': True}

    async def probe(self, client, row, model, prompt=None):
        base = row['base_url'].rstrip('/')
        anthropic = row['protocol'] == 'anthropic'
        headers = {'Authorization': 'Bearer ' + row['key']}
        if anthropic:
            headers.update({'x-api-key': row['key'], 'anthropic-version': '2023-06-01'})
        if prompt is None:
            endpoint, body = 'models', None
        elif anthropic:
            endpoint = 'messages'
            body = {'model': model, 'max_tokens': 4096, 'messages': [{'role': 'user', 'content': prompt}]}
        elif row['protocol'] == 'chat':
            endpoint = 'chat/completions'
            body = {'model': model, 'max_completion_tokens': 4096, 'messages': [{'role': 'user', 'content': prompt}]}
            if row['effort']:
                body['reasoning_effort'] = row['effort']
        else:
            endpoint = 'responses'
            body = {'model': model, 'input': prompt, 'max_output_tokens': 4096, 'store': False}
            if row['effort']:
                body['reasoning'] = {'effort': row['effort']}
        # Codex URLs are complete API roots. Anthropic SDK adds /v1 itself.
        if anthropic and not base.endswith('/v1'):
            base += '/v1'
        started = time.monotonic()
        result = {'checked_at': iso(time.time()), 'model': model, 'status': 'network_error'}
        try:
            response = await client.request('GET' if body is None else 'POST', base + '/' + endpoint, headers=headers, json=body)
            result.update({'latency_ms': round((time.monotonic() - started) * 1000), 'http_status': response.status_code})
            code = response.status_code
            if code >= 300:
                result['status'] = ('redirect' if code < 400 else 'auth_error' if code in (401, 403) else 'rate_limited' if code in (402, 429) else 'model_error' if code in (400, 404, 405, 422) else 'upstream_error')
                return result, None
            data = response.json()
            if body is None:
                models = [item['id'] for item in data['data'] if isinstance(item, dict) and isinstance(item.get('id'), str)]
                return {**result, 'status': 'available'}, models[:300]
            if anthropic:
                text = ''.join(item.get('text', '') for item in data.get('content', []) if item.get('type') == 'text')
                complete = data.get('stop_reason') in ('end_turn', 'stop_sequence')
            elif row['protocol'] == 'chat':
                choice = data.get('choices', [{}])[0]
                text = choice.get('message', {}).get('content') or ''
                complete = choice.get('finish_reason') == 'stop'
            else:
                text = ''.join(part.get('text', '') for item in data.get('output', []) if item.get('type') == 'message' for part in item.get('content', []) if part.get('type') == 'output_text')
                complete = data.get('status') == 'completed'
            result['status'] = 'available' if complete and isinstance(text, str) and text.strip() else 'incomplete' if not complete else 'invalid_response'
            if isinstance(data.get('model'), str):
                result['reported_model'] = data['model'].replace(row['key'], '[redacted]')[:200]
            usage = data.get('usage') or {}
            details = usage.get('output_tokens_details') or usage.get('completion_tokens_details') or {}
            for name, value in (('output_tokens', usage.get('output_tokens', usage.get('completion_tokens'))), ('reasoning_tokens', details.get('reasoning_tokens'))):
                result[name] = value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
            return result, text
        except httpx.TimeoutException:
            return {**result, 'status': 'timeout'}, None
        except httpx.ConnectError as exc:
            return {**result, 'status': 'tls_error' if 'CERTIFICATE_VERIFY_FAILED' in str(exc) else 'network_error'}, None
        except httpx.HTTPError:
            return result, None
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            return {**result, 'status': 'invalid_response'}, None

    async def check(self, source_id, fingerprint, *, mode, model=''):
        if source_id in self.busy or self.limit.locked():
            raise TokenHubError('检测正在进行，请稍后重试（最多同时检测两个来源）。', 'source_busy', 409)
        row = self.find(source_id, fingerprint)
        if not row['key']:
            raise TokenHubError('请先配置 API key。', 'key_missing', 409)
        model = model.strip() or row['model']
        if mode != 'models' and not model:
            raise TokenHubError(STATUS['needs_model'], 'needs_model', 422)
        self.busy.add(source_id)
        try:
            async with self.limit, httpx.AsyncClient(timeout=40, follow_redirects=False, transport=self.transport) as client:
                previous = self.load()['results'].get(fingerprint, {})
                result = {**previous, 'probe_model': model}
                if mode == 'models':
                    probe, models = await self.probe(client, row, model)
                    result['models_status'] = {**probe, 'message': STATUS[probe['status']]}
                    if models is not None:
                        result['models'] = models
                elif mode == 'availability':
                    probe, text = await self.probe(client, row, model, 'Reply with exactly OK.')
                    result['availability'] = {**probe, 'message': STATUS[probe['status']]}
                    if previous.get('probe_model') != model:
                        result['quality'] = None
                else:
                    outcomes = []
                    deadline = time.monotonic() + 70
                    for label, repeat, prompt, expected in quality_cases():
                        try:
                            probe, text = await asyncio.wait_for(self.probe(client, row, model, prompt), timeout=max(.1, deadline - time.monotonic()))
                        except asyncio.TimeoutError:
                            probe, text = {'status': 'timeout', 'checked_at': iso(time.time()), 'model': model}, None
                        passed = None
                        if probe['status'] == 'available':
                            try:
                                answer = json.loads(text.strip())
                                # JSON comparison also rejects Python's True == 1 and 1.0 == 1.
                                passed = json.dumps(answer, sort_keys=True) == json.dumps(expected, sort_keys=True)
                            except (ValueError, AttributeError):
                                passed = False
                        outcomes.append({'name': label, 'round': repeat, 'passed': passed, 'status': probe['status']})
                        if probe['status'] != 'available':
                            break
                    scored = sum(item['passed'] is not None for item in outcomes)
                    passed = sum(item['passed'] is True for item in outcomes)
                    result['availability'] = {**probe, 'message': STATUS[probe['status']]}
                    result['quality'] = {'suite': SUITE, 'checked_at': iso(time.time()), 'model': model, 'effort': row['effort'],
                                         'protocol': row['protocol'], 'output_budget': 4096,
                                         'passed': passed, 'scored': scored, 'planned': 6,
                                         'verdict': 'incomplete' if scored < 6 else 'screen_passed' if passed == 6 else 'review', 'cases': outcomes}
                entry = {'checked_at': iso(time.time()), 'mode': mode, 'model': model,
                         'status': probe['status'], 'latency_ms': probe.get('latency_ms')}
                if mode == 'quality':
                    entry.update({key: result['quality'][key] for key in ('passed', 'scored')})
                result['history'] = ([entry] + previous.get('history', []))[:10]
                self.find(source_id, fingerprint)
                data = self.load()
                data['results'][fingerprint] = result
                active = {item['fingerprint'] for item in self.inventory()[0]}
                data['results'] = {key: value for key, value in data['results'].items() if key in active}
                self.write(data)
                return result
        finally:
            self.busy.discard(source_id)
