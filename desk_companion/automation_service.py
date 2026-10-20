"""Automation admission, scheduling and receipts; no GUI or host dependency."""
from copy import deepcopy
import threading
import time
import uuid

from . import automation as a
from .logutil import log


class AutomationService:
    def __init__(self, *, review=None, tasks=None, busy=lambda: False, changed=lambda: None):
        self.review = review
        self.tasks = tasks
        self.busy = busy
        self.changed = changed
        self._lock = threading.RLock()
        self._queued = {}
        self._active = {}
        self._pending_results = {}
        self._news_active = {}
        self._monitors = set()
        self._running_action = ''
        self._running_job = ''
        self._worker = None
        self._last_tick = 0
        self._booted = False
        self._stopping = threading.Event()

    def _call(self, action, job_id=''):
        try:
            return action()
        except a.AutomationChangeError as exc:
            return dict(exc.details, ok=False, job_id=exc.details.get('job_id', job_id), error=str(exc))
        except Exception as exc:
            return dict(ok=False, job_id=job_id, stage='admission', change_state='not_changed', error=str(exc))

    def list_automation_jobs(self, seen=False):
        return self._call(lambda: self.seen() if seen else self.snapshot())

    def save_automation_job(self, payload):
        def save():
            with self._lock:
                self._admit()
                self._idle(str(payload.get('id') or '') if isinstance(payload, dict) else '')
                return a.upsert_job(payload)
        return self._call(save, str(payload.get('id') or '') if isinstance(payload, dict) else '')

    def delete_automation_job(self, job_id):
        def delete():
            with self._lock:
                self._admit()
                self._idle(job_id)
                return a.delete_job(job_id)
        return self._call(delete, job_id)

    def run_automation_job(self, job_id):
        return self._call(lambda: self.run_now(job_id), job_id)

    def _idle(self, job_id):
        if job_id and (job_id in self._active or job_id in self._news_active or job_id == self._running_job):
            raise RuntimeError('该作业正在排队、执行、重试或结果收尾，不能修改、启停或删除。')

    def _admit(self):
        if self._stopping.is_set():
            raise RuntimeError('自动化正在退出，已停止接收新作业。')
        self._recover()

    def _recover(self):
        if self._booted:
            return
        with a.transaction():
            store = a.load_store()
            if a._abandon_queued(store):
                a.save_store(store)
            self._booted = True

    def _occupied(self):
        return {e['action'] for e in self._active.values()} | set(self._queued) | (
            {self._running_action} if self._running_action else set()) | ({'ai_news'} if self._news_active else set())

    def _accept(self, job):
        execution = dict(id=uuid.uuid4().hex, action=job['action'], doc=job['doc'], phase='queued',
                         accepted_at=a._now().isoformat(timespec='seconds'))
        job.update(last_result='queued', last_error='', alert_seen=True, execution=execution)
        return deepcopy(execution)

    def run_now(self, job_id):
        with self._lock, a.transaction():
            self._admit()
            store = a.load_store()
            job = a._find(store, str(job_id or '').strip())
            if job['action'] in self._occupied() or job['last_result'] in {'queued', 'running'}:
                raise RuntimeError('同类任务正在跑、排队或收尾，不会叠第二趟。')
            execution = self._accept(job)
            try:
                a.save_store(store)
            except a.AutomationChangeError as exc:
                # No memory queue is installed, even if replacement succeeded before the error.
                exc.details.update(stage='enqueue_save', job_id=job['id'], execution=execution)
                raise
            self._active[job['id']] = execution
            self._queued[job['action']] = job['id']
        self._kick()
        try:
            return dict(self.snapshot(), message='已加入执行队列。', job_id=job['id'], execution=execution)
        except Exception as exc:
            raise a.AutomationChangeError(f'执行请求已接受，但列表读取失败：{exc}。请按执行标识重新查询，不要重提。',
                stage='snapshot', change_state='committed', job_id=job['id'], execution=execution) from exc

    def snapshot(self):
        with self._lock, a.transaction():
            result = a.list_snapshot(queued=self._queued)
            for job in result['items']:
                job['active'] = job['id'] in self._active or job['id'] in self._news_active or job['id']==self._running_job
                observation = self._active.get(job['id'], {}).get('observation_error')
                if observation:
                    job['last_error'] = observation
                if job['id'] in self._pending_results:
                    job['pending_result'] = deepcopy(self._pending_results[job['id']])
                    job['last_error'] = ('本轮结果已取得，但本地回执保存未确认；保留执行占用，仅重试本地记录，请勿重复执行。 '
                                         + self._pending_results[job['id']]['last_error'])
            return result

    def seen(self):
        with self._lock:
            a.mark_alerts_seen()
            return self.snapshot()

    def tick(self):
        with self._lock:
            if self._stopping.is_set():
                return
            now_m = time.monotonic()
            if self._last_tick and now_m - self._last_tick < a.TICK_SEC:
                return
            self._last_tick = now_m
            try:
                self._recover()
                self._flush_results()
                with a.transaction():
                    store = a.load_store()
                    now = a._now()
                    dirty = a._mark_missed(store, now, active=set(self._active))
                    occupied = self._occupied()
                    pending = []
                    for job in a._due_jobs(store, now):
                        action = job['action']
                        if job['id'] in self._active or (action == 'ai_news' and action in occupied):
                            continue
                        job['last_run_slot'] = a._slot_key(a._latest_slot(now, job) or now, job)
                        dirty = True
                        if action in occupied:
                            job.update(last_result='missed', last_error='同类作业正在排队、执行或收尾，本时段未执行。', alert_seen=False)
                            continue
                        occupied.add(action)
                        pending.append((job['id'], self._accept(job)))
                    dirty = a._heartbeat(store, now) or dirty
                    if dirty:
                        a.save_store(store)
                    for jid, execution in pending:
                        self._active[jid] = execution
                        self._queued[execution['action']] = jid
            except Exception as exc:
                log(f'automation tick: {exc}')
                return
        self._kick()

    def _kick(self):
        with self._lock:
            if self._stopping.is_set() or not self._queued or self._worker is not None:
                return
            if self.busy() and 'ai_news' not in self._queued:
                return
            self._worker = threading.Thread(target=self._loop, daemon=True, name='automation')
            self._worker.start()

    def _loop(self):
        deferred = False
        try:
            while not self._stopping.is_set():
                busy = self.busy() or deferred
                with self._lock:
                    action = next((k for k in a.ACTION_ORDER if k in self._queued and (not busy or k=='ai_news')), '')
                    if not action:
                        return
                    job_id = self._queued.pop(action)
                    self._running_action = action
                    self._running_job = job_id
                try:
                    if self._execute(job_id) is False:
                        with self._lock:
                            if not self._stopping.is_set():
                                self._queued[action] = job_id
                        deferred = True
                except Exception as exc:
                    self._finish(job_id, dict(ok=False, stage='start', error=f'执行准备失败，未启动：{exc}'), phase='not_started')
                finally:
                    with self._lock:
                        self._running_action = ''
                        self._running_job = ''
        finally:
            with self._lock:
                self._worker = None
            self._notify()
            if not deferred:
                self._kick()

    def _execute(self, job_id):
        with self._lock, a.transaction():
            if self._stopping.is_set():
                return
            store = a.load_store()
            job = a._find(store, job_id)
            execution = deepcopy(self._active.get(job_id) or job.get('execution'))
            if not execution:
                execution = self._accept(job)  # Direct offline execution remains testable.
            self._active[job_id] = execution
            execution['phase'] = 'running'
            job.update(last_result='running', execution=execution)
            try:
                a.save_store(store)
            except Exception as exc:
                self._finish(job_id, dict(ok=False, stage='start_save', change_state='unconfirmed',
                    error=f'执行开始记录未确认，未调用外部动作：{exc}。'), phase='not_started')
                return
        try:
            action = execution['action']
            if action == 'ai_news':
                self._start_news(job_id)
                return
            result = (self.review.generate_week_review() if action=='retro_gen'
                      else self.review.write_scheduled_review(execution['doc']))
            if result.get('retryable_busy'):
                with self._lock, a.transaction():
                    if self._stopping.is_set():
                        self._finish(job_id, dict(ok=False, error='退出时尚未取得助手准入，未执行。'), phase='not_started')
                        return
                    store = a.load_store()
                    job = a._find(store, job_id)
                    execution['phase'] = 'queued'
                    job.update(last_result='queued', execution=execution)
                    a.save_store(store)
                return False
        except Exception as exc:
            result = dict(ok=False, stage='execution', change_state='unconfirmed',
                          error=f'执行结果未确认：{exc}。请核对记录和远端目标，不要直接重发。')
        self._finish(job_id, result)

    def _finish(self, job_id, result, *, phase='completed', status=None, reset_slot=False, task_id=''):
        with self._lock:
            execution = deepcopy(self._active.get(job_id, {}))
            execution.update(phase=phase, result=deepcopy(result))
            record = dict(execution=execution, last_result=status or ('ok' if result.get('ok') else 'fail'),
                          last_error=result.get('error', ''), alert_seen=bool(result.get('ok')))
            if task_id:
                record['last_task_id'] = task_id
            if reset_slot:
                record['last_run_slot'] = ''
            self._pending_results[job_id] = record
            self._flush_results()

    def _flush_results(self):
        for jid, record in list(self._pending_results.items()):
            try:
                with a.transaction():
                    store = a.load_store()
                    a._find(store, jid).update(record)
                    a.save_store(store)
            except Exception as exc:
                log(f'automation result save {jid}: {exc}')
                continue
            self._pending_results.pop(jid, None)
            self._active.pop(jid, None)

    def _start_news(self, job_id):
        started = self._launch_news(job_id)
        with self._lock:
            self._news_active[job_id] = started['task_id']
        self._news_result(job_id, 'running', started['task_id'])
        monitor = threading.Thread(target=self._monitor_news, args=(job_id, started), daemon=True, name='automation-news')
        with self._lock:
            self._monitors.add(monitor)
            monitor.start()

    def _launch_news(self, job_id, attempt=0, seconds=900):
        from .news_controller import start, NewsSubmissionError
        with self._lock:
            identity = self._active.get(job_id, {}).get('id', job_id)
        try:
            return start(self.tasks(), publish=True, job_id=job_id, attempt=attempt, seconds=seconds,
                         submission_key=f'{identity}:news:{attempt}')
        except NewsSubmissionError as exc:
            with self._lock:
                self._active.get(job_id, {})['observation_error'] = str(exc)
            return dict(task_id=exc.task_id, run_id=exc.run_id)

    def _news_result(self, job_id, result, task_id, error='', reset_slot=False):
        if result != 'running':
            self._finish(job_id, dict(ok=result=='ok', stage='news', task_id=task_id, error=error),
                status=result, reset_slot=reset_slot, task_id=task_id)
            return
        with self._lock, a.transaction():
            try:
                store = a.load_store()
                job = a._find(store, job_id)
                job.update(last_result='running', last_task_id=task_id)
                a.save_store(store)
            except Exception as exc:
                log(f'news task accepted {task_id}, running receipt unconfirmed: {exc}')

    def _monitor_news(self, job_id, started):
        from .news import load_run
        from .tasks import TERMINAL
        deadline = None
        task_id = started['task_id']
        finished = False
        try:
            for attempt in range(2):
                while True:
                    try:
                        task = self.tasks().get(task_id, False)
                    except Exception as exc:
                        with self._lock:
                            if job_id in self._active:
                                self._active[job_id]['observation_error'] = f'任务结果读取未确认：{exc}。保留执行占用，请核对任务。'
                        if self._stopping.wait(.3):
                            task = dict(state='interrupted', started=0, error='任务结果读取未确认。')
                            break
                        continue
                    with self._lock:
                        self._active.get(job_id, {}).pop('observation_error', None)
                    if task['started'] and deadline is None:
                        deadline = time.monotonic() + max(0, 900 - (time.time() - task['started']))
                    if task['state'] in TERMINAL or self._stopping.wait(.3):
                        break
                if task['state']=='succeeded':
                    self._news_result(job_id, 'ok', task_id)
                    finished = True
                    return
                run = load_run(started['run_id'])
                seconds = int((deadline or time.monotonic()) - time.monotonic())
                if attempt or self._stopping.is_set() or task['state']=='cancelled' or not run.get('retryable') or seconds<20:
                    self._news_result(job_id, 'missed' if self._stopping.is_set() else 'fail', task_id,
                        '退出时资讯尚未确认完成，将按既有阶段和发布回执恢复最新一次。' if self._stopping.is_set()
                        else task['error'] or '资讯任务中断，已保留阶段回执。', reset_slot=self._stopping.is_set())
                    finished = True
                    return
                self.tasks().event(task_id, 'status', '资讯任务遇到临时失败，将在剩余时间内重试一次，复用已完成阶段。')
                if self._stopping.wait(2):
                    return
                retried = self._launch_news(job_id, 1, max(1, seconds-2))
                task_id = retried['task_id']
                with self._lock:
                    self._news_active[job_id] = task_id
                self._news_result(job_id, 'running', task_id)
        except Exception as exc:
            self._news_result(job_id, 'fail', task_id, f'资讯恢复调度未完成，请核对任务和发布回执：{exc}')
            finished = True
        finally:
            if not finished and self._stopping.is_set():
                self._news_result(job_id, 'missed', task_id, '退出时资讯未完成，需核实发布回执。', reset_slot=True)
            with self._lock:
                self._news_active.pop(job_id, None)
                self._monitors.discard(threading.current_thread())
            self._notify()

    def stop(self, timeout=.5):
        self._stopping.set()
        with self._lock:
            queued = list(self._queued.values())
            self._queued.clear()
            for jid in queued:
                self._finish(jid, dict(ok=False, stage='stop', error='退出时尚未开始，未执行。'),
                             phase='not_started', status='missed')
            for jid in list(self._active):
                if jid not in queued and jid not in self._pending_results:
                    owned = deepcopy(self._active[jid])
                    self._finish(jid, dict(ok=False, stage='stop', change_state='unconfirmed',
                        error='退出时执行结果未确认，请先核对正文、任务与远端目标，不要直接重发。'), phase='unconfirmed')
                    # The ongoing call still owns its identity until it actually finishes.
                    if jid not in self._active:
                        self._active[jid] = owned
            threads = ([self._worker] if self._worker else []) + list(self._monitors)
        deadline = time.monotonic() + timeout
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline-time.monotonic()))
        self._notify()

    def _notify(self):
        try:
            self.changed()
        except Exception as exc:
            log(f'automation notify: {exc}')
