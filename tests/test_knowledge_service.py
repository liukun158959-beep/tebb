"""Knowledge snapshots, lifecycle, model leases and fault receipts using isolated data."""
from contextlib import ExitStack
import io
import json
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

from atlas.testing import FakeLLM
from desk_companion import knowledge as k, local_sources, notebook
from desk_companion.chat_sessions import ChatSessionService
from desk_companion.knowledge_service import KnowledgeService


class KnowledgeFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=folder.name)
        env.start(); self.addCleanup(env.stop)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(k, '_JOBS', {}))
        self.stack.enter_context(patch.object(k, '_SEQ', 0))
        self.stack.enter_context(patch.object(k, '_require_models'))
        self.stack.enter_context(patch.object(k, '_embed', side_effect=lambda texts: [[1., 0.] for _ in texts]))
        self.stack.enter_context(patch.object(k, '_rerank', side_effect=lambda q, rows: rows))
        self.conversation = SimpleNamespace(running=False, state=SimpleNamespace(session_id='ordinary'),
            engage_model=Mock(), record_llm_usage=Mock(), agent=SimpleNamespace(llm=FakeLLM(['answer'])))
        self.service = KnowledgeService(ChatSessionService(self.conversation))
        self.repo = k.EMBED_MODELS[0]['repo']

    def add(self, name='source.md'):
        path = self.root / name
        path.write_text('original source body。', encoding='utf-8')
        source = local_sources.stage([str(path)])['sources'][0]
        doc_id = 'local:' + source['id']
        result = self.service.add_knowledge(doc_id, name)
        self.assertTrue(result['ok'], result)
        return doc_id, path

    def wait_download(self, repo=None):
        repo = repo or self.repo
        deadline = time.monotonic() + 5
        while True:
            rows, _ = k._progress_public()
            row = next(r for r in rows if r['repo'] == repo)
            if not row['active']:
                # Progress is published just before the worker releases its lease.
                try:
                    with k._model_access(repo, write=True):
                        return row
                except RuntimeError:
                    pass
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)


