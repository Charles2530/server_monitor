"""Machine-aware runtime tuning shared by deployment and server components."""
from functools import lru_cache
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = Path(os.environ.get('MONITOR_RUNTIME_PROFILE', ROOT / 'runtime-profile.json'))

# Cloud request concurrency stays deliberately capped at the values measured on
# the local service. The performance tier only increases local fan-out that is
# still bounded by this cap.
_PROFILE_FIELDS = {
    'collector_concurrency': (1, 6),
    'metric_concurrency': (1, 4),
    'queue_concurrency': (1, 8),
    'terminal_max_sessions': (1, 16),
    'target_cache_seconds': (2, 30),
}
_POLL_FIELDS = {'state_poll_seconds': (2, 30), 'busy_state_poll_seconds': (2, 10),
                'error_state_poll_seconds': (2, 30)}

_MEASURED_PROFILES = {
    ('Mac16,12', 'arm64', 10, 16.0): {
        'queue_concurrency': 3,
        'state_poll_seconds': 10,
        'busy_state_poll_seconds': 10,
        'error_state_poll_seconds': 10,
        'web_process_type': 'Standard',
    },
    ('Mac16,10', 'arm64', 10, 24.0): {
        'state_poll_seconds': 30,
        'busy_state_poll_seconds': 10,
        'error_state_poll_seconds': 15,
        'web_process_type': 'Interactive',
    },
}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _measured_overrides(hardware, ncpu, memory_gb):
    model = str(hardware.get('model') or 'unknown')
    machine = hardware.get('machine')
    for (expected_model, expected_machine, expected_ncpu, expected_memory), overrides in _MEASURED_PROFILES.items():
        if (model == expected_model and ncpu == expected_ncpu and memory_gb == expected_memory
                and machine == expected_machine):
            return overrides
    return {}


def detect_hardware():
    """Read stable local hardware facts without failing on non-macOS hosts."""
    values = {'model': 'unknown', 'machine': platform.machine(), 'ncpu': os.cpu_count() or 1,
              'memory_gb': 0}
    if platform.system() == 'Darwin':
        try:
            result = subprocess.run(['/usr/sbin/sysctl', '-n', 'hw.model', 'hw.ncpu', 'hw.memsize'],
                                    capture_output=True, text=True, check=True, timeout=3)
            lines = [line.strip() for line in result.stdout.splitlines()]
            if len(lines) >= 1 and lines[0]:
                values['model'] = lines[0]
            if len(lines) >= 2:
                values['ncpu'] = _as_int(lines[1], values['ncpu'])
            if len(lines) >= 3:
                values['memory_gb'] = round(_as_int(lines[2]) / (1024 ** 3), 1)
        except (OSError, subprocess.SubprocessError):
            pass
    return values


def choose_profile(hardware):
    """Return deterministic settings from detected resources.

    The tiers are intentionally conservative: cloud request concurrency never
    exceeds the proven limit, while larger machines get more local queue and
    terminal capacity.
    """
    ncpu = max(1, _as_int(hardware.get('ncpu'), 1))
    try:
        memory_gb = max(0, float(hardware.get('memory_gb') or 0))
    except (TypeError, ValueError):
        memory_gb = 0
    if ncpu >= 12 or memory_gb >= 32:
        name = 'm4-performance'
        values = dict(collector_concurrency=6, metric_concurrency=4,
                      queue_concurrency=8, terminal_max_sessions=12,
                      target_cache_seconds=6)
    elif ncpu <= 6 or (memory_gb and memory_gb < 16):
        name = 'm4-compact'
        values = dict(collector_concurrency=5, metric_concurrency=3,
                      queue_concurrency=4, terminal_max_sessions=6,
                      target_cache_seconds=10)
    else:
        name = 'm4-balanced'
        values = dict(collector_concurrency=6, metric_concurrency=4,
                      queue_concurrency=6, terminal_max_sessions=8,
                      target_cache_seconds=8)
    values.update(_measured_overrides(hardware, ncpu, memory_gb))
    return {'name': name, **values, 'hardware': {
        'model': str(hardware.get('model') or 'unknown'),
        'machine': str(hardware.get('machine') or 'unknown'),
        'ncpu': ncpu,
        'memory_gb': memory_gb,
    }}


def _valid_profile(value):
    if (not isinstance(value, dict) or not isinstance(value.get('name'), str)
            or value['name'] not in {'m4-compact', 'm4-balanced', 'm4-performance'}):
        return False
    for key, (low, high) in _PROFILE_FIELDS.items():
        value_num = value.get(key)
        if type(value_num) is not int or not low <= value_num <= high:
            return False
    for key, (low, high) in _POLL_FIELDS.items():
        if key in value and (type(value[key]) is not int or not low <= value[key] <= high):
            return False
    if value.get('web_process_type', 'Background') not in ('Background', 'Standard', 'Interactive'):
        return False
    return value['metric_concurrency'] <= value['collector_concurrency'] - 2


def _load_profile():
    try:
        value = json.loads(PROFILE_PATH.read_text())
        if _valid_profile(value):
            if 'hardware' not in value or value['hardware'] == choose_profile(detect_hardware())['hardware']:
                return value
    except (OSError, ValueError, TypeError):
        pass
    return choose_profile(detect_hardware())


@lru_cache(maxsize=1)
def get_runtime_profile():
    """Load the deployment profile once per process."""
    return _load_profile()
