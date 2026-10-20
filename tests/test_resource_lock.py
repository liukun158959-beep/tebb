"""Real process contention for the shared storage lock."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from desk_companion.resource_lock import ResourceLock


SCRIPT = '''
import os, sys, time
from pathlib import Path
from desk_companion.resource_lock import ResourceLock
root = Path(os.environ['DESK_COMPANION_DATA_DIR'])
(root / (sys.argv[1] + '.ready')).touch()
for index in range(4):
    with ResourceLock('process-fixture'):
        path = root / 'count'
        count = int(path.read_text()) if path.exists() else 0
        time.sleep(.01)
        path.write_text(str(count + 1))
'''


class ResourceLockTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=folder.name)
        env.start(); self.addCleanup(env.stop)

    def worker(self, name):
        process = subprocess.Popen([sys.executable, '-c', SCRIPT, name], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                   env={**os.environ, 'PYTHONUTF8': '1'})
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        return process

    def test_contending_processes_wait_and_preserve_every_update(self):
        with ResourceLock('process-fixture'):
            processes = [self.worker(name) for name in ('first', 'second')]
            deadline = time.monotonic() + 15
            while not all((self.root / (name + '.ready')).exists() for name in ('first', 'second')):
                self.assertLess(time.monotonic(), deadline, 'children did not start')
                time.sleep(.01)
            for process in processes:
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.wait(timeout=.2)
        for process in processes:
            out, err = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, out + err)
        self.assertEqual(self.root.joinpath('count').read_text(), '8')

    def test_reentrant_lock_releases_after_exception(self):
        lock = ResourceLock('process-fixture')
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            with lock, lock:
                self.assertEqual(lock.depth, 2)
                raise RuntimeError('fixture')
        self.assertEqual(lock.depth, 0)
        self.assertIsNone(lock.file)
        process = self.worker('retry')
        out, err = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, out + err)
        self.assertEqual(self.root.joinpath('count').read_text(), '4')