class KnowledgeServiceTests(KnowledgeFixture):
    def test_all_operations_source_deletion_isolated_and_no_chat_history(self):
        doc_id, original = self.add()
        other, _ = self.add('other.md')
        sid = notebook.new_notebook()['session_id']
        cites = [dict(n=1, doc='source', title='section', text='snapshot', doc_id=doc_id)]
        notebook.save_note(sid, 'q', 'saved [1]', cites)
        book = notebook._path().read_bytes()
        result = self.service.ask_knowledge('question')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['trace']['answer'], 'answer')
        self.conversation.record_llm_usage.assert_called_once()
        self.assertFalse((self.root / 'memory' / 'chat.jsonl').exists())
        self.assertTrue(self.service.rebuild_knowledge()['ok'])
        self.assertTrue(self.service.delete_knowledge(doc_id)['ok'])
        self.assertEqual([d['id'] for d in k.public_view()['docs']], [other])
        self.assertEqual(notebook._path().read_bytes(), book)
        self.assertTrue(original.is_file())
        self.assertFalse(self.conversation.running)

    def test_settings_preserve_index_and_require_explicit_rebuild(self):
        self.add()
        before = k._index_path().read_bytes()
        settings = dict(k.load_settings(), chunk_size=1000)
        self.assertTrue(self.service.save_knowledge(settings)['ok'])
        self.assertEqual(k._index_path().read_bytes(), before)
        result = self.service.ask_knowledge('question')
        self.assertFalse(result['ok'])
        self.assertIn('重建', result['error'])
        self.assertEqual(self.conversation.agent.llm.calls, [])
        self.assertTrue(self.service.rebuild_knowledge()['ok'])
        self.assertTrue(self.service.ask_knowledge('question')['ok'])

    def test_busy_reads_work_and_all_changes_refused_without_releasing_owner(self):
        self.conversation.running = True
        for action in (lambda: self.service.save_knowledge(k.default_settings()),
                lambda: self.service.add_knowledge('x', 'x'), lambda: self.service.delete_knowledge('x'),
                self.service.rebuild_knowledge, lambda: self.service.download_knowledge(self.repo),
                lambda: self.service.delete_model(self.repo), lambda: self.service.ask_knowledge('q')):
            self.assertEqual(action()['change_state'], 'not_changed')
            self.assertTrue(self.conversation.running)
        self.assertTrue(self.service.load_knowledge()['ok'])
        self.assertEqual(self.conversation.agent.llm.calls, [])

    def test_atomic_write_refresh_and_partial_delete_have_distinct_receipts(self):
        settings = k.default_settings()
        with patch('desk_companion.video.atomic_write', side_effect=OSError('disk')):
            result = self.service.save_knowledge(settings)
        self.assertEqual((result['stage'], result['change_state']), ('settings_save', 'unconfirmed'))
        with patch.object(k, 'public_view', side_effect=OSError('page')):
            result = self.service.save_knowledge(settings)
        self.assertEqual((result['stage'], result['change_state']), ('refresh', 'committed'))
        self.assertEqual(k.load_settings(), settings)
        folder = k._repo_dir(self.repo); folder.mkdir(parents=True)
        (folder / 'config.json').write_text('{}')
        with patch.object(k.shutil, 'rmtree', side_effect=OSError('partial')):
            result = self.service.delete_model(self.repo)
        self.assertEqual((result['stage'], result['change_state']), ('model_delete', 'partial'))
        self.assertEqual(result['path'], str(folder))
        self.assertTrue(self.service.delete_model(self.repo)['ok'])
        self.assertEqual(k.load_settings(), settings)

    def test_compute_failure_and_stale_settings_or_index_never_overwrite(self):
        doc_id, _ = self.add()
        before = k._index_path().read_bytes()
        with patch.object(k, '_fetch', side_effect=OSError('fetch')):
            self.assertFalse(self.service.rebuild_knowledge()['ok'])
        self.assertEqual(k._index_path().read_bytes(), before)
        for target in ('settings', 'index'):
            def change(_texts):
                if target == 'settings':
                    k.save_settings(dict(k.load_settings(), retrieve_k=21))
                else:
                    index = k._read_index(); index['docs'][0]['title'] = 'concurrent title'
                    k._write_index(index)
                return [[1., 0.]]
            with patch.object(k, '_embed', side_effect=change):
                result = self.service.rebuild_knowledge()
            self.assertFalse(result['ok'], result)
            self.assertIn('变化', result['error'])
        self.assertEqual(k._read_index()['docs'][0]['title'], 'concurrent title')
        with patch('desk_companion.video.atomic_write', side_effect=OSError('replace')):
            result = self.service.delete_knowledge(doc_id)
        self.assertEqual(result['change_state'], 'unconfirmed')
        self.assertEqual(len(k._read_index()['docs']), 1)

    def test_retrieval_freezes_settings_and_index_during_model_wait(self):
        self.add()
        seen = []
        before = k.load_settings()
        def embed(texts):
            seen.append(k._current_settings())
            k.save_settings(dict(k.load_settings(), retrieve_k=1, rerank_n=1))
            k.delete_doc(k._read_index()['docs'][0]['id'])
            return [[1., 0.]]
        def rerank(q, rows):
            seen.append(k._current_settings())
            return rows
        with patch.object(k, '_embed', side_effect=embed), patch.object(k, '_rerank', side_effect=rerank):
            found = k._retrieve('q', None)
        self.assertEqual(seen, [before, before])
        self.assertEqual(len(found['kept']), 1)
        self.assertEqual(k.public_view()['docs'], [])

    def test_model_invalid_tool_and_incomplete_results_keep_body_and_usage_order(self):
        self.add()
        for message in (dict(role='assistant', content=''), dict(role='assistant', content=['invalid']),
                        dict(role='assistant', content='partial', tool_calls=[dict(id='tool')])):
            self.conversation.agent.llm = FakeLLM([message])
            result = self.service.ask_knowledge('q')
            self.assertFalse(result['ok'])
            self.assertEqual(result['stage'], 'validation')
            self.assertFalse(result['answer_complete'])
        exc = RuntimeError('truncated')
        exc.partial = dict(message=dict(content='retained body'))
        with patch.object(self.conversation.agent.llm, 'chat', side_effect=exc):
            result = self.service.ask_knowledge('q')
        self.assertEqual(result['answer'], 'retained body')
        self.assertIn('retained body', result['error'])
        self.assertEqual(result['stage'], 'model')
        self.assertEqual(self.conversation.record_llm_usage.call_count, 3)
        self.assertFalse(self.conversation.running)

    def test_download_admission_readers_other_models_and_refresh_failure(self):
        entered, release = threading.Event(), threading.Event()
        def contents(repo, dest):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('not released')
            k._touch_progress(repo, active=False, phase='done')
        with patch.object(k, '_download_contents', side_effect=contents):
            with patch.object(k, 'public_view', side_effect=OSError('refresh')):
                result = self.service.download_knowledge(self.repo)
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(result['change_state'], 'committed')
                self.assertTrue(result['started'])
                self.assertFalse(self.conversation.running)
                self.assertTrue(self.service.load_knowledge()['downloads'][0]['active'])
                self.assertFalse(self.service.delete_model(self.repo)['ok'])
                with self.assertRaises(RuntimeError):
                    with k._model_access(self.repo):
                        pass
                with k._model_access(k.RERANK_MODELS[0]['repo'], write=True):
                    pass
            finally:
                release.set(); self.wait_download()
        with k._model_access(self.repo):
            result = self.service.download_knowledge(self.repo)
            self.assertFalse(result['started'])
            self.assertEqual(result['change_state'], 'not_changed')
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('thread')):
            result = self.service.download_knowledge(self.repo)
        self.assertFalse(result['started'])
        self.assertFalse(k._progress_public()[0][0]['active'])

    def test_failed_download_keeps_partial_and_manual_retry_resumes(self):
        files = [dict(path='config.json', size=2), dict(path='model.safetensors', size=6)]
        count = 0
        class Response(io.BytesIO):
            status = 200
            headers = {}
        def urlopen(req, **kwargs):
            nonlocal count
            if 'config.json' in req.full_url:
                return Response(b'{}')
            count += 1
            if count == 1:
                class Broken(Response):
                    def read(self, *a):
                        if self.tell():
                            raise OSError('network failed')
                        return super().read(3)
                return Broken(b'abcdef')
            self.assertEqual(req.get_header('Range'), 'bytes=3-')
            response = Response(b'def'); response.status = 206
            response.headers = {'Content-Range': 'bytes 3-5/6'}
            return response
        with patch.object(k, '_ms_files', return_value=files), patch('urllib.request.urlopen', side_effect=urlopen):
            self.assertTrue(self.service.download_knowledge(self.repo)['ok'])
            self.assertEqual(self.wait_download()['phase'], 'error')
            self.assertEqual((k._repo_dir(self.repo) / 'model.safetensors.part').read_bytes(), b'abc')
            self.assertTrue(self.service.download_knowledge(self.repo)['ok'])
            self.assertEqual(self.wait_download()['phase'], 'done')
        self.assertEqual((k._repo_dir(self.repo) / 'model.safetensors').read_bytes(), b'abcdef')

    def test_cache_reload_after_external_file_change_and_reader_lease_during_encode(self):
        folder = k._repo_dir(self.repo); folder.mkdir(parents=True)
        weight = folder / 'model.safetensors'; weight.write_bytes(b'old')
        instances = []
        def make(*args, **kwargs):
            def encode(texts, **kwargs):
                self.assertFalse(self.service.delete_model(self.repo)['ok'])
                return [[1., 0.] for _ in texts]
            model = SimpleNamespace(encode=encode); instances.append(model)
            return model
        # Restore the actual embed wrapper while retaining isolated data.
        self.stack.close()
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        with patch.dict(sys.modules, sentence_transformers=SimpleNamespace(SentenceTransformer=make)), \
                patch.object(k, '_MODELS', {}):
            k._embed(['q']); k._embed(['q'])
            self.assertEqual(len(instances), 1)
            weight.write_bytes(b'new version')
            k._embed(['q'])
            self.assertEqual(len(instances), 2)

    def test_real_process_model_leases_release_on_process_exit(self):
        code = "from desk_companion.knowledge import _model_access; import sys;\nwith _model_access(sys.argv[1]):\n print('ready',flush=True); sys.stdin.readline()"
        process = subprocess.Popen([sys.executable, '-u', '-c', code, self.repo],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            with k._model_access(self.repo):
                pass
            self.assertFalse(self.service.download_knowledge(self.repo)['ok'])
            with self.assertRaises(RuntimeError):
                with k._model_access(self.repo, write=True):
                    pass
        finally:
            process.kill(); process.communicate(timeout=10)
        with k._model_access(self.repo, write=True):
            pass

    def test_other_model_loads_while_first_model_constructor_is_paused(self):
        self.stack.close()
        entered, release = threading.Event(), threading.Event()
        errors = []
        def factory(path, **kwargs):
            if path.endswith(self.repo.split('/')[-1]):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('not released')
            return object()
        def load_first():
            try:
                with k._model_access(self.repo):
                    k._embed_model()
            except Exception as exc:
                errors.append(exc)
        with patch.dict(sys.modules, sentence_transformers=SimpleNamespace(
                SentenceTransformer=factory, CrossEncoder=factory)), patch.object(k, '_MODELS', {}):
            thread = threading.Thread(target=load_first); thread.start()
            try:
                self.assertTrue(entered.wait(3))
                other = k.EMBED_MODELS[1]['repo']
                with k._using_settings(dict(k.default_settings(), embed_repo=other)), k._model_access(other):
                    self.assertIsNotNone(k._embed_model())
                with k._model_access(k.RERANK_MODELS[0]['repo']):
                    self.assertIsNotNone(k._rerank_model())
                self.assertTrue(thread.is_alive())
            finally:
                release.set(); thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_no_gui_subprocess_exercises_all_eight_entries(self):
        code = '''
import sys
sys.path.insert(0, 'tests')
from gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from tests.test_knowledge_service import KnowledgeServiceTests
import unittest
suite = unittest.TestSuite(KnowledgeServiceTests(name) for name in (
 'test_all_operations_source_deletion_isolated_and_no_chat_history',
 'test_settings_preserve_index_and_require_explicit_rebuild',
 'test_download_admission_readers_other_models_and_refresh_failure',
 'test_atomic_write_refresh_and_partial_delete_have_distinct_receipts'))
assert unittest.TextTestRunner().run(suite).wasSuccessful()
assert_no_gui()
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
            encoding='utf-8', timeout=40, env={**os.environ, 'PYTHONUTF8': '1'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cross_process_same_local_source_has_one_atomic_commit(self):
        original = self.root / 'shared.md'; original.write_text('shared source')
        code = '''
import os, sys, time
from pathlib import Path
from unittest.mock import patch
from desk_companion import knowledge as k, local_sources
root = Path(os.environ['DESK_COMPANION_DATA_DIR'])
source = local_sources.stage([str(root/'shared.md')])['sources'][0]
def embed(texts):
 (root/(sys.argv[1]+'.ready')).touch()
 deadline=time.monotonic()+15
 while not (root/'go').exists():
  if time.monotonic()>deadline: raise RuntimeError('timeout')
  time.sleep(.01)
 return [[1.,0.] for _ in texts]
with patch.object(k,'_require_models'), patch.object(k,'_embed',side_effect=embed):
 try:
  k.add_doc('local:'+source['id'],'shared')
  print('committed')
 except RuntimeError as exc: print('refused',str(exc))
'''
        processes = [subprocess.Popen([sys.executable, '-u', '-c', code, str(i)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            env={**os.environ, 'PYTHONUTF8': '1'}) for i in range(2)]
        try:
            deadline = time.monotonic()+15
            while not all((self.root / f'{i}.ready').exists() for i in range(2)):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            self.assertEqual(k.public_view()['docs'], [])
            (self.root / 'go').touch()
            outputs = []
            for process in processes:
                out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, err)
                outputs.append(out)
            self.assertEqual(sum(out.startswith('committed') for out in outputs), 1, outputs)
            self.assertEqual(len(k._read_index()['docs']), 1)
            self.assertEqual(len(k._read_index()['chunks']), 1)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
