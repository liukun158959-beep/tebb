"""Automation contracts with isolated storage, execution barriers and real processes."""
from contextlib import ExitStack, closing
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from desk_companion import automation as a
from desk_companion.automation_service import AutomationService

NOW = datetime(2026, 10, 21, 12, tzinfo=a.TZ)


def payload(name='sample', action='retro_gen', **fields):
    return dict(name=name, action=action, enabled=False, cadence='daily', weekdays=[],
                hour=12, minute=0, doc='token' if action=='retro_write' else '', **fields)


class AutomationFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, DESK_COMPANION_DATA_DIR=temp.name, PYTHONUTF8='1'))
        self.stack.enter_context(patch.object(a, '_now', return_value=NOW))
        self.review = Mock()
        self.review.generate_week_review.return_value = dict(ok=True, summary='complete review')
        self.review.write_scheduled_review.return_value = dict(ok=True, url='https://example.invalid/doc', remote_state='confirmed')
        self.service = AutomationService(review=self.review, busy=lambda: True)
        self.addCleanup(lambda: self.service.stop(0))

    def save(self, **fields):
        result = self.service.save_automation_job(payload(**fields))
        self.assertTrue(result['ok'], result)
        return result['job_id']

    def stored(self, jid):
        return next(j for j in a.load_store()['jobs'] if j['id']==jid)

    def child(self, code, *args):
        process = subprocess.Popen([sys.executable, '-u', '-c', code, *args], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env=os.environ.copy())
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=15)
        self.addCleanup(cleanup)
        return process


