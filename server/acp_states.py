"""ACP states verified against the console, shared by collection and controls."""
WAITING_LABELS = {
    'WAIT_QUOTA':'等待配额', 'WAITING':'等待中', 'INIT':'初始化',
    'QUEUEING':'排队中', 'CREATING':'创建中', 'STARTING':'启动中',
    'RESTARTING':'重启中', 'RECOVERING':'恢复中', 'SUSPENDING':'停止中',
}
STOPPABLE_STATES = frozenset(WAITING_LABELS).difference({'SUSPENDING'}) | {'RUNNING'}


def is_charles(name):
    return isinstance(name,str) and 'charles' in name.casefold()
