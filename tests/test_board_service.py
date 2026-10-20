"""Today's board lifecycle: isolated storage, fake Feishu and actual process leases."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from desk_companion import board_data as b, feishu_tools
from desk_companion.board_service import BoardService


DAY = '2026-10-20'
START = DAY + 'T09:00:00+08:00'
END = DAY + 'T10:00:00+08:00'
AGENDA = dict(ok=True, items=[dict(summary='meeting', event_id='event', start='09:00', end='10:00')])
TASKS = dict(ok=True, items=[dict(summary='task', guid='task', due_at='18:00')])


class BoardFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, DESK_COMPANION_DATA_DIR=temp.name))
        self.stack.enter_context(patch.object(b, 'today_date', return_value=DAY))
        self.stack.enter_context(patch.object(b, 'fetch_board', return_value=dict(agenda=AGENDA, tasks=TASKS)))
        self.stack.enter_context(patch.object(b, '_open_tasks_due_today', return_value=TASKS))
        self.calls = []
        def lark(args):
            self.calls.append(args)
            if args[:2] == ['calendar', '+agenda']:
                return json.dumps(dict(ok=True, data=[dict(summary='new', event_id='created', start=START, end=END)]))
            return json.dumps(dict(ok=True, data=dict(event=dict(event_id='created'))))
        self.lark = self.stack.enter_context(patch.object(b, '_run_lark', side_effect=lark))
        self.service = BoardService()

    def seed(self, day=DAY):
        snap = dict(date=day, fetched_at='old', agenda=AGENDA, tasks=TASKS,
                    summary='saved summary', summary_at='saved time', doc_url='https://example.invalid/doc')
        b._write_today_file(snap)
        return snap

    def child(self, code, *args):
        process = subprocess.Popen([sys.executable, '-u', '-c', code, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            env={**os.environ, 'PYTHONUTF8': '1'})
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        return process


class BoardServiceTests(BoardFixture):
    def test_four_entries_preserve_normal_contract_and_remote_side_effects(self):
        before = self.seed()
        self.assertEqual(self.service.load_board(), dict(ok=True, **before))
        created = self.service.create_agenda('title', START, END)
        self.assertTrue(created['ok'], created)
        self.assertEqual(created['summary'], 'title')
        self.assertEqual(created['event_id'], 'created')
        self.assertEqual(created['snapshot']['summary'], 'saved summary')
        deleted = self.service.delete_agenda('event')
        self.assertTrue(deleted['ok'], deleted)
        task = self.service.delete_task('task')
        self.assertTrue(task['ok'], task)
        delete = next(a for a in self.calls if a[:3] == ['calendar', 'events', 'delete'])
        self.assertIn('--need-notification', delete)
        self.assertEqual(delete[delete.index('--need-notification')+1], 'true')
        self.assertEqual(delete[delete.index('--calendar-id')+1], 'primary')
        self.assertEqual(delete[delete.index('--as')+1], 'user')
        self.assertTrue(any(a[:3] == ['task', 'tasks', 'delete'] and '--yes' in a for a in self.calls))
        self.assertEqual(task['doc_url'], before['doc_url'])
        self.assertEqual(task['summary_at'], before['summary_at'])

    def test_create_without_cache_non_today_and_missing_optional_event_id(self):
        self.lark.side_effect = lambda args: json.dumps(dict(ok=True))
        result = self.service.create_agenda('future', '2026-10-21T09:00:00+08:00', '2026-10-21T10:00:00+08:00')
        self.assertTrue(result['ok'], result)
        self.assertIsNone(result['snapshot'])
        self.assertFalse(result['on_today'])
        self.assertIn('今天', result['notice'])
        self.assertNotIn('event_id', result)
        self.assertFalse(b.today_path().exists())

    def test_validation_prevents_remote_calls_and_keeps_storage(self):
        before = self.seed(); self.lark.reset_mock()
        for call in (lambda: self.service.delete_agenda(''), lambda: self.service.delete_task('x\ny'),
                     lambda: self.service.create_agenda('', START, END),
                     lambda: self.service.create_agenda('x', END, START),
                     lambda: self.service.create_agenda('x', '2026-10-20T09:00', END)):
            result = call()
            self.assertEqual(result['change_state'], 'not_changed', result)
        self.lark.assert_not_called()
        self.assertEqual(b._read_today_file(), before)

    def test_delete_requires_today_cache_before_remote_call(self):
        for seed in (None, '2026-10-19'):
            if seed:
                self.seed(seed)
            self.assertFalse(self.service.delete_agenda('event')['ok'])
            self.assertFalse(self.service.delete_task('task')['ok'])
        self.lark.assert_not_called()

    def test_remote_uncertainty_never_retries_or_refreshes(self):
        self.seed(); before = b.today_path().read_bytes()
        for failure in (RuntimeError('timeout'), 'not JSON', json.dumps(dict(ok=False, error='denied'))):
            self.lark.reset_mock()
            self.lark.side_effect = failure if isinstance(failure, Exception) else None
            self.lark.return_value = failure
            result = self.service.create_agenda('retained title', START, END)
            self.assertEqual((result['stage'], result['remote_state']), ('remote', 'unconfirmed'), result)
            self.assertEqual(result['summary'], 'retained title')
            self.assertEqual(result['start'], START)
            self.assertIn('核对', result['error'])
            self.assertEqual(self.lark.call_count, 1)
            self.assertEqual(b.today_path().read_bytes(), before)

    def test_confirmed_remote_query_failure_is_not_full_success(self):
        self.seed()
        def lark(args):
            if args[:2] == ['calendar', '+agenda']:
                raise RuntimeError('query unavailable')
            return json.dumps(dict(ok=True, data=dict(event_id='new-id')))
        self.lark.side_effect = lark
        result = self.service.create_agenda('title', START, END)
        self.assertEqual((result['remote_state'], result['cache_state'], result['stage']),
                         ('confirmed', 'confirmed', 'query'), result)
        self.assertEqual(result['event_id'], 'new-id')
        self.assertFalse(result['snapshot']['agenda']['ok'])
        self.assertEqual(result['snapshot']['tasks'], TASKS)
        self.assertIn('已成功', result['error'])
        self.assertEqual(b._read_today_file()['summary'], 'saved summary')

    def test_cache_write_before_and_after_replace_reports_remote_confirmed(self):
        from desk_companion.video import atomic_write
        for after in (False, True):
            self.seed()
            before = b.today_path().read_bytes()
            def write(path, value):
                if after:
                    atomic_write(path, value)
                raise OSError('replace acknowledgement lost')
            with patch('desk_companion.video.atomic_write', side_effect=write):
                result = self.service.delete_agenda('event')
            self.assertEqual((result['remote_state'], result['cache_state'], result['stage']),
                             ('confirmed', 'unconfirmed', 'cache_save'), result)
            self.assertEqual((result['event_id'], result['summary'], result['start']), ('event', 'meeting', '09:00'))
            self.assertIn('已成功', result['error'])
            self.assertEqual(b.today_path().read_bytes() == before, not after)

    def test_refresh_merges_latest_summary_link_and_preserves_failed_section_errors(self):
        self.seed()
        def fetch():
            b.save_today_fields(summary='concurrent summary', summary_at='new time')
            b.save_today_fields(doc_url='https://example.invalid/new')
            return dict(agenda=dict(ok=False, items=[], error='query failed'), tasks=TASKS)
        with patch.object(b, 'fetch_board', side_effect=fetch):
            result = self.service.load_board(True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['summary'], 'concurrent summary')
        self.assertEqual(result['summary_at'], 'new time')
        self.assertEqual(result['doc_url'], 'https://example.invalid/new')
        self.assertFalse(result['agenda']['ok'])
        self.assertEqual(result['agenda']['error'], 'query failed')
        self.assertEqual(result['tasks'], TASKS)

    def test_remote_partition_update_merges_latest_fields_and_other_partition(self):
        self.seed()
        original = self.lark.side_effect
        def lark(args):
            if args[:2] == ['calendar', '+agenda']:
                b.save_today_fields(summary='new summary', doc_url='new url')
            return original(args)
        self.lark.side_effect = lark
        result = self.service.delete_agenda('event')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['summary'], 'new summary')
        self.assertEqual(result['doc_url'], 'new url')
        self.assertEqual(result['tasks'], TASKS)

    def test_change_of_day_does_not_copy_previous_summary_or_link(self):
        self.seed('2026-10-19')
        result = self.service.load_board()
        self.assertEqual(result['date'], DAY)
        self.assertEqual((result['summary'], result['summary_at'], result['doc_url']), ('', '', ''))

    def test_invalid_cache_is_reported_and_never_silently_replaced(self):
        self.seed()
        for invalid in ('not JSON', '{}'):
            b.today_path().write_text(invalid, encoding='utf-8')
            result = self.service.load_board(True)
            self.assertFalse(result['ok'])
            self.assertEqual(b.today_path().read_text(encoding='utf-8'), invalid)
        self.lark.assert_not_called()

    def test_midnight_refuses_old_query_and_keeps_new_day_snapshot(self):
        self.seed()
        tomorrow = '2026-10-21'
        def fetch():
            b.today_date.return_value = tomorrow
            self.seed(tomorrow)
            b.save_today_fields(summary='tomorrow summary')
            return dict(agenda=AGENDA, tasks=TASKS)
        with patch.object(b, 'fetch_board', side_effect=fetch):
            result = self.service.load_board(True)
        self.assertEqual(result['stage'], 'day_change', result)
        self.assertEqual(b._read_today_file()['date'], tomorrow)
        self.assertEqual(b._read_today_file()['summary'], 'tomorrow summary')

    def test_remote_success_over_midnight_keeps_success_fact_and_target(self):
        self.seed()
        def lark(args):
            b.today_date.return_value = '2026-10-21'
            self.seed('2026-10-21')
            return json.dumps(dict(ok=True, data=dict(event_id='new-id')))
        self.lark.side_effect = lark
        result = self.service.create_agenda('title', START, END)
        self.assertEqual((result['remote_state'], result['stage']), ('confirmed', 'day_change'))
        self.assertEqual(result['event_id'], 'new-id')
        self.assertEqual(b._read_today_file()['date'], '2026-10-21')
        self.assertEqual(self.lark.call_count, 1)

    def test_cache_hit_during_operation_and_other_operations_immediately_refused(self):
        self.seed()
        entered, release = threading.Event(), threading.Event()
        def fetch():
            entered.set()
            if not release.wait(10):
                raise RuntimeError('not released')
            return dict(agenda=AGENDA, tasks=TASKS)
        results = []
        with patch.object(b, 'fetch_board', side_effect=fetch):
            thread = threading.Thread(target=lambda: results.append(self.service.load_board(True)))
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertTrue(self.service.load_board()['ok'])
                for action in (lambda: self.service.load_board(True), lambda: self.service.delete_task('task'),
                               lambda: self.service.delete_agenda('event'),
                               lambda: self.service.create_agenda('title', START, END)):
                    self.assertEqual(action()['stage'], 'admission')
                b.save_today_fields(summary='parallel summary')
            finally:
                release.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]['summary'], 'parallel summary')

    def test_process_network_lease_allows_reads_summary_writes_and_releases_on_exit(self):
        self.seed()
        code = "from desk_companion.board_data import _operation; import time\nwith _operation():\n print('ready',flush=True); time.sleep(30)"
        process = self.child(code)
        self.assertEqual(process.stdout.readline().strip(), 'ready')
        self.assertTrue(self.service.load_board()['ok'])
        self.assertEqual(self.service.load_board(True)['stage'], 'admission')
        self.assertIn('看板正在更新', feishu_tools.create_calendar_event(dict(summary='title', start=START, end=END)))
        b.save_today_fields(summary='while child is working')
        process.kill(); process.communicate(timeout=10)
        self.assertTrue(self.service.load_board(True)['ok'])

    def test_process_storage_merges_concurrent_summary_and_doc_writes(self):
        self.seed()
        code = '''
from desk_companion import board_data as b
import sys
from unittest.mock import patch
with patch.object(b,'today_date',return_value=sys.argv[1]):
 for i in range(15): b.save_today_fields(**{sys.argv[2]:str(i)})
'''
        children = [self.child(code, DAY, field) for field in ('summary', 'doc_url')]
        for child in children:
            out, err = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, out+err)
        snap = b._read_today_file()
        self.assertEqual((snap['summary'], snap['doc_url']), ('14', '14'))
        self.assertEqual(snap['agenda'], AGENDA)

    def test_chat_tool_reports_confirmed_remote_then_cache_failure(self):
        self.seed()
        with patch('desk_companion.video.atomic_write', side_effect=OSError('disk')):
            result = feishu_tools.create_calendar_event(dict(summary='title', start=START, end=END))
        self.assertIn('已成功', result)
        self.assertIn('cache_save', result)
        self.assertIn('title', result)

    def test_no_gui_subprocess_exercises_four_entries_and_tool(self):
        code = '''
import sys, unittest
sys.path.insert(0,'tests')
from gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0,NoGui())
from tests.test_board_service import BoardServiceTests
suite=unittest.TestSuite(BoardServiceTests(n) for n in (
 'test_four_entries_preserve_normal_contract_and_remote_side_effects',
 'test_refresh_merges_latest_summary_link_and_preserves_failed_section_errors',
 'test_chat_tool_reports_confirmed_remote_then_cache_failure'))
assert unittest.TextTestRunner().run(suite).wasSuccessful()
assert_no_gui()
'''
        child = self.child(code)
        out, err = child.communicate(timeout=25)
        self.assertEqual(child.returncode, 0, out+err)

    def test_actual_task_calendar_tool_uses_board_lease_without_global_write_lock(self):
        from desk_companion.resource_lock import WRITES
        from desk_companion import state
        settings = state.load_state(0, 0)
        settings.session_id = 'desktop'; settings.save()
        request = dict(text='日程锁测试', session_id='calendar-worker', channel='desktop',
            sampling=dict(reasoning_effort='low', temperature=.5, top_p=1),
            limits=dict(call_timeout=30, task_timeout=60))
        child = subprocess.Popen([sys.executable, '-u', 'tests/board_worker_fixture.py'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            env={**os.environ, 'PYTHONUTF8': '1'})
        try:
            child.stdin.write(json.dumps(request)+'\n'); child.stdin.flush()
            deadline = time.monotonic()+20
            while not (self.root / 'calendar.ready').exists():
                self.assertIsNone(child.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            with WRITES.hold(blocking=False):
                b.save_today_fields(summary='parallel') if b.today_path().exists() else None
            self.assertEqual(self.service.create_agenda('other', START, END)['stage'], 'admission')
            (self.root / 'calendar.go').touch()
            out, err = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, err)
            events = [json.loads(line) for line in out.splitlines()]
            self.assertFalse([e for e in events if e['kind'] == 'failure'], out+err)
            started = next(e for e in events if e['kind'] == 'tool_start')
            self.assertEqual(started['data']['tool'], 'create_calendar_event')
            self.assertFalse(started['data']['read_only'])
            completed = next(e for e in events if e['kind'] == 'tool_end')
            self.assertIn('worker-event', str(completed['data']['result']))
            # Same write fingerprint remains guarded before the self-managed-lock branch.
            (self.root / 'calendar.ready').unlink()
            request['completed_writes'] = [started['data']['fingerprint']]
            resumed = subprocess.run([sys.executable, '-u', 'tests/board_worker_fixture.py'],
                input=json.dumps(request), capture_output=True, text=True, encoding='utf-8',
                timeout=25, env={**os.environ, 'PYTHONUTF8': '1'})
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertFalse((self.root / 'calendar.ready').exists())
            resumed_events = [json.loads(line) for line in resumed.stdout.splitlines()]
            ended = next(e for e in resumed_events if e['kind'] == 'tool_end')
            self.assertIn('不再重复执行', str(ended['data']['result']))
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
