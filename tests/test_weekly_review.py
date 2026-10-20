"""Review ownership, publication facts and isolated fault/concurrency contracts."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desk_companion import board_data as b, automation, feishu_auth, feishu_docs
from desk_companion.chat_sessions import ChatSessionService
from desk_companion.weekly_review import WeeklyReviewService


NOW = datetime(2026, 10, 21, 12, tzinfo=b.TZ)
MONDAY = NOW.replace(day=19, hour=0)
DAY = NOW.date().isoformat()
URL = 'https://example.invalid/docx/created'
SECTIONS = dict(agenda=dict(ok=True, items=[dict(summary='meeting', start='09:00', end='10:00')]),
                tasks=dict(ok=True, items=[dict(summary='pending', due_at='tomorrow')]))


class ReviewFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, DESK_COMPANION_DATA_DIR=temp.name))
        self.day = self.stack.enter_context(patch.object(b, 'today_date', return_value=DAY))
        self.stack.enter_context(patch.object(b, 'week_range', return_value=(MONDAY, NOW)))
        self.stack.enter_context(patch.object(b, 'fetch_board', return_value=SECTIONS))
        self.materials = self.stack.enter_context(patch.object(b, 'fetch_week_feishu', return_value=SECTIONS))
        self.github = self.stack.enter_context(patch('desk_companion.github.format_week_review_text', return_value='committed output'))
        self.stack.enter_context(patch('desk_companion.skill_catalog.read_skill', return_value=dict(body='review rules')))
        self.llm = SimpleNamespace(chat=Mock(return_value=dict(message=dict(content='new review'), usage=dict(total_tokens=5))))
        self.conversation = SimpleNamespace(running=False, agent=SimpleNamespace(llm=self.llm), agent_error='',
            state=SimpleNamespace(session_id='ordinary', persona='persona'), record_llm_usage=Mock())
        self.sessions = ChatSessionService(self.conversation)
        self.open = Mock()
        self.service = WeeklyReviewService(self.sessions, open_document=self.open)
        self.create = self.stack.enter_context(patch.object(feishu_auth, 'create_markdown_doc',
            return_value=dict(ok=True, url=URL, document_id='created')))
        self.overwrite = self.stack.enter_context(patch.object(feishu_docs, 'overwrite_markdown_doc',
            return_value=dict(ok=True, url=URL)))
        self.seed()

    def seed(self, **fields):
        snap = dict(date=DAY, fetched_at='fetched', **SECTIONS, summary='old review',
                    summary_at=(NOW-timedelta(hours=1)).isoformat(), doc_url='https://example.invalid/old')
        snap.update(fields)
        b._write_today_file(snap)
        return snap


class ReviewTests(ReviewFixture):
    def test_generation_preserves_materials_usage_and_clears_stale_link(self):
        result = self.service.generate_week_review()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['summary'], 'new review')
        self.assertEqual(result['doc_url'], '')
        self.assertEqual(result['summary_at'], NOW.isoformat(timespec='seconds'))
        self.assertEqual(b._read_today_file()['summary'], result['summary'])
        self.materials.assert_called_once_with(MONDAY, NOW)
        self.github.assert_called_once_with(MONDAY, NOW)
        messages = self.llm.chat.call_args.args[0]
        self.assertEqual(self.llm.chat.call_args.kwargs, dict(tools=None))
        for part in ('review rules', 'committed output', 'meeting', 'pending', '不要把会议、待办或对话写成产出'):
            self.assertIn(part, messages[-1]['content'])
        self.conversation.record_llm_usage.assert_called_once()
        self.assertIsNone(self.llm.sampling)
        self.assertIsNone(self.llm.on_reasoning)
        self.assertFalse(self.conversation.running)
        self.assertEqual(self.conversation.state.session_id, 'ordinary')

    def test_invalid_outputs_record_usage_but_never_replace_summary(self):
        for message in (dict(content=''), dict(content=123), dict(content=['not text']),
                        dict(content='body', tool_calls=[{}]), dict(content='body', function_call={'name':'x'})):
            with self.subTest(message=message):
                before = b.today_path().read_bytes()
                self.conversation.record_llm_usage.reset_mock()
                self.llm.chat.return_value = dict(message=message)
                result = self.service.generate_week_review()
                self.assertEqual(result['stage'], 'validation', result)
                self.conversation.record_llm_usage.assert_called_once()
                self.assertEqual(b.today_path().read_bytes(), before)
                self.assertFalse(self.conversation.running)

    def test_incomplete_response_and_usage_failure_keep_acquired_body(self):
        incomplete = RuntimeError('EOF incomplete')
        incomplete.partial = dict(message=dict(content='partial body'))
        self.llm.chat.side_effect = incomplete
        before = b.today_path().read_bytes()
        result = self.service.generate_week_review()
        self.assertEqual((result['stage'], result['summary']), ('model', 'partial body'))
        self.conversation.record_llm_usage.assert_not_called()
        self.llm.chat.side_effect = None
        self.conversation.record_llm_usage.side_effect = OSError('usage disk')
        result = self.service.generate_week_review()
        self.assertEqual((result['stage'], result['summary']), ('usage', 'new review'))
        self.assertEqual(b.today_path().read_bytes(), before)
        self.assertFalse(self.conversation.running)

    def test_material_failure_releases_reservation_without_model_or_storage_change(self):
        before = b.today_path().read_bytes()
        self.materials.return_value = dict(SECTIONS, tasks=dict(ok=False, error='denied'))
        result = self.service.generate_week_review()
        self.assertEqual(result['stage'], 'feishu_materials')
        self.llm.chat.assert_not_called()
        self.assertEqual(b.today_path().read_bytes(), before)
        self.assertFalse(self.conversation.running)
        self.conversation.agent = None
        self.assertEqual(self.service.generate_week_review()['stage'], 'model_setup')

    def test_midnight_and_monday_rollover_never_write_old_materials_to_new_day(self):
        for new_day in ('2026-10-22', '2026-10-26'):
            self.day.return_value = DAY
            self.seed()
            def model(*args, **kwargs):
                self.day.return_value = new_day
                self.seed(date=new_day, summary='new day review', doc_url='new day link')
                return dict(message=dict(content='old material result'))
            self.llm.chat.side_effect = model
            result = self.service.generate_week_review()
            self.assertEqual(result['stage'], 'cache_precondition', result)
            self.assertEqual(result['summary'], 'old material result')
            self.assertEqual(b._read_today_file()['summary'], 'new day review')
            self.assertEqual(b._read_today_file()['doc_url'], 'new day link')

    def test_save_before_and_after_replace_failure_never_claims_rollback(self):
        from desk_companion.video import atomic_write
        for after in (False, True):
            self.seed()
            def write(path, value):
                if after:
                    atomic_write(path, value)
                raise OSError('lost write acknowledgement')
            with patch('desk_companion.video.atomic_write', side_effect=write):
                result = self.service.generate_week_review()
            self.assertEqual((result['stage'], result['change_state']), ('cache_save', 'unconfirmed'), result)
            self.assertEqual(result['summary'], 'new review')
            self.assertEqual(b._read_today_file()['summary'], 'new review' if after else 'old review')

    def test_publication_modes_keep_registration_and_opening_differences(self):
        created = self.service.write_today_summary_doc()
        self.assertTrue(created['ok'], created)
        self.create.assert_called_once_with('2026-10-19～2026-10-21 周复盘', 'old review')
        self.assertEqual(created['document_id'], 'created')
        self.assertEqual(b._read_today_file()['doc_url'], URL)
        self.open.assert_called_once_with(URL)
        self.open.reset_mock(); self.seed()
        selected = self.service.write_week_review_doc(dict(doc='token'))
        self.assertTrue(selected['ok'], selected)
        self.overwrite.assert_called_once_with('token', 'old review')
        self.assertEqual(b._read_today_file()['doc_url'], URL)
        self.open.assert_not_called()
        self.seed()
        scheduled = self.service.write_scheduled_review('configured-token')
        self.assertTrue(scheduled['ok'], scheduled)
        self.assertEqual(b._read_today_file()['doc_url'], 'https://example.invalid/old')
        self.open.assert_not_called()
        self.assertTrue(self.service.write_week_review_doc(dict(title='custom title'))['ok'])
        self.create.assert_called_with('custom title', 'old review')
        self.open.assert_called_once_with(URL)

    def test_all_publications_require_valid_current_week_nonfuture_timestamp(self):
        for at in ('', 'bad', '2026-10-20T08:00:00', (MONDAY-timedelta(seconds=1)).isoformat(),
                   (NOW+timedelta(seconds=1)).isoformat()):
            self.seed(summary_at=at)
            for action in (self.service.write_today_summary_doc,
                           lambda: self.service.write_week_review_doc(dict(title='new')),
                           lambda: self.service.write_scheduled_review('token')):
                result = action()
                self.assertEqual(result['stage'], 'validation', result)
        self.seed(summary='')
        self.assertFalse(self.service.write_today_summary_doc()['ok'])
        self.create.assert_not_called(); self.overwrite.assert_not_called(); self.open.assert_not_called()

    def test_bad_parameters_never_publish_or_open(self):
        for payload in (None, [], {}, dict(doc='token', title='heading')):
            self.assertFalse(self.service.write_week_review_doc(payload)['ok'])
        self.assertFalse(self.service.write_scheduled_review('')['ok'])
        self.create.assert_not_called(); self.overwrite.assert_not_called()

    def test_remote_unknown_and_bad_envelopes_keep_body_target_and_never_retry(self):
        before = b.today_path().read_bytes()
        for value in (RuntimeError('timeout'), {}, dict(ok=False), dict(ok=True, document_id='id', url='file:///unsafe')):
            self.create.reset_mock()
            self.create.side_effect = value if isinstance(value, Exception) else None
            self.create.return_value = value
            result = self.service.write_today_summary_doc()
            self.assertEqual((result['stage'], result['remote_state']), ('remote', 'unconfirmed'), result)
            self.assertEqual(result['summary'], 'old review')
            self.assertIn('周复盘', result['target'])
            self.create.assert_called_once()
            self.assertEqual(b.today_path().read_bytes(), before)
        self.open.assert_not_called()

    def test_overwrite_without_valid_returned_url_does_not_register_or_open(self):
        for url in ('', 'file:///unsafe'):
            self.overwrite.return_value = dict(ok=True, url=url)
            result = self.service.write_week_review_doc(dict(doc='token'))
            self.assertTrue(result['ok'], result)
            self.assertEqual(b._read_today_file()['doc_url'], 'https://example.invalid/old')
        self.open.assert_not_called()

    def test_remote_confirmed_cache_precondition_write_and_open_failures_keep_facts(self):
        def changed(title, body):
            self.seed(summary='newer body')
            return dict(ok=True, url=URL, document_id='created')
        self.create.side_effect = changed
        result = self.service.write_today_summary_doc()
        self.assertEqual((result['stage'], result['remote_state'], result['change_state']),
                         ('cache_precondition', 'confirmed', 'committed'), result)
        self.assertEqual(result['summary'], 'old review')
        self.assertEqual(result['url'], URL)
        self.assertNotEqual(b._read_today_file()['doc_url'], URL)
        self.open.assert_not_called()
        self.create.side_effect = None; self.seed()
        with patch('desk_companion.video.atomic_write', side_effect=OSError('disk')):
            result = self.service.write_today_summary_doc()
        self.assertEqual((result['remote_state'], result['cache_state']), ('confirmed', 'unconfirmed'), result)
        self.assertEqual(result['document_id'], 'created')
        self.open.side_effect = OSError('shell')
        result = self.service.write_today_summary_doc()
        self.assertEqual((result['stage'], result['remote_state'], result['cache_state']),
                         ('open', 'confirmed', 'confirmed'), result)
        self.assertEqual(b._read_today_file()['doc_url'], URL)

    def test_publish_day_rollover_preserves_remote_fact_without_registering_old_link(self):
        def changed(doc, body):
            self.day.return_value = '2026-10-22'
            self.seed(date='2026-10-22', summary='new day')
            return dict(ok=True, url=URL)
        self.overwrite.side_effect = changed
        result = self.service.write_week_review_doc(dict(doc='token'))
        self.assertEqual((result['stage'], result['remote_state']), ('cache_precondition', 'confirmed'), result)
        self.assertEqual(result['url'], URL)
        self.assertEqual(b._read_today_file()['summary'], 'new day')
        self.assertNotEqual(b._read_today_file()['doc_url'], URL)

    def test_creation_confirmed_without_link_retains_document_id_for_remote_check(self):
        self.create.side_effect = feishu_auth.DocumentCreationError('created without URL', 'created-id')
        result = self.service.write_today_summary_doc()
        self.assertEqual((result['stage'], result['remote_state'], result['document_id']),
                         ('remote_result', 'confirmed', 'created-id'), result)
        self.assertEqual(result['steps']['remote'], 'confirmed')
        self.assertIn('created-id', result['error'])
        self.open.assert_not_called()
        self.create.assert_called_once()

    def test_reservation_covers_materials_and_publication_without_holding_shared_locks(self):
        from desk_companion.resource_lock import WRITES
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def fetch(*args):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('not released')
            return SECTIONS
        self.materials.side_effect = fetch
        result = []
        worker = threading.Thread(target=lambda: result.append(self.service.generate_week_review()))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertTrue(self.conversation.running)
            for action in (self.service.generate_week_review, self.service.write_today_summary_doc,
                           lambda: self.service.write_scheduled_review('token')):
                self.assertTrue(action()['retryable_busy'])
                self.assertTrue(self.conversation.running)
            with WRITES.hold(blocking=False), self.sessions.selected_session():
                self.assertEqual(b.load_today_snapshot(refresh=False)['summary'], 'old review')
                b.save_today_fields(doc_url='parallel link')
        finally:
            release.set(); worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0]['ok'], result)
        self.assertFalse(self.conversation.running)

    def test_guard_rejection_is_a_busy_result_and_does_not_take_ownership(self):
        @contextmanager
        def guard():
            raise RuntimeError('host busy')
            yield
        self.service._start_guard = guard
        self.assertTrue(self.service.generate_week_review()['retryable_busy'])
        self.materials.assert_not_called()
        self.assertFalse(self.conversation.running)

    def test_no_gui_subprocess_runs_generation_and_publication(self):
        code = '''
import sys, unittest
sys.path.insert(0,'tests')
from gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0,NoGui())
from tests.test_weekly_review import ReviewTests
suite=unittest.TestSuite(ReviewTests(n) for n in (
 'test_generation_preserves_materials_usage_and_clears_stale_link',
 'test_publication_modes_keep_registration_and_opening_differences'))
assert unittest.TextTestRunner().run(suite).wasSuccessful()
assert_no_gui()
'''
        child = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
            encoding='utf-8', timeout=25, env={**os.environ, 'PYTHONUTF8':'1'})
        self.assertEqual(child.returncode, 0, child.stdout+child.stderr)


class ReviewSchedulerTests(ReviewFixture):
    def job(self, action):
        automation.upsert_job(dict(name=action, action=action, enabled=False, cadence='daily',
            weekdays=[], hour=12, minute=0, doc='token' if action == 'retro_write' else ''))
        return next(j for j in automation.load_store()['jobs'] if j['action']==action)['id']

    def test_scheduled_review_actions_use_same_service_and_record_real_outcomes(self):
        scheduler = automation.AutomationScheduler(SimpleNamespace(weekly_review=self.service))
        generate, publish = self.job('retro_gen'), self.job('retro_write')
        scheduler._execute(generate); scheduler._execute(publish)
        self.assertEqual([j['last_result'] for j in automation.load_store()['jobs']], ['ok', 'ok'])
        self.overwrite.assert_called_once_with('token', 'new review')
        self.assertEqual(b._read_today_file()['doc_url'], '')
        self.open.assert_not_called()
        self.overwrite.side_effect = RuntimeError('timeout')
        scheduler._execute(publish)
        failed = next(j for j in automation.load_store()['jobs'] if j['id']==publish)
        self.assertEqual(failed['last_result'], 'fail')
        self.assertIn('未确认', failed['last_error'])
        self.assertFalse(failed['alert_seen'])

    def test_final_admission_race_keeps_queue_and_retries_only_after_later_kick(self):
        jid = self.job('retro_gen')
        scheduler = automation.AutomationScheduler(SimpleNamespace(weekly_review=self.service, _agent_running=False))
        self.conversation.running = True  # Host precheck can race the service admission.
        with patch.object(scheduler, '_kick'), patch.object(scheduler, '_notify'):
            scheduler.run_now(jid)
            scheduler._loop()
            self.assertEqual(scheduler._queued, dict(retro_gen=jid))
            job = automation.load_store()['jobs'][0]
            self.assertEqual(job['last_result'], 'queued')
            self.assertEqual(job['last_error'], '')
            self.llm.chat.assert_not_called()
            self.conversation.running = False
            scheduler._loop()
        self.assertFalse(scheduler._queued)
        self.assertEqual(automation.load_store()['jobs'][0]['last_result'], 'ok')
        self.llm.chat.assert_called_once()

    def test_deferred_review_still_allows_independent_news_to_start(self):
        jid = self.job('retro_gen')
        scheduler = automation.AutomationScheduler(SimpleNamespace(weekly_review=self.service, _agent_running=False))
        self.conversation.running = True
        scheduler._queued = dict(retro_gen=jid, ai_news='news')
        execute = scheduler._execute
        def run(job_id):
            if job_id != 'news':
                return execute(job_id)
        with patch.object(scheduler, '_execute', side_effect=run) as called, patch.object(scheduler, '_notify'):
            scheduler._loop()
        self.assertEqual([c.args[0] for c in called.call_args_list], [jid, 'news'])
        self.assertEqual(scheduler._queued, dict(retro_gen=jid))


class ReviewDocumentAdapterTests(unittest.TestCase):
    def test_creation_missing_link_exposes_confirmed_document_identity(self):
        env = dict(ok=True, data=dict(document=dict(document_id='created-id')))
        with patch.object(feishu_auth, '_run_lark', return_value=json.dumps(env)) as cli:
            with self.assertRaises(feishu_auth.DocumentCreationError) as caught:
                feishu_auth.create_markdown_doc('title', 'body')
            self.assertEqual(caught.exception.document_id, 'created-id')
            self.assertEqual(cli.call_args.kwargs, dict(timeout=90, stdin='body'))

    def test_overwrite_requires_explicit_success_and_preserves_timeout_and_target(self):
        for envelope in ({}, dict(ok=False), dict(ok=1), dict(ok=True, data=dict(url=URL))):
            with patch.object(feishu_docs, '_run_lark', return_value=json.dumps(envelope)) as cli:
                if envelope.get('ok') is True:
                    self.assertEqual(feishu_docs.overwrite_markdown_doc('token', 'body')['url'], URL)
                else:
                    with self.assertRaises(RuntimeError):
                        feishu_docs.overwrite_markdown_doc('token', 'body')
                self.assertEqual(cli.call_args.kwargs, dict(timeout=90, stdin='body'))
                self.assertEqual(cli.call_args.args[0][cli.call_args.args[0].index('--doc')+1], 'token')
        with patch.object(feishu_docs, '_run_lark', return_value=json.dumps(dict(ok=True))):
            self.assertEqual(feishu_docs.overwrite_markdown_doc('token', 'body')['url'], '')
