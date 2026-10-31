"""Bounded standbys: each process handles exactly one request, never two sessions."""
import json
import threading
import time

from .task_process import ProcessTree


class WorkerStandby:
    def __init__(self, spawn, count=1):
        self.spawn=spawn;self.count=count
        self.cv=threading.Condition();self.closed=False;self.ready=[];self.owned={}
        self.threads=[threading.Thread(target=self._loop,daemon=True,name='worker-standby') for _ in range(count)]
        for thread in self.threads:thread.start()

    def _dispose(self, process, tree):
        tree.close()
        for pipe in (process.stdin,process.stdout):
            if pipe:
                try:pipe.close()
                except OSError:pass

    def _loop(self):
        failures=0
        while True:
            with self.cv:
                while not self.closed and len(self.owned)>=self.count:self.cv.wait(.5)
                if self.closed:return
                # Reserve before spawning so concurrent refillers stay bounded.
                slot=object();self.owned[slot]=None
            item=None;process=tree=None
            try:
                process=self.spawn();tree=ProcessTree(process);item=(process,tree)
                with self.cv:
                    self.owned[slot]=item
                    if self.closed:raise RuntimeError('closed')
                timer=threading.Timer(30,tree.close);timer.daemon=True;timer.start()
                try:
                    event=json.loads(process.stdout.readline())
                    if event.get('kind')!='standby_ready' or process.poll() is not None:raise RuntimeError('standby unavailable')
                finally:timer.cancel()
                with self.cv:
                    if self.closed:raise RuntimeError('closed')
                    self.ready.append((slot,item));self.cv.notify_all()
                    failures=0
                    while not self.closed and slot in self.owned:
                        self.cv.wait(.5)
                        if process.poll() is not None:raise RuntimeError('standby exited')
                    if slot not in self.owned:item=None  # ownership transferred
            except Exception:
                failures=min(5,failures+1)  # Back off; failed warmup must not thrash.
            finally:
                with self.cv:
                    self.ready=[entry for entry in self.ready if entry[0] is not slot]
                    self.owned.pop(slot,None);self.cv.notify_all()
                if item:self._dispose(*item)
                elif process is not None and tree is None:
                    if process.poll() is None:process.kill()
                    process.wait()
                    for pipe in (process.stdin,process.stdout):
                        if pipe:pipe.close()
            with self.cv:
                if self.closed:return
                self.cv.wait(min(30,2**failures) if failures else .1)

    def take(self):
        with self.cv:
            if self.closed or not self.ready:return None
            slot,item=self.ready.pop(0);self.owned.pop(slot,None);self.cv.notify_all()
            if item[0].poll() is None:return item
        self._dispose(*item)
        return None

    def wait_ready(self, timeout=30):
        deadline=time.monotonic()+timeout
        with self.cv:
            while len(self.ready)<self.count and not self.closed:
                remaining=deadline-time.monotonic()
                if remaining<=0:return False
                self.cv.wait(remaining)
            return not self.closed

    def shutdown(self):
        with self.cv:
            self.closed=True
            items=[item for item in self.owned.values() if item]
            self.cv.notify_all()
        # Closing the process tree unblocks the readiness reader before joining.
        for process,tree in items:tree.close()
        for thread in self.threads:thread.join(3)
        with self.cv:
            remaining=[item for item in self.owned.values()if item]
            pending_slots=len(self.owned)
        processes={id(process):process for process,_ in items+remaining}
        workers=[dict(pid=process.pid,exited=process.poll()is not None,returncode=process.returncode)for process in processes.values()]
        threads_alive=sum(thread.is_alive()for thread in self.threads)
        return dict(complete=not pending_slots and not threads_alive and all(worker['exited']for worker in workers),
                    workers=workers,pending_slots=pending_slots,threads_alive=threads_alive,scope='owned_idle_standbys')
