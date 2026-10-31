"""Data-directory ownership, acquired before any task recovery or subscriptions."""
from .resource_lock import ResourceLock

INSTANCE_EXIT_CODE = 73
RUNTIME_LOCK_NAME = 'runtime-process'
INSTANCE_MESSAGE = '该数据目录已有运行实例，请使用已有客户端或先退出该实例。'
_lease = None


class InstanceInUse(RuntimeError):
    pass


def acquire_runtime():
    global _lease
    if _lease is not None:
        raise InstanceInUse(INSTANCE_MESSAGE)
    lease = ResourceLock(RUNTIME_LOCK_NAME)
    if not lease.acquire(blocking=False):
        raise InstanceInUse(INSTANCE_MESSAGE)
    # Deliberately no stop/release API: daemon requests may still be finishing.
    # The OS releases this handle when the process exits, including a hard kill.
    _lease = lease
