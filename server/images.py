"""Read configured CCR repositories and Harbor artifacts; never execute Docker."""
import asyncio
import copy
import os
import re
import time
from pathlib import Path
from urllib.parse import quote
import httpx
from dotenv import dotenv_values
from .auth import load_session
from .normalize import iso


ACCOUNTS = ('sensecore', 'maoshanwang', 'xiaoyu-bg', 'xiaoyu-nx')
CCR = {'sensecore': 'https://ccr.cn-sh-01.sensecoreapi.cn',
       'maoshanwang': 'https://ccr.ms-sc-01.maoshanwangtechapi.com'}


def load_accounts(path=None):
    path = Path(path or os.environ.get('DOCKER_REGISTRIES_CONFIG', Path.home()/'initial/docker/registries.env')).expanduser()
    values = dotenv_values(path) if path.is_file() else {}
    accounts = {}
    for name in ACCOUNTS:
        prefix = name.upper().replace('-', '_')
        data = {key: values.get(prefix+'_'+key.upper()) or '' for key in ('registry', 'namespace', 'username', 'password')}
        if (re.fullmatch(r'[a-z0-9.-]+(?::[0-9]+)?', data['registry'])
                and re.fullmatch(r'[a-z0-9][a-z0-9_.-]*', data['namespace'])):
            accounts[name] = data
    return accounts


def public_accounts(accounts):
    return {name: {key: data[key] for key in ('registry', 'namespace')} for name, data in accounts.items()}


def image_rows(account, repository, artifact, metadata):
    attrs = artifact.get('extra_attrs') or {}
    environment = {}
    allowed = {'CUDA_VERSION', 'PYTORCH_VERSION', 'PYTHON_VERSION'}
    for item in (attrs.get('config') or {}).get('Env') or []:
        key, _, value = item.partition('=')
        if key in allowed and re.fullmatch(r'[A-Za-z0-9_.+\-]{1,128}', value):
            environment[key] = value
    namespace, name = repository.split('/', 1)
    digest = artifact.get('digest') or ''
    base = {'name': name, 'namespace': namespace, 'digest': digest,
            'source': metadata.get('source') or 'unknown', 'products': metadata.get('products') or [],
            'size_bytes': artifact.get('size'), 'created_at': attrs.get('created'),
            'pushed_at': artifact.get('push_time'), 'architecture': attrs.get('architecture'),
            'cuda': environment.get('CUDA_VERSION'), 'pytorch': environment.get('PYTORCH_VERSION'),
            'python': environment.get('PYTHON_VERSION')}
    return [{**base, 'tag': tag.get('name'),
             'path': account['registry']+'/'+repository+(':'+tag['name'] if tag.get('name') else '@'+digest)}
            for tag in artifact.get('tags') or [{}]]