class AutomationServiceTests(AutomationFixture):
    def test_four_contracts_disable_manual_execution_and_delete_only_configuration(self):
        jid = self.save()
        artifact = self.root/'keep.md'; artifact.write_text('keep')
        with patch.object(self.service, '_kick'):
            accepted = self.service.run_automation_job(jid)
        self.assertTrue(accepted['ok'], accepted)
        execution = accepted['execution']
        self.assertEqual(execution['action'], 'retro_gen')
        self.assertEqual(self.stored(jid)['last_run_slot'], '')
        self.service.busy = lambda: False
        self.service._loop()
        self.assertEqual(self.stored(jid)['execution']['id'], execution['id'])
        self.assertEqual(self.stored(jid)['execution']['result']['summary'], 'complete review')
        self.assertEqual(self.stored(jid)['last_result'], 'ok')
        self.assertFalse(self.service.snapshot()['items'][0]['active'])
        self.assertTrue(self.service.delete_automation_job(jid)['ok'])
        self.assertEqual(artifact.read_text(), 'keep')

    def test_queued_job_rejects_every_edit_and_delete_but_other_config_and_reads_continue(self):
        jid = self.save()
        with patch.object(self.service, '_kick'):
            self.service.run_now(jid)
        original = self.stored(jid)
        for change in (dict(enabled=True), dict(action='retro_write', doc='new target'), dict(name='changed')):
            form = {k: original[k] for k in ('id','name','action','enabled','cadence','weekdays','hour','minute','doc')}
            result = self.service.save_automation_job(dict(form, **change))
            self.assertFalse(result['ok'], result)
        self.assertFalse(self.service.delete_automation_job(jid)['ok'])
        self.assertEqual(self.stored(jid), original)
        other = self.save(name='other', action='retro_write')
        self.assertTrue(self.service.delete_automation_job(other)['ok'])
        self.assertTrue(self.service.list_automation_jobs()['ok'])
        self.assertFalse(self.service.run_automation_job(jid)['ok'])

    def test_failed_enqueue_before_or_after_replace_never_runs_unacknowledged_work(self):
        from desk_companion.video import atomic_write
        for after in (False, True):
            jid = self.save(name=str(after))
            def write(path, value):
                if after:
                    atomic_write(path, value)
                raise OSError('ack lost')
            with patch('desk_companion.video.atomic_write', side_effect=write):
                result = self.service.run_automation_job(jid)
            self.assertEqual((result['stage'], result['change_state']), ('enqueue_save','unconfirmed'), result)
            self.assertTrue(result['execution']['id'])
            self.assertFalse(self.service._queued)
            self.service.busy = lambda: False
            self.service._last_tick = 0; self.service.tick()
            self.review.generate_week_review.assert_not_called()
            if after:
                restarted = AutomationService(review=self.review)
                restarted._recover()
                self.assertEqual(self.stored(jid)['execution']['phase'], 'not_started')

    def test_successful_config_or_enqueue_then_snapshot_failure_is_committed(self):
        with patch.object(a, 'list_snapshot', side_effect=OSError('read')):
            result = self.service.save_automation_job(payload())
        self.assertEqual((result['stage'], result['change_state']), ('snapshot','committed'), result)
        jid = result['job_id']
        self.assertEqual(self.stored(jid)['name'], 'sample')
        with patch.object(self.service, '_kick'), patch.object(self.service, 'snapshot', side_effect=OSError('view')):
            result = self.service.run_automation_job(jid)
        self.assertEqual((result['stage'], result['change_state']), ('snapshot','committed'), result)
        self.assertEqual(self.service._queued, dict(retro_gen=jid))
        self.assertEqual(result['execution']['id'], self.stored(jid)['execution']['id'])

    def test_running_and_result_retry_do_not_hold_io_locks_or_repeat_action(self):
        jid = self.save()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def model():
            entered.set()
            if not release.wait(15):
                raise RuntimeError('not released')
            return dict(ok=True, summary='saved body', remote_state='confirmed')
        self.review.generate_week_review.side_effect = model
        self.service.busy = lambda: False
        self.assertTrue(self.service.run_automation_job(jid)['ok'])
        self.assertTrue(entered.wait(5))
        from desk_companion.resource_lock import WRITES
        try:
            with WRITES.hold(blocking=False), a._STORE_LOCK.hold(blocking=False):
                self.assertEqual(self.stored(jid)['last_result'], 'running')
            self.assertFalse(self.service.delete_automation_job(jid)['ok'])
            self.save(name='parallel', action='retro_write')
            from desk_companion.video import atomic_write
            def write(path, value):
                if any(j.get('execution', {}).get('phase')=='completed' for j in value['jobs']):
                    raise OSError('result disk')
                atomic_write(path, value)
            with patch('desk_companion.video.atomic_write', side_effect=write):
                release.set()
                self.service._worker.join(5)
                page = self.service.snapshot()
                row = next(j for j in page['items'] if j['id']==jid)
                self.assertTrue(row['active'])
                self.assertEqual(row['pending_result']['execution']['result']['summary'], 'saved body')
                self.assertFalse(self.service.delete_automation_job(jid)['ok'])
                self.assertFalse(self.service.run_automation_job(jid)['ok'])
            self.service._last_tick = 0; self.service.tick()
            self.assertEqual(self.stored(jid)['last_result'], 'ok')
            self.assertFalse(self.service.snapshot()['items'][0]['active'])
            self.review.generate_week_review.assert_called_once()
        finally:
            release.set()

    def test_stop_queued_vs_running_and_late_completion_preserves_execution_identity(self):
        jid = self.save()
        with patch.object(self.service, '_kick'):
            self.service.run_now(jid)
        self.service.stop(0)
        self.assertEqual(self.stored(jid)['execution']['phase'], 'not_started')
        self.assertIn('未执行', self.stored(jid)['last_error'])
        self.assertFalse(self.service.run_automation_job(jid)['ok'])
        self.review.generate_week_review.assert_not_called()
        self.service = AutomationService(review=self.review)
        self.addCleanup(lambda: self.service.stop(0))
        entered, release = threading.Event(), threading.Event(); self.addCleanup(release.set)
        def model():
            entered.set(); release.wait(10)
            return dict(ok=True, summary='late confirmed')
        self.review.generate_week_review.side_effect = model
        self.service.run_now(jid)
        self.assertTrue(entered.wait(5))
        identity = self.stored(jid)['execution']['id']
        self.service.stop(0)
        self.assertEqual(self.stored(jid)['execution']['phase'], 'unconfirmed')
        self.assertIn('结果未确认', self.stored(jid)['last_error'])
        worker = self.service._worker
        release.set(); worker.join(5)
        self.assertEqual(self.stored(jid)['execution']['id'], identity)
        self.assertEqual(self.stored(jid)['execution']['result']['summary'], 'late confirmed')

    def test_recovery_distinguishes_new_queued_running_and_legacy_unknown(self):
        ids = [self.save(name=str(i)) for i in range(3)]
        store = a.load_store()
        for i, job in enumerate(store['jobs']):
            job['last_result'] = 'queued'
            job['enabled'] = True
            job['created_at'] = (NOW-timedelta(days=1)).isoformat()
            if i<2:
                job['execution'] = dict(id=str(i), action='retro_gen', doc='', phase='queued' if i==0 else 'running')
        a.save_store(store)
        restarted = AutomationService(review=self.review)
        restarted._recover()
        self.assertIn('未执行', self.stored(ids[0])['last_error'])
        self.assertIn('结果未确认', self.stored(ids[1])['last_error'])
        self.assertIn('旧版', self.stored(ids[2])['last_error'])
        self.assertEqual(a._due_jobs(a.load_store(), NOW), [])
        self.review.generate_week_review.assert_not_called()

    def test_timed_same_action_conflict_is_explicit_and_manual_does_not_consume_slot(self):
        ids = [self.save(name=str(i)) for i in range(2)]
        store = a.load_store()
        for job in store['jobs']:
            job.update(enabled=True, created_at=(NOW-timedelta(days=1)).isoformat())
        a.save_store(store)
        with patch.object(self.service, '_kick'):
            self.service.tick()
        self.assertEqual(self.stored(ids[0])['last_result'], 'queued')
        self.assertEqual(self.stored(ids[1])['last_result'], 'missed')
        self.assertIn('同类', self.stored(ids[1])['last_error'])

    def test_delete_write_failure_keeps_configuration_and_acceptance_state(self):
        jid = self.save()
        with patch('desk_companion.video.atomic_write', side_effect=OSError('disk')):
            result = self.service.delete_automation_job(jid)
        self.assertEqual(result['change_state'], 'unconfirmed', result)
        self.assertEqual(self.stored(jid)['name'], 'sample')

    def test_no_gui_subprocess_exercises_configuration_execution_and_stop(self):
        code = '''
import sys, unittest
sys.path.insert(0,'tests')
from gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0,NoGui())
from tests.test_automation_service import AutomationServiceTests
suite=unittest.TestSuite(AutomationServiceTests(n) for n in (
 'test_four_contracts_disable_manual_execution_and_delete_only_configuration',
 'test_stop_queued_vs_running_and_late_completion_preserves_execution_identity'))
assert unittest.TextTestRunner().run(suite).wasSuccessful()
assert_no_gui()
'''
        child = self.child(code)
        out, err = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 0, out+err)

    def test_cross_process_configuration_transactions_do_not_lose_jobs(self):
        code = '''
import sys
from desk_companion import automation as a
for i in range(8):
 a.upsert_job(dict(name=sys.argv[1]+str(i), action='retro_gen',enabled=False,cadence='daily',weekdays=[],hour=1,minute=0,doc=''))
'''
        children = [self.child(code, name) for name in ('one','two')]
        for child in children:
            out, err = child.communicate(timeout=40)
            self.assertEqual(child.returncode, 0, out+err)
        self.assertEqual(len(a.load_store()['jobs']), 16)

    def test_same_action_manual_race_accepts_once(self):
        jid = self.save()
        barrier = threading.Barrier(3)
        results = []
        def submit():
            barrier.wait(); results.append(self.service.run_automation_job(jid))
        with patch.object(self.service, '_kick'):
            workers = [threading.Thread(target=submit) for _ in range(2)]
            for worker in workers: worker.start()
            barrier.wait()
            for worker in workers: worker.join(5)
        self.assertEqual(sum(bool(r['ok']) for r in results), 1, results)
        self.assertEqual(len(self.service._queued), 1)

    def test_busy_review_queue_does_not_start_or_spin_workers(self):
        jid = self.save()
        with patch('desk_companion.automation_service.threading.Thread') as thread:
            self.service.run_now(jid)
            self.service._loop()
        thread.assert_not_called()
        self.assertEqual(self.service._queued, dict(retro_gen=jid))
        self.assertEqual(self.stored(jid)['last_result'], 'queued')

    def test_news_running_receipt_failure_still_observes_same_task_without_resubmission(self):
        from desk_companion import news_controller
        jid = self.save(action='ai_news')
        manager = Mock()
        manager.get.return_value = dict(state='succeeded', started=time.time(), error='')
        self.service.tasks = lambda: manager
        from desk_companion.video import atomic_write
        def write(path, value):
            if any(j.get('last_task_id') and j['last_result']=='running' for j in value['jobs']):
                raise OSError('running acknowledgement lost')
            atomic_write(path, value)
        with patch.object(self.service, '_kick'):
            self.service.run_now(jid)
        with patch.object(news_controller, 'start', return_value=dict(task_id='accepted', run_id='run')) as start, \
                patch('desk_companion.video.atomic_write', side_effect=write):
            self.service._loop()
            deadline = time.monotonic()+5
            while self.service._news_active:
                self.assertLess(time.monotonic(), deadline); time.sleep(.01)
        start.assert_called_once()
        self.assertTrue(start.call_args.kwargs['submission_key'].endswith(':news:0'))
        self.assertEqual(self.stored(jid)['last_result'], 'ok')
        self.assertEqual(self.stored(jid)['last_task_id'], 'accepted')

    def test_news_observation_failure_keeps_job_owned_until_stop_and_resets_recovery_slot(self):
        from desk_companion import news_controller, news
        jid = self.save(action='ai_news')
        manager = Mock(); manager.get.side_effect = OSError('task database unavailable')
        self.service.tasks = lambda: manager
        with patch.object(news_controller, 'start', return_value=dict(task_id='accepted', run_id='run')) as start, \
                patch.object(news, 'load_run', return_value={}):
            self.service.run_now(jid)
            deadline = time.monotonic()+5
            while not self.service._active[jid].get('observation_error'):
                self.assertLess(time.monotonic(), deadline); time.sleep(.01)
            self.assertFalse(self.service.delete_automation_job(jid)['ok'])
            self.assertIn('读取未确认', self.service.snapshot()['items'][0]['last_error'])
            self.service.stop(1)
        start.assert_called_once()
        self.assertEqual(self.stored(jid)['last_result'], 'missed')
        self.assertEqual(self.stored(jid)['last_run_slot'], '')

    def test_news_submission_ack_loss_reconciles_existing_task_without_resubmitting(self):
        from desk_companion import news_controller, news
        from tests.test_news import fixture
        run = fixture()
        manager = Mock(); manager.cv = threading.Condition(threading.RLock())
        manager.list.return_value = dict(items=[])
        manager.submit.side_effect = OSError('event write failed after commit')
        manager.get.return_value = dict(source=dict(run_id=run['id']))
        with patch.object(news, 'load_settings', return_value=run['settings']), \
                patch.object(news_controller, 'datetime') as clock:
            clock.now.return_value = datetime.fromisoformat(run['day']).replace(tzinfo=a.TZ)
            result = news_controller.start(manager, publish=True, job_id='job', submission_key='execution:0')
        self.assertTrue(result['ok'])
        self.assertEqual(result['task_id'], manager.get.call_args.args[0])
        manager.submit.assert_called_once()


