"""Manual edits with real storage, task admission and isolated fault injection."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desk_companion import context_pack, facts, memory, state
from desk_companion.chat_sessions import ChatSessionService
from desk_companion.conversation import ConversationService
from desk_companion.memory_editing import MemoryEditingService
from desk_companion.tasks import TaskManager


class MemoryFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=folder.name)
        env.start(); self.addCleanup(env.stop)
        self.state = state.load_state(0, 0)
        self.state.session_id = 'selected'
        self.conversation = ConversationService(self.state)
        self.conversation.agent = SimpleNamespace(memory=['old context'])
        self.sessions = ChatSessionService(self.conversation)
        self.editor = MemoryEditingService(self.sessions)
        for sid in ('selected', 'other'):
            for role, text in (('user', 'old question'), ('pet', 'old answer'),
                               ('user', 'latest question'), ('pet', 'latest answer')):
                memory.append_chat(role, text, sid)

    def compress(self, sid='selected'):
        context_pack.prepare_injection(sid, 'system', '', exclude_user=None, manual=True,
                                       complete=lambda _: 'summary of old question')

    def make_manager(self):
        with patch.object(TaskManager, '_schedule', return_value=None):
            manager = TaskManager(path=self.root / 'tasks.sqlite3')
        self.addCleanup(manager.shutdown)
        self.sessions = ChatSessionService(self.conversation, mutation_guard=manager.session_mutation_guard)
        self.editor = MemoryEditingService(self.sessions)
        return manager


class MemoryEditingTests(MemoryFixture):
    def test_fact_contracts_sources_noop_and_cross_session_scope(self):
        with facts.bind_turn_user('my name is Ada'):
            self.assertIn('已记下', facts.remember_fact(dict(text='name: Ada', quote='my name is Ada')))
        original = facts.read_facts()[0]
        self.assertTrue(self.editor.update_fact(original['id'], original['text'])['ok'])
        self.assertEqual(facts.read_facts()[0], original)
        result = self.editor.update_fact(original['id'], ' name: Grace ')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['facts'][0]['writer'], 'user')
        self.assertEqual(result['facts'][0]['quote'], '')
        self.assertEqual(result['facts'][0]['text'], 'name: Grace')
        self.assertTrue(self.sessions.switch_chat_session('other')['ok'])
        self.assertEqual(self.sessions.load_memory()['facts'], result['facts'])
        self.assertTrue(self.editor.add_fact('x' * facts.FACT_MAX)['ok'])
        self.assertTrue(self.editor.delete_fact(original['id'])['ok'])
        self.assertEqual(len(facts.read_facts()), 1)
        self.assertEqual(len(memory.list_chat('selected')), 4)

    def test_fact_validation_and_limits_leave_data_unchanged(self):
        self.editor.add_fact('existing')
        fact_id = facts.read_facts()[0]['id']
        before = facts.facts_path().read_bytes()
        for call in (lambda: self.editor.add_fact(None), lambda: self.editor.add_fact(' '),
                     lambda: self.editor.add_fact('x' * (facts.FACT_MAX + 1)),
                     lambda: self.editor.add_fact(' existing '),
                     lambda: self.editor.update_fact('', 'changed'),
                     lambda: self.editor.update_fact(fact_id, False),
                     lambda: self.editor.delete_fact('missing')):
            result = call()
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['change_state'], 'not_changed')
            self.assertEqual(facts.facts_path().read_bytes(), before)
        for number in range(facts.FACT_LIMIT - 1):
            self.assertTrue(self.editor.add_fact(f'fact {number}')['ok'])
        self.assertFalse(self.editor.add_fact('over limit')['ok'])
        self.assertEqual(len(facts.read_facts()), facts.FACT_LIMIT)
        self.assertFalse(self.editor.update_fact(facts.read_facts()[1]['id'], 'existing')['ok'])

    def test_shared_reply_blocks_all_edits_but_background_tasks_only_block_history(self):
        manager = self.make_manager()
        fact_id = self.editor.add_fact('before')['facts'][0]['id']
        calls = (lambda: self.editor.add_fact('new'), lambda: self.editor.update_fact(fact_id, 'changed'),
                 lambda: self.editor.delete_fact(fact_id),
                 lambda: self.editor.delete_memory_turn('user', 'old question'))
        self.conversation.running = True
        for call in calls:
            self.assertEqual(call()['change_state'], 'not_changed')
        self.assertEqual(facts.read_facts()[0]['text'], 'before')
        self.assertEqual(len(memory.list_chat('selected')), 4)
        self.conversation.running = False
        task = manager.submit('pending', 'selected')
        for status in ('queued', 'running', 'cancelling'):
            manager.update(task, state=status)
            self.assertFalse(self.editor.delete_memory_turn('user', 'old question')['ok'])
            self.assertTrue(self.editor.update_fact(fact_id, status)['ok'])
            self.assertEqual(manager.get(task)['state'], status)
        manager.update(task, state='failed')
        manager.active[task] = dict(session='selected', cancel=threading.Event())
        try:
            self.assertFalse(self.editor.delete_memory_turn('user', 'old question')['ok'])
            self.assertTrue(self.editor.add_fact('allowed during tail')['ok'])
            self.assertTrue(self.editor.delete_fact(fact_id)['ok'])
        finally:
            manager.active.pop(task)
        other_task = manager.submit('other task', 'other')
        self.assertTrue(self.editor.delete_memory_turn('user', 'old question')['ok'])
        self.assertEqual(manager.get(other_task)['state'], 'queued')

    def test_duplicate_history_delete_invalidates_only_covering_summary(self):
        self.compress(); self.compress('other')
        other = (memory.list_chat('other'), context_pack.context_view('other', 'system'))
        facts.add_user_fact('keep fact')
        memory.append_chat('user', 'old question', 'selected')
        memory.append_chat('pet', 'old question', 'selected')
        result = self.editor.delete_memory_turn('user', ' old question ')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['total'], 4)
        self.assertFalse(any(r['role'] == 'user' and r['text'] == 'old question'
                             for r in memory.list_chat('selected')))
        self.assertIn('old question', [r['text'] for r in memory.list_chat('selected') if r['role'] == 'pet'])
        self.assertEqual(result['context']['summary'], '')
        self.assertEqual(self.conversation.agent.memory, [])
        self.assertEqual((memory.list_chat('other'), context_pack.context_view('other', 'system')), other)
        self.assertEqual(facts.read_facts()[0]['text'], 'keep fact')

    def test_uncovered_or_invalid_turn_does_not_drop_summary(self):
        self.compress()
        before = context_pack.context_view('selected', 'system')['summary']
        history = memory.list_chat('selected')
        for role, text in (('system', 'old question'), ('user', ''), ('user', None), ('user', 'missing')):
            self.assertEqual(self.editor.delete_memory_turn(role, text)['change_state'], 'not_changed')
            self.assertEqual(memory.list_chat('selected'), history)
            self.assertEqual(context_pack.context_view('selected', 'system')['summary'], before)
        self.assertTrue(self.editor.delete_memory_turn('user', 'latest question')['ok'])
        self.assertEqual(context_pack.context_view('selected', 'system')['summary'], before)

    def test_summary_and_history_write_failures_report_actual_progress(self):
        self.compress()
        before = memory.list_chat('selected')
        with patch.object(context_pack, 'drop_pack_if_covered', side_effect=OSError('summary failure')):
            result = self.editor.delete_memory_turn('user', 'old question')
        self.assertEqual(result['change_state'], 'unconfirmed')
        self.assertEqual(memory.list_chat('selected'), before)
        with patch.object(memory, 'drop_session_turn', side_effect=OSError('history failure')):
            result = self.editor.delete_memory_turn('user', 'old question')
            self.assertEqual(result['change_state'], 'partial')
            self.assertEqual(context_pack.context_view('selected', 'system')['summary'], '')
            self.assertEqual(memory.list_chat('selected'), before)
            result = self.editor.delete_memory_turn('user', 'latest question')
            self.assertEqual(result['change_state'], 'unconfirmed')

    def test_fact_write_uncertainty_and_committed_refresh_failure(self):
        original = facts._write_unlocked
        def write_then_fail(items):
            original(items)
            raise OSError('replace result unknown')
        with patch.object(facts, '_write_unlocked', side_effect=write_then_fail):
            result = self.editor.add_fact('saved but unconfirmed')
        self.assertEqual(result['change_state'], 'unconfirmed')
        self.assertEqual(facts.read_facts()[0]['text'], 'saved but unconfirmed')
        for failure in (dict(ok=False, error='bad snapshot'), RuntimeError('bad snapshot')):
            kwargs = dict(side_effect=failure) if isinstance(failure, Exception) else dict(return_value=failure)
            with patch.object(self.sessions, 'load_memory', **kwargs):
                result = self.editor.update_fact(facts.read_facts()[0]['id'], 'updated despite refresh failure')
            self.assertEqual(result['change_state'], 'committed')
        self.assertEqual(facts.read_facts()[0]['text'], 'updated despite refresh failure')
        with patch.object(self.conversation, 'clear_agent_memory', side_effect=RuntimeError('agent failure')):
            result = self.editor.delete_memory_turn('user', 'old question')
        self.assertEqual(result['change_state'], 'committed')
        self.assertFalse(any(r['text'] == 'old question' for r in memory.list_chat('selected')))

    def test_history_edit_blocks_new_submission_and_selection_until_finished(self):
        manager = self.make_manager()
        entered, release, attempted, submitted, switched = [threading.Event() for _ in range(5)]
        self.addCleanup(release.set)
        original = memory.drop_session_turn
        def delayed(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test did not release history edit')
            original(*args)
        result = []
        def submit():
            attempted.set(); manager.submit('after edit', 'selected'); submitted.set()
        def switch():
            self.sessions.switch_chat_session('other'); switched.set()
        with patch.object(memory, 'drop_session_turn', side_effect=delayed):
            edit = threading.Thread(target=lambda: result.append(self.editor.delete_memory_turn('user', 'old question')))
            edit.start()
            self.assertTrue(entered.wait(2))
            writer = threading.Thread(target=submit); writer.start()
            selector = threading.Thread(target=switch); selector.start()
            self.assertTrue(attempted.wait(2))
            self.assertFalse(submitted.wait(.1)); self.assertFalse(switched.is_set())
            release.set()
            for thread in (edit, writer, selector):
                thread.join(3); self.assertFalse(thread.is_alive())
        self.assertTrue(result[0]['ok'], result)
        self.assertTrue(submitted.is_set()); self.assertTrue(switched.is_set())
        self.assertEqual(len(memory.list_chat('selected')), 3)

    def test_service_has_no_gui_imports(self):
        script = '''
import sys, unittest
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from tests.test_memory_editing import MemoryEditingTests
names = [n for n in unittest.defaultTestLoader.getTestCaseNames(MemoryEditingTests)
         if n != 'test_service_has_no_gui_imports']
result = unittest.TextTestRunner().run(unittest.TestSuite(MemoryEditingTests(n) for n in names))
assert_no_gui()
assert result.wasSuccessful()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, encoding='utf-8',
            cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'PYTHONUTF8': '1'}, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class CompatibilityTests(MemoryFixture):
    def test_old_app_and_bridge_use_shared_editor(self):
        from desk_companion.app import App
        from desk_companion.bridge import Bridge
        app = App.__new__(App)
        app.memory_editing = self.editor
        bridge = Bridge(app)
        added = app.board_add_fact('legacy fact')
        fact_id = added['facts'][0]['id']
        self.assertTrue(bridge.update_fact(fact_id, 'new text')['ok'])
        self.assertTrue(app.board_update_fact(fact_id, 'legacy update')['ok'])
        self.assertTrue(app.board_drop_memory_turn('user', 'old question')['ok'])
        self.assertTrue(bridge.delete_memory_turn('pet', 'old answer')['ok'])
        self.assertTrue(app.board_delete_fact(fact_id)['ok'])
        self.assertTrue(bridge.add_fact('new fact')['ok'])
        self.assertTrue(bridge.delete_fact(facts.read_facts()[0]['id'])['ok'])


class TailWriteTests(MemoryFixture):
    def test_real_failed_worker_tail_blocks_history_edit_until_write_finishes(self):
        command = [sys.executable, '-u', '-c',
            "import json,sys; json.loads(sys.stdin.readline()); "
            "print(json.dumps(dict(kind='token',data='partial answer')),flush=True); "
            "print(json.dumps(dict(kind='failure',data='failure')),flush=True)"]
        manager = TaskManager(path=self.root / 'tasks.sqlite3', command=command)
        self.addCleanup(manager.shutdown)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = manager._save_partial
        def write_partial(row, reason):
            entered.set()
            if not release.wait(8):
                raise RuntimeError('test did not release worker tail')
            original(row, reason)
        editor = MemoryEditingService(ChatSessionService(self.conversation,
                                      mutation_guard=manager.session_mutation_guard))
        with patch.object(manager, '_save_partial', side_effect=write_partial):
            task = manager.submit('offline failure', 'selected')
            self.assertTrue(entered.wait(5))
            self.assertEqual(manager.get(task)['state'], 'failed')
            self.assertFalse(editor.delete_memory_turn('user', 'old question')['ok'])
            self.assertTrue(editor.add_fact('allowed during worker tail')['ok'])
            release.set()
            with manager.cv:
                deadline = time.monotonic() + 5
                while task in manager.active and time.monotonic() < deadline:
                    manager.cv.wait(.05)
                self.assertNotIn(task, manager.active)
            self.assertTrue(editor.delete_memory_turn('user', 'old question')['ok'])
        self.assertIn('partial answer', memory.list_chat('selected')[-1]['text'])