class ImageInventory:
    def __init__(self, site, *, config_path=None, transport=None, session_loader=None):
        self.site, self.config_path, self.transport = site, config_path, transport
        self.session_loader = session_loader or (lambda: load_session(site))
        self.refresh_task = None
        self.checked_at = None
        self.updated_at = None
        self.records = {}
        self.repository_count = 0
        self.completed = 0
        self.warnings = []
        self.stale = False
        self.identity = None

    async def pages(self, client, path):
        rows, seen = [], set()
        for page in range(1, 1001):
            response = await client.get(path, params={'page': page, 'page_size': 100, 'with_label': 'false'})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list): raise ValueError('Invalid registry list')
            if not data: return rows
            identity = tuple(str(row.get('digest') or row.get('name') or row.get('id')) for row in data)
            if identity in seen: raise ValueError('Repeated registry page')
            seen.add(identity); rows.extend(data)
            total = response.headers.get('x-total-count')
            if (total is not None and len(rows) >= int(total)) or (total is None and len(data) < 100): return rows
        raise ValueError('Registry page limit reached')

    async def metadata(self, namespace):
        headers = self.session_loader()['headers']
        base = '/devtools/ccr/data/v1'+self.site.workspace.split('/workspaces/')[0]
        path = base+'/namespaces/'+quote(namespace, safe='')+'/repositories'
        result, token, seen = {}, '', set()
        async with httpx.AsyncClient(base_url=CCR[self.site.id], headers=headers, timeout=20,
                                     follow_redirects=False, transport=self.transport) as client:
            for _ in range(1000):
                response = await client.get(path, params={'pageSize': 100, 'pageToken': token})
                response.raise_for_status(); data = response.json()
                if not isinstance(data.get('repositories'), list): raise ValueError('Invalid CCR list')
                for row in data['repositories']:
                    if isinstance(row.get('name'), str): result[row['name']] = row.get('metadata') or {}
                token = str(data.get('nextPageToken') or '')
                if not token: return result
                if token in seen: raise ValueError('Repeated CCR page')
                seen.add(token)
        raise ValueError('CCR page limit reached')

    async def refresh(self, account):
        try:
            await asyncio.wait_for(self.read_inventory(account), 180)
        except asyncio.TimeoutError:
            self.warnings.append('仓库读取超时，当前记录可能不完整，请刷新重试。')
            self.stale = True
            self.checked_at = time.monotonic()

    async def read_inventory(self, account):
        self.completed = 0; self.warnings = []; self.stale = bool(self.records)
        try:
            async with httpx.AsyncClient(base_url='https://'+account['registry'],
                                         auth=(account['username'], account['password']), timeout=25,
                                         follow_redirects=False, transport=self.transport) as client:
                prefix = '/api/v2.0/projects/'+quote(account['namespace'], safe='')+'/repositories'
                repositories, metadata = await asyncio.gather(self.pages(client, prefix), self.metadata(account['namespace']), return_exceptions=True)
                if isinstance(repositories, BaseException): raise repositories
                if isinstance(metadata, BaseException):
                    metadata = {}; self.warnings.append('平台来源标签读取失败；未确认的来源显示为未知，可重新连接云会话后刷新。')
                repositories = [row for row in repositories if isinstance(row.get('name'), str) and row['name'].startswith(account['namespace']+'/')]
                self.repository_count = len(repositories)
                names = {row['name'] for row in repositories}
                self.records = {name: rows for name, rows in self.records.items() if name in names}
                semaphore = asyncio.Semaphore(4)
                async def read(repository):
                    name = repository['name']
                    # Harbor requires nested repository slashes to be encoded twice.
                    encoded = quote(quote(name.split('/', 1)[1], safe=''), safe='')
                    try:
                        async with semaphore:
                            artifacts = await self.pages(client, prefix+'/'+encoded+'/artifacts')
                        rows = [row for artifact in artifacts if artifact.get('type') == 'IMAGE'
                                for row in image_rows(account, name, artifact, metadata.get(name, {}))]
                        self.records[name] = rows
                    except Exception:
                        self.warnings.append(name+' 的镜像版本读取失败；保留已有记录。')
                    finally: self.completed += 1
                await asyncio.gather(*(read(repository) for repository in repositories))
            self.stale = bool(self.warnings)
            if not self.stale: self.updated_at = iso(time.time())
        except Exception:
            self.warnings.append('仓库读取失败，请检查 registry 配置、网络和账号权限；已有记录可能过期。')
            self.stale = bool(self.records)
        finally: self.checked_at = time.monotonic()

    async def snapshot(self, force=False, namespace=None):
        accounts = load_accounts(self.config_path)
        account = accounts.get(self.site.id)
        if account and namespace: account = {**account, "namespace": namespace}
        identity = (account['registry'], account['namespace']) if account else None
        if identity != self.identity:
            await ImageInventory.close(self)
            self.identity = identity
            self.refresh_task = None; self.checked_at = None; self.updated_at = None
            self.records = {}; self.repository_count = 0; self.completed = 0; self.warnings = []; self.stale = False
        configured = bool(account and account['username'] and account['password'])
        refreshing = bool(self.refresh_task and not self.refresh_task.done())
        interval = 60 if self.warnings else 300
        if configured and not refreshing and (force or self.checked_at is None or time.monotonic()-self.checked_at >= interval):
            self.refresh_task = asyncio.create_task(self.refresh(account)); refreshing = True
        warnings = list(self.warnings) if configured else ['未找到当前站点完整的 registry 配置，请检查 DOCKER_REGISTRIES_CONFIG 或 ~/initial/docker/registries.env。']
        rows = [row for group in self.records.values() for row in group] if configured else []
        return copy.deepcopy({'site': self.site.id, 'accounts': public_accounts(accounts),
            'registry': account['registry'] if account else None, 'namespace': account['namespace'] if account else None,
            'images': sorted(rows, key=lambda row: (row['pushed_at'] or '', row['path']), reverse=True),
            'repository_count': self.repository_count, 'completed_repositories': self.completed,
            'refreshing': refreshing, 'updated_at': self.updated_at, 'stale': self.stale,
            'warnings': warnings, 'status': 'loading' if refreshing else ('partial' if rows else 'error') if warnings else 'ok',
            'script': str(Path(os.environ.get('DOCKER_REGISTRY_SCRIPT', Path.home()/'initial/docker/registry.sh')).expanduser())})

    async def close(self):
        if self.refresh_task and not self.refresh_task.done(): self.refresh_task.cancel()
        if self.refresh_task: await asyncio.gather(self.refresh_task, return_exceptions=True)


