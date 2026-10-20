"""Notebook output operations with fake cloud/platform and isolated files."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from desk_companion import knowledge, notebook
from desk_companion.notebook_platform import NotebookPlatform
from desk_companion.notebook_service import NotebookService
from tests.test_notebook_service import NotebookFixture


class NotebookFileTests(NotebookFixture):
    def setUp(self):
        super().setUp()
        self.platform = Mock()
        self.create = Mock(return_value=dict(ok=True, url='https://example.invalid/doc/1', document_id='doc-1'))
        self.service = NotebookService(self.sessions, platform=self.platform, create_document=self.create)
        self.sid = self.new()
        self.nid = self.save(self.sid)

    def export(self):
        result = self.service.export_notebook_markdown(self.sid, self.nid)
        self.assertTrue(result['ok'], result)
        return Path(result['path'])

    def test_markdown_overwrites_and_cleans_older_registered_outputs(self):
        path = self.export()
        self.assertIn('source text', path.read_text(encoding='utf-8'))
        path.write_text('overwrite this', encoding='utf-8')
        old = path.with_name('older.md'); old.write_text('old', encoding='utf-8')
        with notebook._LOCK:
            book = notebook._read()
            note = notebook._find_note(notebook._find(book, self.sid), self.nid)
            note['files'].append(dict(name=old.name, path=str(old), saved=notebook._now()))
            notebook._write(book)
        self.assertEqual(self.export(), path)
        self.assertFalse(old.exists())
        self.assertNotIn('overwrite this', path.read_text(encoding='utf-8'))
        note = notebook._read()['sessions'][0]['notes'][0]
        self.assertEqual(len(note['files']), 1)
        self.assertEqual(self.platform.open.call_count, 2)

    def test_feishu_creates_each_time_without_registering_remote_files(self):
        before = notebook._path().read_bytes()
        for _ in range(2):
            result = self.service.export_notebook_feishu(self.sid, self.nid)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['document_id'], 'doc-1')
            self.assertEqual(result['steps'], dict(creation='confirmed', open='requested'))
        self.assertEqual(self.create.call_count, 2)
        self.assertEqual(notebook._path().read_bytes(), before)
        self.assertIn('source text', self.create.call_args.args[1])

    def test_open_export_failure_preserves_confirmed_path_url_and_page(self):
        self.platform.open.side_effect = OSError('launch refused')
        for method, key in ((self.service.export_notebook_markdown, 'path'),
                            (self.service.export_notebook_feishu, 'url')):
            with self.subTest(key=key):
                result = method(self.sid, self.nid)
                self.assertFalse(result['ok'])
                self.assertEqual(result['change_state'], 'committed')
                self.assertEqual(result['stage'], 'open')
                self.assertEqual(result['steps']['open'], 'failed')
                self.assertIn(result[key], result['error'])
                self.assertFalse(self.conversation.running)
                if key == 'path':
                    self.assertTrue(Path(result[key]).exists())
                    self.assertIn('sessions', result)
                else:
                    self.assertEqual(result['document_id'], 'doc-1')

    def test_creation_timeout_or_invalid_response_is_unconfirmed_and_not_retried(self):
        for effect in (TimeoutError('timeout'), RuntimeError('invalid JSON'), dict(ok=True)):
            self.create.reset_mock()
            self.create.side_effect = effect if isinstance(effect, Exception) else None
            self.create.return_value = effect
            result = self.service.export_notebook_feishu(self.sid, self.nid)
            self.assertEqual(result['change_state'], 'unconfirmed', result)
            self.assertEqual(result['stage'], 'creation')
            self.assertIn('不要直接重复导出', result['error'])
            self.assertEqual(self.create.call_count, 1)
            self.assertFalse(self.conversation.running)
        self.platform.open.assert_not_called()

    def test_markdown_write_or_cleanup_failure_preserves_registration(self):
        path = self.export()
        before = notebook._path().read_bytes()
        with patch.object(Path, 'write_text', side_effect=OSError('disk full')):
            result = self.service.export_notebook_markdown(self.sid, self.nid)
        self.assertEqual(result['stage'], 'generation', result)
        self.assertEqual(result['steps']['generation'], 'unconfirmed')
        self.assertEqual(notebook._path().read_bytes(), before)
        old = path.with_name('old.md'); old.write_text('old', encoding='utf-8')
        book = notebook._read(); book['sessions'][0]['notes'][0]['files'].append(
            dict(name=old.name, path=str(old), saved=notebook._now()))
        notebook._write(book)
        before = notebook._path().read_bytes()
        with patch.object(Path, 'unlink', side_effect=OSError('locked')):
            result = self.service.export_notebook_markdown(self.sid, self.nid)
        self.assertEqual(result['stage'], 'cleanup', result)
        self.assertEqual(result['change_state'], 'partial')
        self.assertEqual(notebook._path().read_bytes(), before)
        self.assertTrue(old.exists())
        self.assertEqual(self.platform.open.call_count, 1)

    def test_file_registration_failure_and_post_commit_refresh_failure(self):
        path = self.export()
        for operation in ('export', 'delete'):
            if not path.exists():
                path = self.export()
            call = (lambda: self.service.export_notebook_markdown(self.sid, self.nid)) if operation == 'export' else (
                lambda: self.service.delete_notebook_file(self.sid, self.nid, path.name))
            before = notebook._path().read_bytes()
            with patch.object(notebook, '_write', side_effect=OSError('registry failed')):
                result = call()
            self.assertEqual(result['stage'], 'registration', result)
            self.assertEqual(result['change_state'], 'unconfirmed')
            self.assertEqual(notebook._path().read_bytes(), before)
            self.assertEqual(path.exists(), operation == 'export')
            with patch.object(knowledge, 'public_view', side_effect=RuntimeError('refresh failed')):
                result = call()
            self.assertEqual(result['change_state'], 'committed', result)
            self.assertEqual(result['steps']['registration'], 'confirmed')
            self.assertEqual(result['steps']['refresh'], 'failed')
            self.assertEqual(result['path'], str(path))
            self.assertIn(str(path), result['error'])
            self.assertFalse(self.conversation.running)

    def test_registry_error_after_replace_is_still_unconfirmed(self):
        original = notebook._write
        def write_then_fail(book):
            original(book)
            raise OSError('ack lost')
        with patch.object(notebook, '_write', side_effect=write_then_fail):
            result = self.service.export_notebook_markdown(self.sid, self.nid)
        self.assertEqual(result['change_state'], 'unconfirmed')
        self.assertTrue(Path(result['path']).exists())
        self.assertEqual(notebook._read()['sessions'][0]['notes'][0]['files'][0]['path'], result['path'])
        self.platform.open.assert_not_called()

    def test_delete_unlink_failure_and_ambiguous_target_preserve_records(self):
        path = self.export()
        before = notebook._path().read_bytes()
        with patch.object(Path, 'unlink', side_effect=OSError('locked')):
            result = self.service.delete_notebook_file(self.sid, self.nid, path.name)
        self.assertEqual(result['stage'], 'deletion', result)
        self.assertTrue(path.exists())
        self.assertEqual(notebook._path().read_bytes(), before)
        book = notebook._read()
        note = book['sessions'][0]['notes'][0]
        note['files'].append(dict(note['files'][0]))
        notebook._write(book)
        for method in (self.service.open_notebook_file, self.service.reveal_notebook_file,
                       self.service.delete_notebook_file):
            result = method(self.sid, self.nid, path.name)
            self.assertEqual(result['change_state'], 'not_changed', result)
            self.assertIn('重复', result['error'])
        self.assertTrue(path.exists())

    def test_invalid_session_note_or_external_path_never_triggers_platform_or_cloud(self):
        path = self.export(); self.platform.reset_mock()
        other = self.new()
        for sid, nid in ((other, self.nid), (self.sid, 'missing')):
            self.assertEqual(self.service.export_notebook_feishu(sid, nid)['change_state'], 'not_changed')
            self.assertEqual(self.service.export_notebook_markdown(sid, nid)['change_state'], 'not_changed')
            for method in (self.service.open_notebook_file, self.service.reveal_notebook_file,
                           self.service.delete_notebook_file):
                self.assertEqual(method(sid, nid, path.name)['change_state'], 'not_changed')
        outside = self.root / 'outside.md'; outside.write_text('keep', encoding='utf-8')
        book = notebook._read()
        notebook._find_note(notebook._find(book, self.sid), self.nid)['files'] = [
            dict(name=outside.name, path=str(outside), saved=notebook._now())]
        notebook._write(book)
        self.assertEqual(self.service.delete_notebook_file(self.sid, self.nid, outside.name)['change_state'], 'not_changed')
        self.assertEqual(outside.read_text(encoding='utf-8'), 'keep')
        self.platform.open.assert_not_called(); self.platform.reveal.assert_not_called()
        self.create.assert_not_called()

    def test_default_feishu_adapter_uses_existing_parser_without_network(self):
        import json
        from desk_companion import feishu_auth
        service = NotebookService(self.sessions, platform=self.platform)
        envelope = dict(ok=True, data=dict(document=dict(url='https://example.invalid/doc', document_id='doc-id')))
        with patch.object(feishu_auth, '_run_lark', return_value=json.dumps(envelope)) as cli:
            result = service.export_notebook_feishu(self.sid, self.nid)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['document_id'], 'doc-id')
        self.assertEqual(cli.call_count, 1)
        self.assertEqual(cli.call_args.kwargs['timeout'], 90)
        self.assertIn('source text', cli.call_args.kwargs['stdin'])
        self.platform.open.assert_called_once_with('https://example.invalid/doc')

    def test_delete_preserves_note_turns_sources_other_files_and_notebooks(self):
        path = self.export()
        extra = path.with_name('keep.md'); extra.write_text('keep generated file', encoding='utf-8')
        book = notebook._read()
        extra_record = dict(name=extra.name, path=str(extra), saved=notebook._now())
        book['sessions'][0]['notes'][0]['files'].append(extra_record)
        notebook._write(book)
        other = self.new(); keep = self.save(other)
        source = self.root / 'source.md'; source.write_text('original', encoding='utf-8')
        notebook.begin_notebook_turn(self.sid, 'question')
        notebook.finish_notebook_turn(self.sid, 'answer', notebook._read()['sessions'][1]['notes'][0]['cites'])
        before = notebook._read()
        result = self.service.delete_notebook_file(self.sid, self.nid, path.name)
        self.assertTrue(result['ok'], result)
        self.assertFalse(path.exists())
        current = notebook._find(notebook._read(), self.sid)
        previous = notebook._find(before, self.sid)
        self.assertEqual(current['turns'], previous['turns'])
        self.assertEqual(current['notes'][0], dict(previous['notes'][0], files=[extra_record]))
        self.assertEqual(extra.read_text(encoding='utf-8'), 'keep generated file')
        self.assertEqual(notebook._find(notebook._read(), other), notebook._find(before, other))
        self.assertEqual(source.read_text(encoding='utf-8'), 'original')
        self.assertEqual(notebook._find(notebook._read(), other)['notes'][0]['id'], keep)

    def test_missing_file_drops_stale_registration_but_open_rejects(self):
        path = self.export(); path.unlink()
        self.platform.reset_mock()
        for method in (self.service.open_notebook_file, self.service.reveal_notebook_file):
            self.assertEqual(method(self.sid, self.nid, path.name)['change_state'], 'not_changed')
        self.platform.open.assert_not_called(); self.platform.reveal.assert_not_called()
        result = self.service.delete_notebook_file(self.sid, self.nid, path.name)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['steps']['deletion'], 'already_missing')

    def test_open_reveal_use_only_registered_target_and_report_request_status(self):
        path = self.export(); self.platform.reset_mock()
        for name in ('open', 'reveal'):
            call = getattr(self.service, name + '_notebook_file')
            action = getattr(self.platform, name)
            result = call(self.sid, self.nid, path.name)
            self.assertEqual(result['steps'][name], 'requested')
            action.assert_called_once_with(str(path))
            action.side_effect = OSError('not available')
            result = call(self.sid, self.nid, path.name)
            self.assertEqual(result['stage'], name)
            self.assertIn(str(path), result['error'])
            self.assertEqual(call(self.sid, self.nid, '../source')['change_state'], 'not_changed')

    def test_all_operations_reject_busy_without_releasing_existing_reservation(self):
        path = self.export(); self.platform.reset_mock()
        self.conversation.running = True
        calls = [lambda: self.service.export_notebook_markdown(self.sid, self.nid),
                 lambda: self.service.export_notebook_feishu(self.sid, self.nid)]
        calls += [lambda name=name: getattr(self.service, name + '_notebook_file')(self.sid, self.nid, path.name)
                  for name in ('open', 'reveal', 'delete')]
        before = notebook._path().read_bytes()
        for call in calls:
            self.assertEqual(call()['change_state'], 'not_changed')
            self.assertTrue(self.conversation.running)
        self.assertTrue(self.service.load_notebook()['ok'])
        self.assertEqual(notebook._path().read_bytes(), before)
        self.create.assert_not_called(); self.platform.open.assert_not_called()

    def test_network_and_platform_wait_keep_reads_and_storage_writes_available(self):
        for kind in ('cloud', 'platform'):
            entered, release = threading.Event(), threading.Event()
            self.addCleanup(release.set)
            def pause(*args):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('fixture timeout')
                return dict(ok=True, url='https://example.invalid/doc')
            self.create.side_effect = pause if kind == 'cloud' else None
            self.platform.open.side_effect = pause if kind == 'platform' else None
            results = []
            thread = threading.Thread(target=lambda: results.append(self.service.export_notebook_feishu(self.sid, self.nid)))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(self.conversation.running)
                self.assertTrue(self.service.load_notebook()['ok'])
                # Another storage writer can complete while the external request is paused.
                notebook.begin_notebook_turn(self.sid, kind)
                self.assertEqual(self.service.new_notebook()['change_state'], 'not_changed')
            finally:
                release.set(); thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(results[0]['ok'], results)
            self.assertFalse(self.conversation.running)

    def test_default_platform_only_requests_system_launch(self):
        platform = NotebookPlatform()
        with patch('os.startfile') as launch, patch('subprocess.Popen') as reveal:
            platform.open('local.md'); platform.reveal('local.md')
        launch.assert_called_once_with('local.md')
        reveal.assert_called_once_with(['explorer', '/select,local.md'])

    def test_outputs_can_be_used_without_gui_imports(self):
        script = '''
import sys, unittest
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from tests.test_notebook_files import NotebookFileTests
names = [n for n in unittest.defaultTestLoader.getTestCaseNames(NotebookFileTests)
         if n != 'test_outputs_can_be_used_without_gui_imports']
result = unittest.TextTestRunner().run(unittest.TestSuite(NotebookFileTests(n) for n in names))
assert_no_gui()
assert result.wasSuccessful()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'}, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
