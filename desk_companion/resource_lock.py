"""进程间文件锁：独立 Agent 写共享资料时仍遵循同一把锁。"""
from contextlib import contextmanager
import hashlib
import errno
import os
import threading
import time
from .paths import data_root


class ResourceLock:
    def __init__(self, name, *, shared=False):
        self.name = name
        self.shared = shared
        self.local = threading.RLock()
        self.depth = 0
        self.file = None

    def __enter__(self):
        self.acquire()
        return self

    def acquire(self, blocking=True):
        if not self.local.acquire(blocking=blocking):
            return False
        try:
            if not self.depth:
                path = data_root() / "memory" / "locks" / (hashlib.sha256(self.name.encode()).hexdigest() + ".lock")
                path.parent.mkdir(parents=True, exist_ok=True)
                self.file = path.open("a+b")
                if os.name == "nt":
                    import msvcrt
                    while True:
                        try:
                            self.file.seek(0)
                            if self.shared:
                                _windows_shared_lock(self.file.fileno())
                            else:
                                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError as exc:
                            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                                raise
                            if not blocking:
                                self.file.close(); self.file = None
                                self.local.release()
                                return False
                            time.sleep(.05)
                else:
                    import fcntl
                    try:
                        fcntl.flock(self.file.fileno(), (fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX)
                                    | (0 if blocking else fcntl.LOCK_NB))
                    except BlockingIOError:
                        self.file.close(); self.file = None
                        self.local.release()
                        return False
                # Windows byte-range locks also reject reads from another process.
                # Lock the range first (including an empty file), then initialize it.
                if not self.shared:
                    self.file.seek(0)
                    if not self.file.read(1):
                        self.file.write(b"0")
                        self.file.flush()
            self.depth += 1
            return True
        except BaseException:
            if self.file:
                self.file.close()
                self.file = None
            self.local.release()
            raise

    @contextmanager
    def hold(self, *, blocking=True):
        if not self.acquire(blocking=blocking):
            raise BlockingIOError(f'资源正在使用：{self.name}')
        try:
            yield self
        finally:
            self.__exit__()

    def __exit__(self, *args):
        self.depth -= 1
        if not self.depth:
            self.file.close()  # 关闭句柄释放系统锁；杀掉 worker 时也自动释放。
            self.file = None
        self.local.release()


WRITES = ResourceLock("shared-tool-writes")


def _windows_shared_lock(fd):
    """LockFileEx shared range locks interoperate with existing exclusive CRT locks."""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = kernel.LockFileEx
    fn.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                   wintypes.DWORD, ctypes.POINTER(Overlapped)]
    fn.restype = wintypes.BOOL
    # FAIL_IMMEDIATELY, with EXCLUSIVE_LOCK absent. No data access/initialization under a shared lock.
    if not fn(msvcrt.get_osfhandle(fd), 1, 0, 1, 0, ctypes.byref(Overlapped())):
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            raise BlockingIOError(errno.EACCES, 'resource locked')
        raise ctypes.WinError(error)
