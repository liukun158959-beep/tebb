"""Manual compression with real SDK/storage, controlled IO and task reservations."""
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

import httpx
from openai import OpenAI
from atlas.llm import LLM

from desk_companion import context_pack, memory, state, usage
from desk_companion.assistant_settings import AssistantSettingsService
from desk_companion.chat_sessions import ChatSessionService
from desk_companion.context_compression import ContextCompressionService
from desk_companion.conversation import ConversationService
from desk_companion.memory_editing import MemoryEditingService
from desk_companion.resource_lock import WRITES
from desk_companion.tasks import TaskManager


class CompressionFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=folder.name)
        env.start(); self.addCleanup(env.stop)
        self.state = state.load_state(0, 0)
        self.state.session_id = 'selected'
        self.conversation = ConversationService(self.state)
        self.conversation.engage_model = Mock()
        self.sessions = ChatSessionService(self.conversation)
        self.service = ContextCompressionService(self.sessions)
        self.requests = []
        self.reply = dict(role='assistant', content='compressed summary')
        self.finish = 'stop'
        self.failure = None
        self.during_model = None
        def handle(request):
            self.requests.append(json.loads(request.content))
            if self.during_model:
                self.during_model()
            if self.failure:
                raise self.failure
            return httpx.Response(200, json=dict(id='fixture', object='chat.completion', created=0, model='fixture',
                choices=[dict(index=0, finish_reason=self.finish, message=self.reply)],
                usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15)))
        self.http = httpx.Client(transport=httpx.MockTransport(handle))
        sdk = OpenAI(api_key='fixture', base_url='https://example.invalid/v1', http_client=self.http, max_retries=0)
        self.addCleanup(sdk.close)
        # Resolve SDK lazy imports before timing thread barriers; this sends no request.
        sdk.chat.completions
        self.conversation.agent = SimpleNamespace(llm=LLM(client=sdk, model='fixture'), memory=['temporary'])
        for sid in ('selected', 'other'):
            for role, text in (('user', 'old question'), ('pet', 'old answer'),
                               ('user', 'latest question'), ('pet', 'latest answer')):
                memory.append_chat(role, text, sid)

    def old_pack(self):
        context_pack.prepare_injection('selected', 'system', '', exclude_user=None, manual=True,
                                       complete=lambda _: 'old saved summary')
        memory.append_chat('user', 'newest question', 'selected')
        memory.append_chat('pet', 'newest answer', 'selected')

    def manager(self):
        with patch.object(TaskManager, '_schedule', return_value=None):
            manager = TaskManager(path=self.root / 'tasks.sqlite3')
        self.addCleanup(manager.shutdown)
        self.sessions = ChatSessionService(self.conversation, mutation_guard=manager.session_mutation_guard,
                                          reservation_guard=manager.reserve_session)
        self.service = ContextCompressionService(self.sessions)
        return manager