class RuntimeOwnershipTests(AutomationFixture):
    def test_duplicate_backend_and_legacy_rejected_before_task_recovery_then_kill_releases(self):
        from desk_companion.tasks import TaskManager
        manager = TaskManager(); manager.shutdown()
        path = self.root/'memory/tasks.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute("INSERT INTO tasks(id,session,channel,text,request,state,created) VALUES('keep','x','automation','x','{}','running',0)")
            db.commit()
        owner = self.child("from desk_companion.runtime_instance import acquire_runtime; acquire_runtime(); print('ready',flush=True); input()")
        self.assertEqual(owner.stdout.readline().strip(), 'ready')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        backend = subprocess.run([sys.executable, '-u', '-m', 'desk_companion.local_api.server', '--port', str(port)],
            capture_output=True, text=True, encoding='utf-8', timeout=30, env=os.environ.copy())
        self.assertEqual(backend.returncode, 73, backend.stdout+backend.stderr)
        self.assertIn('已有运行实例', backend.stdout+backend.stderr)
        legacy = self.child("from desk_companion.app import run_app; run_app('not-loaded')")
        out, err = legacy.communicate(timeout=30)
        self.assertNotEqual(legacy.returncode, 0)
        self.assertIn('已有运行实例', out+err)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT state FROM tasks WHERE id='keep'").fetchone()[0], 'running')
        owner.kill(); owner.communicate(timeout=15)
        successor = self.child("from desk_companion.runtime_instance import acquire_runtime; acquire_runtime(); print('acquired')")
        out, err = successor.communicate(timeout=15)
        self.assertEqual(successor.returncode, 0, out+err)
        self.assertIn('acquired', out)
