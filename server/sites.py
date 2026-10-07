"""Fixed cloud destinations; each site owns credentials, sessions and history."""
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple
from .config import DATA, CONSOLE, SERVICES, WORKSPACE


@dataclass(frozen=True)
class Site:
    id: str
    console: str
    signin: str
    services: Mapping[str, str]
    workspace: str
    data: Path
    credentials_prefix: str
    log_endpoint: str
    cluster_priority: Optional[Tuple[str, ...]] = None
    inventory_clusters: Optional[frozenset] = None
    log_endpoints: Tuple[str, ...] = ()


MAOSHANWANG = Site('maoshanwang', CONSOLE, 'https://signin.ms-sc-01.maoshanwangtech.com',
    SERVICES, WORKSPACE, DATA, 'MONITOR_IAM_', 'monitor-data.ms-sc-01.maoshanwangtechapi.com',
    ('modeltoolchain-test', 'modeltoolchain', 'vigen'), frozenset(('modeltoolchain','modeltoolchain-test')))
SENSECORE = Site('sensecore', 'https://console.sensecore.cn/cn-sh-01', 'https://signin.sensecore.cn',
    {'CCI':'https://cci.cn-sh-01.sensecore.cn', 'AEC2':'https://aec2.cn-sh-01.sensecoreapi.cn',
     'MONITOR':'https://monitor.sensecoreapi.cn', 'MANAGEMENT':'https://management.sensecoreapi.cn',
     'AFS':'https://afs.cn-sh-01.sensecoreapi.cn'},
    '/subscriptions/65b0bc94-2cf4-42a3-b054-9900bbf51b7e/resourceGroups/default/zones/cn-sh-01z/workspaces/fdfe760c-ecd5-4466-aa6f-3d4125b17e7f',
    DATA/'sensecore', 'SENSECORE_IAM_', 'monitor-data.cn-sh-01b.sensecoreapi.cn',
    log_endpoints=tuple('monitor-data.'+zone+'.sensecoreapi.cn' for zone in ('cn-sh-01','cn-sh-01a','cn-sh-01b','cn-sh-01e','cn-sh-01f')))
SITES = {s.id:s for s in (MAOSHANWANG,SENSECORE)}
