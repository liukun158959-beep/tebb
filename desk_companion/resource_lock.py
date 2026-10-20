"""进程间文件锁：独立 Agent 写共享资料时仍遵循同一把锁。"""
from contextlib import contextmanager
import hashlib
import os
import threading
import time
from .paths import data_root


class ResourceLock:
    def __init__(self, name):
        self.name = name
        self.local = threading.RLock()
        self.depth = 0
        self.file = None

    def __enter__(self):
        self.local.acquire()
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
                            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            time.sleep(.05)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_EX)
                # Windows byte-range locks also reject reads from another process.
                # Lock the range first (including an empty file), then initialize it.
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
            self.depth += 1
            return self
        except BaseException:
            if self.file:
                self.file.close()
                self.file = None
            self.local.release()
            raise

    def __exit__(self, *args):
        self.depth -= 1
        if not self.depth:
            self.file.close()  # 关闭句柄释放系统锁；杀掉 worker 时也自动释放。
            self.file = None
        self.local.release()


WRITES = ResourceLock("shared-tool-writes")