class CompressionTests(CompressionFixture):
    def test_success_preserves_originals_other_sessions_and_returns_existing_snapshot(self):
        context_pack.prepare_injection('other', 'system', '', exclude_user=None, manual=True,
                                       complete=lambda _: 'other summary')
        before = memory.memory_path().read_bytes()
        other = context_pack.context_view('other', 'system')
        result = self.service.compress_context()
        self.assertTrue(result['ok'], result)
        self.assertEqual(set(result), {'ok', 'total', 'fact_limit', 'facts', 'context', 'items'})
        self.assertEqual(result['context']['summary'], 'compressed summary')
        self.assertEqual([r['text'] for r in result['items']], ['latest question', 'latest answer'])
        self.assertEqual(memory.memory_path().read_bytes(), before)
        self.assertEqual(context_pack.context_view('other', 'system'), other)
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn('tools', self.requests[0])
        self.assertNotIn('stream', self.requests[0])
        self.assertEqual(usage.summarize_usage()['total']['total_tokens'], 15)
        self.assertFalse(self.conversation.running)

    def test_recompression_includes_existing_summary_and_keeps_latest_exchange(self):
        self.old_pack()
        result = self.service.compress_context()
        self.assertTrue(result['ok'], result)
        prompt = self.requests[0]['messages'][1]['content']
        self.assertIn('old saved summary', prompt)
        self.assertIn('latest question', prompt)
        self.assertNotIn('newest question', prompt)
        self.assertEqual(len(result['context']['covered']), 4)
        self.assertEqual([r['text'] for r in result['items']], ['newest question', 'newest answer'])

    def test_incomplete_timeout_empty_and_tool_results_do_not_replace_summary(self):
        self.old_pack()
        before = context_pack.pack_path().read_bytes()
        original = memory.memory_path().read_bytes()
        cases = [('length', dict(role='assistant', content='truncated'), None),
                 ('content_filter', dict(role='assistant', content='filtered'), None),
                 (None, dict(role='assistant', content='unfinished'), None),
                 ('stop', dict(role='assistant', content='  '), None),
                 ('tool_calls', dict(role='assistant', content='text plus tool', tool_calls=[
                     dict(id='call', type='function', function=dict(name='remember_fact', arguments='{}'))]), None),
                 ('stop', dict(role='assistant', content='unused'), httpx.ReadTimeout('fixture timeout'))]
        for self.finish, self.reply, self.failure in cases:
            with self.subTest(finish=self.finish, reply=self.reply):
                result = self.service.compress_context()
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['change_state'], 'not_changed')
                self.assertEqual(context_pack.pack_path().read_bytes(), before)
                self.assertEqual(memory.memory_path().read_bytes(), original)
                self.assertFalse(self.conversation.running)
        self.assertEqual(len(self.requests), len(cases))  # No retry on timeout or incomplete output.

    def test_too_little_history_does_not_call_model_and_init_failure_releases_busy(self):
        memory.clear_session('selected')
        memory.append_chat('user', 'one question', 'selected')
        memory.append_chat('pet', 'one answer', 'selected')
        self.assertEqual(self.service.compress_context()['change_state'], 'not_changed')
        self.assertEqual(self.requests, [])
        self.conversation.engage_model.side_effect = RuntimeError('initialization failed')
        self.assertFalse(self.service.compress_context()['ok'])
        self.assertFalse(self.conversation.running)

    def test_usage_failure_and_save_failure_before_or_after_replace_report_progress(self):
        from desk_companion import context_compression
        self.old_pack()
        before = context_pack.pack_path().read_bytes()
        with patch.object(self.conversation, 'record_llm_usage', side_effect=OSError('usage failed')):
            result = self.service.compress_context()
        self.assertEqual(result['change_state'], 'not_changed')
        self.assertEqual(context_pack.pack_path().read_bytes(), before)
        with patch.object(context_compression, 'save_pack', side_effect=OSError('save failed')):
            result = self.service.compress_context()
        self.assertEqual(result['change_state'], 'unconfirmed')
        self.assertEqual(context_pack.pack_path().read_bytes(), before)
        original = context_compression.save_pack
        def save_then_fail(*args):
            original(*args)
            raise OSError('replace outcome unknown')
        with patch.object(context_compression, 'save_pack', side_effect=save_then_fail):
            result = self.service.compress_context()
        self.assertEqual(result['change_state'], 'unconfirmed')
        self.assertEqual(context_pack.context_view('selected', 'system')['summary'], 'compressed summary')
        self.assertFalse(self.conversation.running)

    def test_snapshot_failure_reports_committed_and_reservation_released(self):
        manager = self.manager()
        with patch.object(self.sessions, 'load_memory', side_effect=RuntimeError('refresh failed')):
            result = self.service.compress_context()
        self.assertEqual(result['change_state'], 'committed')
        self.assertEqual(context_pack.context_view('selected', 'system')['summary'], 'compressed summary')
        self.assertFalse(self.conversation.running)
        self.assertTrue(manager.submit('after cleanup', 'selected'))

    def test_queue_running_cancel_and_terminal_tail_reject_before_model(self):
        manager = self.manager()
        task = manager.submit('pending', 'selected')
        for status in ('queued', 'running', 'cancelling'):
            manager.update(task, state=status)
            self.assertFalse(self.service.compress_context()['ok'])
        manager.update(task, state='failed')
        manager.active[task] = dict(session='selected', cancel=threading.Event())
        try:
            self.assertFalse(self.service.compress_context()['ok'])
        finally:
            manager.active.pop(task)
        self.assertEqual(self.requests, [])
        self.conversation.running = True
        self.assertFalse(self.service.compress_context()['ok'])
        self.assertTrue(self.conversation.running)  # Must not clear someone else's busy state.
        self.conversation.running = False
        self.assertTrue(self.service.compress_context()['ok'])

    def test_during_model_only_current_session_and_shared_mutations_are_blocked(self):
        manager = self.manager()
        existing = manager.submit('same request', 'selected', submission_key='existing-key')
        manager.update(existing, state='succeeded')
        other = manager.submit('other queued', 'other')
        editor = MemoryEditingService(self.sessions)
        settings = AssistantSettingsService(self.conversation)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def waiting():
            entered.set()
            if not release.wait(7):
                raise RuntimeError('test did not release model')
        self.during_model = waiting
        result = []
        thread = threading.Thread(target=lambda: result.append(self.service.compress_context()))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertFalse(self.service.compress_context()['ok'])
            self.assertTrue(self.sessions.load_memory()['ok'])
            self.assertTrue(self.sessions.load_chat_log()['ok'])
            for call in (self.sessions.new_chat_session, self.sessions.clear_chat,
                         lambda: self.sessions.switch_chat_session('other'),
                         lambda: editor.add_fact('busy'),
                         lambda: editor.delete_memory_turn('user', 'old question'),
                         lambda: settings.save_persona(dict(persona='changed', max_steps=8, nudge_enabled=True)),
                         lambda: settings.use_model('other-model')):
                self.assertFalse(call()['ok'])
            with self.assertRaisesRegex(RuntimeError, '整理上下文'):
                manager.submit('rejected', 'selected')
            self.assertEqual(manager.submit('same request', 'selected', submission_key='existing-key'), existing)
            self.assertEqual(manager.get(other)['state'], 'queued')
            self.assertTrue(manager.cancel(other)['ok'])
            self.assertTrue(manager.submit('another session', 'third'))
            # Another thread can enter the real global write and queue locks.
            def independent_write():
                with WRITES, manager.cv:
                    memory.append_chat('pet', 'other completed', 'other')
            writer = threading.Thread(target=independent_write); writer.start(); writer.join(2)
            self.assertFalse(writer.is_alive())
        finally:
            release.set(); thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result[0]['ok'], result)
        self.assertTrue(manager.submit('after compression', 'selected'))

    def test_reservation_cleanup_after_failure_accepts_new_work(self):
        manager = self.manager()
        self.failure = httpx.ReadTimeout('fixture timeout')
        self.assertEqual(self.service.compress_context()['change_state'], 'not_changed')
        self.assertFalse(self.conversation.running)
        self.assertTrue(manager.submit('retry later', 'selected'))
        with self.assertRaises(RuntimeError):
            with manager.reserve_session('selected'):
                self.fail('queue must block reservation')

    def test_service_has_no_gui_imports(self):
        script = '''
import sys, unittest
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from tests.test_context_compression import CompressionTests
names = [n for n in unittest.defaultTestLoader.getTestCaseNames(CompressionTests)
         if n != 'test_service_has_no_gui_imports']
result = unittest.TextTestRunner().run(unittest.TestSuite(CompressionTests(n) for n in names))
assert_no_gui()
assert result.wasSuccessful()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, encoding='utf-8',
            cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'PYTHONUTF8': '1'}, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DesktopCompatibilityTests(CompressionFixture):
    def test_old_app_bridge_and_pet_busy_guard(self):
        from desk_companion.app import App
        from desk_companion.bridge import Bridge
        app = App.__new__(App)
        app.conversation = self.conversation
        app.pet = SimpleNamespace(busy=True)
        app.chat_sessions = ChatSessionService(self.conversation, mutation_guard=app._session_mutation_guard,
                                              reservation_guard=app._session_reservation)
        app.context_compression = ContextCompressionService(app.chat_sessions)
        self.assertFalse(app.board_compress_context()['ok'])
        self.assertEqual(self.requests, [])
        app.pet.busy = False
        self.assertTrue(Bridge(app).compress_context()['ok'])
        memory.append_chat('user', 'new question', 'selected')
        memory.append_chat('pet', 'new answer', 'selected')
        self.assertTrue(app.board_compress_context()['ok'])
