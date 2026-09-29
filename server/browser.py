"""Choose an installed macOS Chrome or Playwright's portable Chromium."""
import os
from pathlib import Path
import sys


def browser_launch_options(headless=True):
    channel=os.environ.get('MONITOR_BROWSER_CHANNEL','auto').strip().lower()
    if channel not in ('auto','chrome','chromium'):
        raise ValueError('MONITOR_BROWSER_CHANNEL must be auto, chrome or chromium')
    if channel=='auto':
        chrome=Path('/Applications/Google Chrome.app').exists()
        channel='chrome' if sys.platform=='darwin' and chrome else 'chromium'
    return {'headless':headless,**({'channel':'chrome'} if channel=='chrome' else {})}
