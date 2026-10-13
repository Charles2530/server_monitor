"""Startup states for the all-user queues; stopping/finished tasks are excluded."""
from .acp_states import WAITING_LABELS, STOPPABLE_STATES

# CCI console's canStopApplication states; platform authorization remains authoritative.
STOPPABLE_BY_KIND = {
    'CCI': frozenset(('QUEUING', 'SCHEDULED', 'PROGRESSING', 'RUNNING', 'FAILED', 'UPDATING')),
    'ACP': STOPPABLE_STATES,
}

QUEUE_LABELS = {
    'CCI': {
        'WAIT_QUOTA':'等待配额', 'INIT':'初始化', 'QUEUING':'排队中',
        'SCHEDULED':'已调度', 'PROGRESSING':'启动中',
    },
    'ACP': {state:label for state,label in WAITING_LABELS.items() if state != 'SUSPENDING'},
}
