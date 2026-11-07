"""Choose a local browser with a reliable network stack for cloud login."""
import os
from pathlib import Path
import sys


def browser_launch_options(headless=True):
    channel=os.environ.get('MONITOR_BROWSER_CHANNEL','auto').strip().lower()
    if channel not in ('auto','edge','chrome','chromium'):
        raise ValueError('MONITOR_BROWSER_CHANNEL must be auto, edge, chrome or chromium')
    if channel=='auto':
        edge=Path('/Applications/Microsoft Edge.app').exists()
        chrome=Path('/Applications/Google Chrome.app').exists()
        channel='edge' if sys.platform=='darwin' and edge else 'chrome' if sys.platform=='darwin' and chrome else 'chromium'
    return {'headless':headless,**({'channel':'msedge' if channel=='edge' else 'chrome'} if channel in ('edge','chrome') else {})}
