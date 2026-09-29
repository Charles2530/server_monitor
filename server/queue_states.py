"""Startup states for the all-user queues; stopping/finished tasks are excluded."""
from .acp_states import WAITING_LABELS

QUEUE_LABELS = {
    'CCI': {
        'WAIT_QUOTA':'等待配额', 'INIT':'初始化', 'QUEUING':'排队中',
        'SCHEDULED':'已调度', 'PROGRESSING':'启动中',
    },
    'ACP': {state:label for state,label in WAITING_LABELS.items() if state != 'SUSPENDING'},
}
