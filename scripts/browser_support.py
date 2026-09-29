"""Allow directly executed smoke scripts to use the service's browser choice."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from server import config
from server.browser import browser_launch_options
