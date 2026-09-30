from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
DATA = ROOT / 'data'
DATA.mkdir(mode=0o700, exist_ok=True)
os.chmod(DATA, 0o700)
WORKSPACE = os.environ.get('MONITOR_WORKSPACE', '/subscriptions/a14649f0-2e3e-4a08-91ca-5a7e435d7ada/resourceGroups/default/zones/ms-sc-01z/workspaces/modeltoolchain')
INTERVAL = max(30, int(os.environ.get('MONITOR_INTERVAL', '60')))
CONSOLE = 'https://console.ms-sc-01.maoshanwangtech.com'
SERVICES = {
    'AFS': 'https://afs.ms-sc-01.maoshanwangtechapi.com',
    'CCI': 'https://cci.ms-sc-01.maoshanwangtech.com',
    'AEC2': 'https://aec2.ms-sc-01.maoshanwangtechapi.com',
    'MONITOR': 'https://monitor.ms-sc-01.maoshanwangtechapi.com',
    'MANAGEMENT': 'https://management.ms-sc-01.maoshanwangtechapi.com',
}
