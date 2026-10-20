"""Notebook contracts, isolated storage failures and process concurrency."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desk_companion import knowledge, notebook
from desk_companion.chat_sessions import ChatSessionService
from desk_companion.notebook_service import NotebookService


CITES = [dict(n=1, doc='source', title='section', text='source text', doc_id='doc-1')]


class NotebookFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=folder.name)
        env.start(); self.addCleanup(env.stop)
        view = patch.object(knowledge, 'public_view', return_value=dict(docs=[dict(id='doc-1')]))
        view.start(); self.addCleanup(view.stop)
        self.conversation = SimpleNamespace(running=False, state=SimpleNamespace(session_id='ordinary'))
        self.sessions = ChatSessionService(self.conversation)
        self.service = NotebookService(self.sessions)

    def new(self):
        result = self.service.new_notebook()
        self.assertTrue(result['ok'], result)
        return result['session_id']

    def save(self, sid, question='question'):
        result = self.service.save_notebook_note(sid, question, 'answer [1]', CITES)
        self.assertTrue(result['ok'], result)
        return result['note_id']


class NotebookTests(NotebookFixture):
    def test_create_save_load_and_delete_preserve_other_records_and_sources(self):
        self.assertEqual(self.service.load_notebook(), dict(ok=True, docs=[dict(id='doc-1')], sessions=[]))
        self.assertFalse(notebook._path().exists())
        sid, other = self.new(), self.new()
        self.assertNotEqual(sid, other)  # Empty notebooks are deliberately not reused.
        nid, keep = self.save(sid), self.save(other, 'keep')
        notebook.begin_notebook_turn(sid, 'question')
        notebook.finish_notebook_turn(sid, 'answer [1]', CITES)
        path = Path(notebook.write_note_markdown(sid, nid)['path'])
        source = self.root / 'original.md'
        source.write_text('keep source', encoding='utf-8')
        before = notebook._read()
        result = self.service.delete_notebook_note(sid, nid)
        self.assertTrue(result['ok'], result)
        self.assertFalse(path.exists())
        current = notebook._read()
        self.assertEqual(notebook._find(current, sid)['turns'], notebook._find(before, sid)['turns'])
        self.assertEqual(notebook._find(current, other), notebook._find(before, other))
        self.assertEqual(notebook._find(current, other)['notes'][0]['id'], keep)
        self.assertEqual(source.read_text(encoding='utf-8'), 'keep source')
        self.assertEqual(self.conversation.state.session_id, 'ordinary')
        self.assertFalse(self.conversation.running)

    def test_duplicate_content_is_saved_again_and_citations_are_snapshots(self):
        sid = self.new()
        first, second = self.save(sid), self.save(sid)
        self.assertNotEqual(first, second)
        with patch.object(knowledge, 'public_view', return_value=dict(docs=[])):
            page = self.service.load_notebook()
        self.assertTrue(page['ok'])
        self.assertEqual(len(page['sessions'][0]['notes']), 2)
        self.assertEqual(page['sessions'][0]['notes'][0]['cites'], CITES)

    def test_busy_writes_fail_without_releasing_someone_elses_reservation(self):
        sid = self.new(); nid = self.save(sid)
        before = notebook._path().read_bytes()
        self.conversation.running = True
        calls = [self.service.new_notebook,
                 lambda: self.service.save_notebook_note(sid, 'new', 'answer', CITES),
                 lambda: self.service.delete_notebook_note(sid, nid)]
        for call in calls:
            result = call()
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['change_state'], 'not_changed')
            self.assertTrue(self.conversation.running)
        self.assertTrue(self.service.load_notebook()['ok'])
        self.assertEqual(notebook._path().read_bytes(), before)

    def test_validation_failures_leave_notebook_and_busy_state_unchanged(self):
        sid = self.new(); nid = self.save(sid)
        before = notebook._path().read_bytes()
        for call in [lambda: self.service.save_notebook_note(sid, '', 'answer', CITES),
                     lambda: self.service.save_notebook_note(sid, 'q', '', CITES),
                     lambda: self.service.save_notebook_note(sid, 'q', 'answer', []),
                     lambda: self.service.save_notebook_note('missing', 'q', 'answer', CITES),
                     lambda: self.service.delete_notebook_note(sid, ''),
                     lambda: self.service.delete_notebook_note(sid, 'missing'),
                     lambda: self.service.delete_notebook_note('missing', nid)]:
            result = call()
            self.assertEqual(result['change_state'], 'not_changed', result)
            self.assertEqual(notebook._path().read_bytes(), before)
            self.assertFalse(self.conversation.running)

    def test_knowledge_failure_does_not_hide_committed_save_or_delete(self):
        sid = self.new(); before = notebook._path().read_bytes()
        with patch.object(knowledge, 'public_view', side_effect=OSError('knowledge unavailable')):
            self.assertFalse(self.service.load_notebook()['ok'])
            self.assertEqual(self.service.new_notebook()['change_state'], 'not_changed')
            self.assertEqual(notebook._path().read_bytes(), before)
            saved = self.service.save_notebook_note(sid, 'question', 'answer', CITES)
            self.assertEqual(saved['change_state'], 'committed', saved)
            self.assertIn('不要重复', saved['error'])
            nid = saved['note_id']
            self.assertEqual(notebook._read()['sessions'][0]['notes'][0]['id'], nid)
            deleted = self.service.delete_notebook_note(sid, nid)
            self.assertEqual(deleted['change_state'], 'committed', deleted)
            self.assertEqual(deleted['note_id'], nid)
            self.assertEqual(notebook._read()['sessions'][0]['notes'], [])
        self.assertFalse(self.conversation.running)

    def test_replace_failure_preserves_previous_json_for_new_save_and_delete(self):
        sid = self.new(); nid = self.save(sid)
        path = Path(notebook.write_note_markdown(sid, nid)['path'])
        before = notebook._path().read_bytes()
        replace = Path.replace

        def fail_replace(source, target):
            if Path(target) == notebook._path():
                raise OSError('replace failed')
            return replace(source, target)

        with patch.object(Path, 'replace', fail_replace):
            for call in [self.service.new_notebook,
                         lambda: self.service.save_notebook_note(sid, 'new', 'answer', CITES),
                         lambda: self.service.delete_notebook_note(sid, nid)]:
                result = call()
                self.assertEqual(result['change_state'], 'unconfirmed', result)
                self.assertEqual(notebook._path().read_bytes(), before)
                self.assertFalse(self.conversation.running)
        self.assertFalse(path.exists())  # Generated file removal is not rolled back.
        self.assertIn(path.name, result['removed_files'])
        self.assertFalse(list(notebook._path().parent.glob('*.tmp')))

    def test_failure_after_replace_reports_uncertainty_with_recoverable_identity(self):
        from desk_companion import video
        sid = self.new()
        write = video.atomic_write

        def write_then_fail(path, value):
            write(path, value)
            raise OSError('completion not acknowledged')

        with patch.object(video, 'atomic_write', side_effect=write_then_fail):
            result = self.service.save_notebook_note(sid, 'question', 'answer', CITES)
        self.assertEqual(result['change_state'], 'unconfirmed', result)
        self.assertEqual(notebook._read()['sessions'][0]['notes'][0]['id'], result['note_id'])
        self.assertFalse(self.conversation.running)

    def test_partial_file_cleanup_keeps_record_and_can_be_retried(self):
        sid = self.new(); nid = self.save(sid)
        first = Path(notebook.write_note_markdown(sid, nid)['path'])
        second = first.with_name('second.md')
        second.write_text('second export', encoding='utf-8')
        with notebook._LOCK:
            book = notebook._read()
            note = notebook._find_note(notebook._find(book, sid), nid)
            note['files'].append(dict(name=second.name, path=str(second), saved=notebook._now()))
            notebook._write(book)
        before = notebook._path().read_bytes()
        unlink = Path.unlink

        def fail_second(path, *args, **kwargs):
            if path == second:
                raise PermissionError('file is open')
            return unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', fail_second):
            result = self.service.delete_notebook_note(sid, nid)
        self.assertEqual(result['change_state'], 'partial', result)
        self.assertEqual(result['removed_files'], [first.name])
        self.assertEqual(result['failed_file'], second.name)
        self.assertEqual(result['session_id'], sid)
        self.assertEqual(result['note_id'], nid)
        self.assertEqual(notebook._path().read_bytes(), before)
        self.assertFalse(first.exists()); self.assertTrue(second.exists())
        self.assertTrue(self.service.delete_notebook_note(sid, nid)['ok'])
        self.assertFalse(second.exists())

    def test_duplicate_ids_and_external_file_metadata_fail_before_removal(self):
        sid = self.new(); nid = self.save(sid)
        path = Path(notebook.write_note_markdown(sid, nid)['path'])
        book = notebook._read()
        book['sessions'][0]['notes'].append(dict(book['sessions'][0]['notes'][0]))
        notebook._write(book)
        before = notebook._path().read_bytes()
        self.assertEqual(self.service.delete_notebook_note(sid, nid)['change_state'], 'not_changed')
        self.assertEqual(notebook._path().read_bytes(), before)
        self.assertTrue(path.exists())
        outside = self.root / 'source.md'
        outside.write_text('keep', encoding='utf-8')
        book['sessions'][0]['notes'].pop()
        book['sessions'][0]['notes'][0]['files'] = [dict(name=outside.name, path=str(outside), saved='now')]
        notebook._write(book)
        self.assertEqual(self.service.delete_notebook_note(sid, nid)['change_state'], 'not_changed')
        self.assertEqual(outside.read_text(encoding='utf-8'), 'keep')

    def test_service_can_be_used_without_gui_imports(self):
        script = '''
import sys, unittest
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from tests.test_notebook_service import NotebookTests
names = [name for name in unittest.defaultTestLoader.getTestCaseNames(NotebookTests)
         if name != 'test_service_can_be_used_without_gui_imports']
result = unittest.TextTestRunner().run(unittest.TestSuite(NotebookTests(name) for name in names))
assert_no_gui()
assert result.wasSuccessful()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'}, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class NotebookProcessTests(NotebookFixture):
    def start_worker(self, role, sid, nid):
        process = subprocess.Popen([sys.executable, '-m', 'tests.notebook_worker_fixture', role, sid, nid],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'})
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        return process

    def wait_ready(self, role):
        deadline = time.monotonic() + 15
        while not (self.root / (role + '.ready')).exists():
            if time.monotonic() > deadline:
                self.fail('worker did not become ready: ' + role)
            time.sleep(.01)

    def test_store_lock_protects_all_existing_writers_across_processes(self):
        sid = self.new(); nid = self.save(sid)
        roles = ('save', 'history', 'artifact', 'export')
        processes = [self.start_worker(role, sid, nid) for role in roles]
        for role in roles:
            self.wait_ready(role)
        (self.root / 'go').touch()
        for process in processes:
            out, err = process.communicate(timeout=35)
            self.assertEqual(process.returncode, 0, out + err)
        book = notebook._read()
        session = notebook._find(book, sid)
        self.assertEqual(len(session['notes']), 11)
        self.assertEqual(len(session['turns']), 20)
        self.assertTrue(Path(notebook._find_note(session, nid)['files'][0]['path']).is_file())
        artifact = next(s for s in book['sessions'] if s['id'] != sid)
        self.assertEqual(len(artifact['notes']), 10)

    def test_reader_waits_for_short_storage_lock_in_another_process(self):
        sid = self.new(); nid = self.save(sid)
        with notebook._LOCK:
            process = self.start_worker('read', sid, nid)
            self.wait_ready('read')
            (self.root / 'go').touch()
            with self.assertRaises(subprocess.TimeoutExpired):
                process.wait(timeout=.3)
        out, err = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, out + err)


class NotebookDesktopCompatibilityTests(NotebookFixture):
    def test_bridge_uses_service_and_app_facade_has_no_spurious_gui_refresh(self):
        from desk_companion.app import App
        from desk_companion.bridge import Bridge
        app = App.__new__(App)
        app.conversation = self.conversation
        app.chat_sessions = self.sessions
        app.pet = SimpleNamespace(busy=False)
        app.notebooks = NotebookService(self.sessions, start_guard=app._notebook_start_guard)
        bridge = Bridge(app)
        with patch.object(app, 'board_new_notebook', side_effect=AssertionError('Bridge must use service')):
            page = bridge.new_notebook()
        self.assertTrue(page['ok'], page)
        self.assertTrue(app.board_load_notebook()['ok'])
        sid = page['session_id']
        saved = bridge.save_notebook_note(sid, 'q', 'answer', CITES)
        self.assertTrue(saved['ok'], saved)
        app.pet.busy = True
        self.assertEqual(app.board_new_notebook()['change_state'], 'not_changed')
        self.assertEqual(bridge.delete_notebook_note(sid, saved['note_id'])['change_state'], 'not_changed')
        self.assertTrue(bridge.load_notebook()['ok'])
        app.pet.busy = False
        self.assertTrue(app.board_delete_notebook_note(sid, saved['note_id'])['ok'])


if __name__ == '__main__':
    unittest.main()