class ImageManager(ImageInventory):
    """Share namespace discovery while keeping each inventory and refresh independent."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.namespaces = []
        self.namespace_warning = ''
        self.namespace_task = None
        self.namespace_checked = None
        self.inventories = {}
        self.account_identity = None

    async def discover_namespaces(self, account):
        try:
            async with httpx.AsyncClient(base_url='https://'+account['registry'],
                                         auth=(account['username'], account['password']), timeout=25,
                                         follow_redirects=False, transport=self.transport) as client:
                projects = await asyncio.wait_for(self.pages(client, '/api/v2.0/projects'), 60)
            names = {row['name'] for row in projects if row.get('current_user_role_id')
                     and isinstance(row.get('name'), str) and re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,127}', row['name'])}
            self.namespaces = [account['namespace']] + sorted(names - {account['namespace']})
            self.namespace_warning = ''
        except Exception:
            self.namespace_warning = '命名空间列表读取失败；保留已读取的命名空间，可刷新重试。'
        finally:
            self.namespace_checked = time.monotonic()

    async def snapshot(self, force=False, namespace=None):
        if namespace and not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,127}', namespace):
            from .acp_actions import ActionError
            raise ActionError('命名空间格式无效。')
        account = load_accounts(self.config_path).get(self.site.id)
        identity = tuple(account.get(key) for key in ('registry', 'namespace', 'username', 'password')) if account else None
        if identity != self.account_identity:
            await self.close()
            self.account_identity = identity
            self.identity = None
            self.inventories = {}; self.namespaces = []; self.namespace_warning = ''; self.namespace_checked = None
        default = account['namespace'] if account else None
        selected = namespace or default
        if selected and selected != default:
            if selected not in self.inventories:
                self.inventories[selected] = ImageInventory(self.site, config_path=self.config_path,
                                                           transport=self.transport, session_loader=self.session_loader)
            result = await self.inventories[selected].snapshot(force=force, namespace=selected)
        else:
            result = await super().snapshot(force=force)
        if account and account['username'] and account['password']:
            if not self.namespaces: self.namespaces = [default]
            if (not self.namespace_task or self.namespace_task.done()) and (force or self.namespace_checked is None
                    or time.monotonic() - self.namespace_checked >= (60 if self.namespace_warning else 300)):
                self.namespace_task = asyncio.create_task(self.discover_namespaces(account))
        result.update(default_namespace=default, namespaces=list(self.namespaces),
                      namespaces_refreshing=bool(self.namespace_task and not self.namespace_task.done()))
        if self.namespace_warning:
            result['warnings'].append(self.namespace_warning)
            if result['status'] == 'ok': result['status'] = 'partial'
        return result

    async def close(self):
        if self.namespace_task and not self.namespace_task.done(): self.namespace_task.cancel()
        if self.namespace_task: await asyncio.gather(self.namespace_task, return_exceptions=True)
        await asyncio.gather(*(inventory.close() for inventory in self.inventories.values()))
        await super().close()
