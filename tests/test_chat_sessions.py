"""Session service and task admission, using isolated data and controlled writes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desk_companion import context_pack, facts, memory, notebook, state
from desk_companion.chat_sessions import ChatSessionService
from desk_companion.conversation import ConversationService
from desk_companion.tasks import TaskManager


class SessionFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=folder.name)
        env.start(); self.addCleanup(env.stop)
        self.state = state.load_state(11, 22)
        self.state.session_id = 'selected'; self.state.save()
        self.conversation = ConversationService(self.state)
        self.conversation.agent = SimpleNamespace(memory=['stale model context'])
        self.service = ChatSessionService(self.conversation)
        memory.append_chat('user', 'selected question', 'selected')
        memory.append_chat('pet', 'selected answer', 'selected')
        memory.append_chat('user', 'other question', 'other')

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and 'locks' not in p.parts}

    def make_manager(self):
        # Disable scheduling only; use the real queue, condition lock and admission query.
        with patch.object(TaskManager, '_schedule', return_value=None):
            manager = TaskManager(path=self.root / 'tasks.sqlite3')
        self.addCleanup(manager.shutdown)
        self.service = ChatSessionService(self.conversation, mutation_guard=manager.session_mutation_guard)
        return manager



class SessionTests(SessionFixture):
    def test_new_switch_empty_reuse_and_saved_selection(self):
        created = self.service.new_chat_session()
        self.assertTrue(created['ok'], created)
        sid = created['session_id']
        self.assertNotIn(sid, ('selected', 'other'))
        self.assertEqual(state.load_state(0, 0).session_id, sid)
        self.assertEqual(self.conversation.agent.memory, [])
        self.assertEqual(self.service.new_chat_session()['session_id'], sid)
        self.assertEqual(self.service.switch_chat_session(' selected ')['items'][0]['text'], 'selected question')
        self.assertEqual(self.state.session_id, 'selected')
        before = self.snapshot()
        for invalid in ('unknown', '', None):
            self.assertFalse(self.service.switch_chat_session(invalid)['ok'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.service.load_chat_log()['session_id'], 'selected')

    def test_clear_removes_only_current_history_summary_and_agent_context(self):
        for sid in ('selected', 'other'):
            memory.append_chat('user', 'latest question', sid)
            memory.append_chat('pet', 'latest answer', sid)
            context_pack.prepare_injection(sid, 'system', '', exclude_user=None,
                                           manual=True, complete=lambda _: 'saved summary')
            self.assertTrue(context_pack.context_view(sid, 'system')['summary'])
        facts.add_user_fact('keep this long-term fact')
        book = notebook.new_notebook()
        notebook.save_note(book['session_id'], 'note question', 'note answer [1]',
                           [dict(n=1, doc='fixture document', title='section', text='retained source', doc_id='doc-1')])
        manager = self.make_manager()
        task = manager.submit('unrelated task', 'other')
        preserved = (memory.list_chat('other'), context_pack.context_view('other', 'system'),
                     facts.read_facts(), notebook.load_notebook(), manager.get(task))
        result = self.service.clear_chat()
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.state.session_id, 'selected')
        self.assertEqual(memory.list_chat('selected'), [])
        self.assertEqual(context_pack.context_view('selected', 'system')['summary'], '')
        self.assertEqual(self.conversation.agent.memory, [])
        self.assertEqual((memory.list_chat('other'), context_pack.context_view('other', 'system'),
                          facts.read_facts(), notebook.load_notebook(), manager.get(task)), preserved)
        self.assertEqual([row['id'] for row in self.service.load_chat_log()['sessions']], ['other'])
        self.assertTrue(self.service.clear_chat()['ok'])  # Clearing is repeatable.

    def test_busy_rejects_changes_but_allows_snapshots(self):
        before = self.snapshot()
        self.conversation.running = True
        for call in (self.service.new_chat_session, self.service.clear_chat,
                     lambda: self.service.switch_chat_session('other')):
            self.assertFalse(call()['ok'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.conversation.agent.memory, ['stale model context'])
        self.assertTrue(self.service.load_chat_log()['ok'])
        view = self.service.load_memory()
        self.assertTrue(view['ok'], view)
        self.assertEqual(view['total'], 2)
        self.assertIn('context', view)
        self.assertEqual(view['facts'], [])

    def test_failed_selection_save_restores_memory_and_reports_uncertain_file(self):
        before = self.snapshot()
        with patch.object(self.state, 'save', side_effect=OSError('fixture write failed')):
            for call in (self.service.new_chat_session, lambda: self.service.switch_chat_session('other')):
                result = call()
                self.assertFalse(result['ok'])
                self.assertEqual(result['change_state'], 'unconfirmed')
                self.assertEqual(self.state.session_id, 'selected')
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.conversation.agent.memory, ['stale model context'])

    def test_clear_failures_report_stage_without_claiming_rollback(self):
        before = memory.list_chat('selected')
        with patch.object(context_pack, 'drop_pack', side_effect=OSError('fixture summary failed')):
            result = self.service.clear_chat()
        self.assertEqual(result['change_state'], 'unconfirmed')
        self.assertEqual(memory.list_chat('selected'), before)
        with patch.object(memory, 'clear_session', side_effect=OSError('fixture history failed')):
            result = self.service.clear_chat()
        self.assertEqual(result['change_state'], 'partial')
        self.assertEqual(memory.list_chat('selected'), before)
        with patch.object(self.conversation, 'clear_agent_memory', side_effect=RuntimeError('fixture model memory failed')):
            result = self.service.clear_chat()
        self.assertEqual(result['change_state'], 'committed')
        self.assertEqual(memory.list_chat('selected'), [])
        self.assertTrue(self.service.clear_chat()['ok'])

    def test_queue_cancel_and_terminal_but_active_tail_all_block_current_session(self):
        manager = self.make_manager()
        task = manager.submit('pending question', 'selected')
        before = memory.list_chat('selected')
        for status in ('queued', 'running', 'cancelling'):
            manager.update(task, state=status)
            for call in (self.service.new_chat_session, self.service.clear_chat,
                         lambda: self.service.switch_chat_session('other')):
                self.assertFalse(call()['ok'], status)
            self.assertTrue(self.service.load_chat_log()['ok'])
            self.assertTrue(self.service.load_memory()['ok'])
            self.assertEqual(manager.get(task)['state'], status)
        manager.update(task, state='failed')
        manager.active[task] = dict(session='selected', cancel=threading.Event())
        try:
            self.assertFalse(self.service.clear_chat()['ok'])
            self.assertEqual(memory.list_chat('selected'), before)
        finally:
            manager.active.pop(task)
        self.assertTrue(self.service.clear_chat()['ok'])
        manager.submit('other pending', 'other')
        self.assertTrue(self.service.new_chat_session()['ok'])

    def test_guard_keeps_admission_out_until_session_mutation_finishes(self):
        manager = self.make_manager()
        attempting, submitted = threading.Event(), threading.Event()
        def submit():
            attempting.set()
            manager.submit('registered after clear', 'selected')
            submitted.set()
        with manager.session_mutation_guard('selected'):
            thread = threading.Thread(target=submit)
            thread.start()
            self.assertTrue(attempting.wait(2))
            self.assertFalse(submitted.wait(.1))
            self.assertTrue(self.service.clear_chat()['ok'])
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(submitted.is_set())
        self.assertFalse(self.service.clear_chat()['ok'])

    def test_no_gui_imports_while_exercising_real_service_and_admission(self):
        script = '''
import sys, unittest
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from tests.test_chat_sessions import SessionTests
names = [n for n in unittest.defaultTestLoader.getTestCaseNames(SessionTests)
         if n != 'test_no_gui_imports_while_exercising_real_service_and_admission']
result = unittest.TextTestRunner().run(unittest.TestSuite(SessionTests(n) for n in names))
assert_no_gui()
assert result.wasSuccessful()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, encoding='utf-8',
            cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'PYTHONUTF8': '1'}, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TailWriteTests(SessionFixture):
    def test_finished_worker_tail_is_blocked_until_real_partial_write_completes(self):
        command = [sys.executable, '-u', '-c',
            "import json,sys; json.loads(sys.stdin.readline()); "
            "print(json.dumps(dict(kind='token',data='partial answer')),flush=True); "
            "print(json.dumps(dict(kind='failure',data='fixture failure')),flush=True)"]
        manager = TaskManager(path=self.root / 'tasks.sqlite3', command=command)
        self.addCleanup(manager.shutdown)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = manager._save_partial
        def write_partial(row, reason):
            entered.set()
            if not release.wait(8):
                raise RuntimeError('test did not release final write')
            original(row, reason)
        service = ChatSessionService(self.conversation, mutation_guard=manager.session_mutation_guard)
        with patch.object(manager, '_save_partial', side_effect=write_partial):
            task = manager.submit('offline failure', 'selected')
            self.assertTrue(entered.wait(5))
            self.assertEqual(manager.get(task)['state'], 'failed')
            for call in (service.clear_chat, service.new_chat_session,
                         lambda: service.switch_chat_session('other')):
                self.assertFalse(call()['ok'])
            release.set()
            with manager.cv:
                deadline = time.monotonic() + 5
                while task in manager.active and time.monotonic() < deadline:
                    manager.cv.wait(.05)
                self.assertNotIn(task, manager.active)
            self.assertIn('partial answer', memory.list_chat('selected')[-1]['text'])
            self.assertTrue(service.clear_chat()['ok'])
        self.assertEqual(memory.list_chat('selected'), [])


class DesktopSessionTests(SessionFixture):
    # Only desktop-specific tests; base service tests are loaded separately above.
    def make_app(self):
        from desk_companion.app import App
        from desk_companion.bridge import Bridge
        app = App.__new__(App)
        app.state = self.state; app.conversation = self.conversation
        app.pet = SimpleNamespace(busy=False, bubble_open=False)
        app.pet.set_busy = lambda busy: setattr(app.pet, 'busy', busy)
        app.pet.review = lambda: app.pet.set_busy(False)
        app.pet.fail = lambda: app.pet.set_busy(False)
        app.pet.wave = Mock()
        app._card_mode = 'chat'
        app._eval_bubble = Mock(); app._eval_board = Mock(); app.show_bubble = Mock()
        app.chat_sessions = ChatSessionService(self.conversation, mutation_guard=app._session_mutation_guard)
        app.bridge = Bridge(app, session_changed=app._on_session_changed)
        app.engage_model = Mock(); app.inject_prepared = Mock(); app._record_usage = Mock()
        return app

    def test_desktop_views_refresh_after_commit_and_report_refresh_failure(self):
        app = self.make_app()
        app.pet.busy = True
        self.assertFalse(app.clear_chat_history()['ok'])
        app.pet.busy = False
        result = app.switch_chat_session('other')
        self.assertTrue(result['ok'], result)
        app._eval_board.assert_called_with('load_thread', items=memory.list_chat('other'),
            session_id='other', sessions=memory.list_sessions())
        with patch.object(app.bridge, '_session_changed', side_effect=RuntimeError('fixture UI failed')):
            result = app.clear_chat_history()
        self.assertEqual(result['change_state'], 'committed')
        self.assertIn('界面刷新失败', result['error'])
        self.assertEqual(memory.list_chat('other'), [])

    def test_busy_spans_desktop_start_and_queued_history_callback(self):
        app = self.make_app()
        callbacks = []
        app.ui = callbacks.append
        with patch('desk_companion.app.threading.Thread'):
            app.send_chat('desktop question')
        self.assertTrue(app._agent_running)
        self.assertFalse(app.chat_sessions.clear_chat()['ok'])
        self.conversation.run_agent = Mock(return_value='desktop answer')
        app._run_agent('desktop question')
        self.assertTrue(app._agent_running)
        self.assertFalse(app.chat_sessions.new_chat_session()['ok'])
        original = memory.append_chat
        def append(*args, **kwargs):
            self.assertFalse(app.pet.busy)  # review() already cleared the visual flag.
            self.assertFalse(app.chat_sessions.clear_chat()['ok'])
            return original(*args, **kwargs)
        with patch('desk_companion.app.append_chat', side_effect=append):
            callbacks.pop()()
        self.assertFalse(app._agent_running)
        self.assertEqual(memory.list_chat('selected')[-1]['text'], 'desktop answer')
        self.assertTrue(app.chat_sessions.clear_chat()['ok'])

    def test_initialization_error_callback_releases_busy_after_delayed_dispatch(self):
        app = self.make_app()
        callbacks = []; app.ui = callbacks.append
        app.engage_model.side_effect = RuntimeError('fixture initialization failed')
        self.conversation.running = True
        app._run_agent('question')
        self.assertTrue(app._agent_running)
        callbacks.pop()()
        self.assertFalse(app._agent_running)

    def test_daily_reply_stays_busy_until_history_callback(self):
        app = self.make_app()
        callbacks = []
        started = False
        def dispatch(fn):
            nonlocal started
            if not started:
                started = True
                fn()
            else:
                callbacks.append(fn)
        app.ui = dispatch
        app.pet.visible = lambda: True
        self.conversation.agent.run = Mock(return_value='daily answer')
        app._run_daily()
        self.assertTrue(app._agent_running)
        self.assertFalse(app.chat_sessions.clear_chat()['ok'])
        callbacks.pop()()
        self.assertFalse(app._agent_running)
        self.assertEqual(memory.list_chat('selected')[-1]['text'], 'daily answer')


if __name__ == '__main__':
    unittest.main()
