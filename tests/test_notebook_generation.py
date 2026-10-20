"""Notebook model lifecycles with isolated storage and real SDK transport failures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import OpenAI
from atlas.llm import LLM, IncompleteResponseError
from atlas.testing import FakeLLM

from desk_companion import knowledge, notebook
from tests.test_notebook_service import NotebookFixture, CITES


class NotebookGenerationTests(NotebookFixture):
    def setUp(self):
        super().setUp()
        self.llm = FakeLLM(['generated [1]'])
        self.conversation.agent = SimpleNamespace(llm=self.llm)
        self.conversation.engage_model = Mock()
        self.conversation.record_llm_usage = Mock()
        self.service.platform = Mock()
        self.sid = self.new()
        self.nid = self.save(self.sid)
        self.tokens, self.status = [], []
        def ask(text, ids, complete):
            if not ids:
                raise RuntimeError('no sources')
            return dict(answer=complete('sources only', text), cites=CITES)
        p = patch.object(knowledge, 'ask_sources', side_effect=ask)
        p.start(); self.addCleanup(p.stop)

    def question(self, ids=None):
        return self.service.run_notebook(self.sid, 'question', ['doc-1'] if ids is None else ids,
            dict(reasoning_effort='low', temperature=.5, top_p=1), self.tokens.append, self.status.append)

    def summary(self, picks=None):
        return self.service.summarize_notebook(self.sid, [self.nid] if picks is None else picks, {})

    def reset_model(self, reply='generated [1]'):
        self.llm = FakeLLM([reply]); self.conversation.agent.llm = self.llm

    def assert_clean(self):
        self.assertFalse(self.conversation.running)
        self.assertIsNone(self.llm.sampling)
        self.assertIsNone(self.llm.on_reasoning)

    def test_question_streams_and_saves_only_to_target_notebook(self):
        other = self.new(); before = notebook._find(notebook._read(), other)
        result = self.question()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['answer'], 'generated [1]')
        self.assertEqual(result['cites'], CITES)
        self.assertEqual(''.join(self.tokens), result['answer'])
        self.assertEqual(self.status, ['正在检索来源', '正在写回答'])
        turns = notebook._find(notebook._read(), self.sid)['turns']
        self.assertEqual([row['role'] for row in turns], ['user', 'pet'])
        self.assertEqual(notebook._find(notebook._read(), other), before)
        self.assertEqual(self.conversation.state.session_id, 'ordinary')
        self.conversation.record_llm_usage.assert_called_once()
        self.assert_clean()

    def test_summary_uses_selected_answer_snapshots_reindexes_and_exports_once(self):
        second = self.service.save_notebook_note(self.sid, 'second', 'second answer [1] [9]',
            [dict(CITES[0], context='stored context')])['note_id']
        result = self.summary([second, self.nid])
        self.assertTrue(result['ok'], result)
        self.assertEqual([c['n'] for c in result['cites']], [1, 2])
        self.assertEqual(result['cites'][0]['context'], 'stored context')
        prompt = self.llm.calls[0]['messages'][1]['content']
        self.assertLess(prompt.index('second answer [1] [9]'), prompt.index('answer [2]'))
        self.assertNotIn('source text', prompt)  # Summarize saved answers, not source bodies.
        self.assertTrue(Path(result['path']).is_file())
        self.assertEqual(result['steps']['open'], 'requested')
        self.assertEqual(len(notebook._find(notebook._read(), self.sid)['notes']), 3)
        self.assertEqual(notebook._find(notebook._read(), self.sid)['turns'], [])
        self.service.platform.open.assert_called_once_with(result['path'])
        self.assert_clean()

    def test_invalid_selection_and_busy_fail_before_model_and_storage_changes(self):
        before = notebook._path().read_bytes()
        for picks in ([], ['missing'], [self.nid, self.nid]):
            self.assertFalse(self.summary(picks)['ok'])
        self.assertEqual(notebook._path().read_bytes(), before)
        self.conversation.running = True
        for call in (self.question, self.summary):
            result = call()
            self.assertEqual(result['change_state'], 'not_changed')
            self.assertEqual(result['stage'], 'admission')
            self.assertTrue(self.conversation.running)
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(notebook._path().read_bytes(), before)

    def test_retrieval_failure_keeps_question_and_never_calls_model(self):
        result = self.question([])
        self.assertEqual(result['stage'], 'retrieval', result)
        self.assertEqual(result['steps']['question_save'], 'confirmed')
        self.assertEqual(notebook._find(notebook._read(), self.sid)['turns'], [dict(role='user', text='question')])
        self.assertEqual(self.llm.calls, [])
        self.assertFalse(self.conversation.running)

    def test_empty_non_text_or_tool_response_never_saves_complete_output(self):
        for content, extra in (('', {}), ('   ', {}), (['text'], {}),
                ('body', dict(tool_calls=[dict(id='call')])), ('body', dict(function_call=dict(name='tool')))):
            for mode in ('question', 'summary'):
                with self.subTest(content=content, mode=mode):
                    self.reset_model(dict(role='assistant', content=content, **extra))
                    before = notebook._find(notebook._read(), self.sid)
                    result = self.question() if mode == 'question' else self.summary()
                    self.assertEqual(result['stage'], 'validation', result)
                    self.assertFalse(result['answer_complete'])
                    after = notebook._find(notebook._read(), self.sid)
                    self.assertEqual(after['notes'], before['notes'])
                    self.assertEqual(sum(t['role'] == 'pet' for t in after['turns']), 0)
                    self.assert_clean()
        self.service.platform.open.assert_not_called()

    def test_incomplete_response_preserves_partial_but_not_as_saved_answer(self):
        partial = dict(message=dict(role='assistant', content='half answer'), usage={})
        def model(*args, **kwargs):
            if kwargs.get('on_delta'):
                kwargs['on_delta']('half answer')
            raise IncompleteResponseError('length', partial)
        for call in (self.question, self.summary):
            with patch.object(self.llm, 'chat', side_effect=model):
                result = call()
            self.assertEqual(result['answer'], 'half answer')
            self.assertFalse(result['answer_complete'])
            self.assertEqual(result['stage'], 'model')
            self.assert_clean()
        self.assertEqual(len(notebook._find(notebook._read(), self.sid)['notes']), 1)
        self.conversation.record_llm_usage.assert_not_called()

    def test_usage_failure_keeps_received_body_and_blocks_persistence(self):
        for call in (self.question, self.summary):
            self.reset_model()
            with patch.object(self.conversation, 'record_llm_usage', side_effect=OSError('usage disk failed')):
                result = call()
            self.assertEqual(result['stage'], 'usage')
            self.assertEqual(result['answer'], 'generated [1]')
            self.assert_clean()
        book = notebook._find(notebook._read(), self.sid)
        self.assertEqual(len(book['notes']), 1)
        self.assertEqual([t['role'] for t in book['turns']], ['user'])

    def test_question_write_acknowledgement_failures_preserve_actual_phase(self):
        original = notebook._write
        for target in (1, 2):
            count = 0
            def fail_at(book):
                nonlocal count
                count += 1
                original(book)
                if count == target:
                    raise OSError('ack lost')
            self.reset_model()
            with patch.object(notebook, '_write', side_effect=fail_at):
                result = self.question()
            self.assertEqual(result['change_state'], 'unconfirmed', result)
            self.assertEqual(result['stage'], 'question_save' if target == 1 else 'answer_save')
            if target == 2:
                self.assertEqual(result['answer'], 'generated [1]')
                self.assertTrue(result['answer_complete'])
            self.assertFalse(self.conversation.running)
        self.assertEqual([t['role'] for t in notebook._find(notebook._read(), self.sid)['turns']], ['user', 'user', 'pet'])

    def test_summary_save_unconfirmed_or_refresh_failed_returns_recoverable_note_id(self):
        for mode in ('write', 'refresh'):
            self.reset_model()
            target, attribute = (notebook, '_write') if mode == 'write' else (knowledge, 'public_view')
            with patch.object(target, attribute, side_effect=OSError('fixture failure')):
                result = self.summary()
            self.assertEqual(result['change_state'], 'unconfirmed' if mode == 'write' else 'committed', result)
            self.assertTrue(result['note_id'])
            self.assertEqual(result['answer'], 'generated [1]')
            self.assertIn(result['note_id'], result['error'])
            self.assert_clean()
        self.service.platform.open.assert_not_called()
        self.assertEqual(len(notebook._find(notebook._read(), self.sid)['notes']), 2)

    def test_summary_export_registration_refresh_and_open_failures_never_regenerate(self):
        for mode in ('generation', 'registration', 'refresh', 'open'):
            self.reset_model()
            original_write = notebook._write
            count = 0
            def write(book):
                nonlocal count
                count += 1
                if mode == 'registration' and count == 2:
                    raise OSError('registry failed')
                original_write(book)
            original_view = knowledge.public_view
            views = 0
            def view():
                nonlocal views
                views += 1
                if mode == 'refresh' and views == 2:
                    raise OSError('refresh failed')
                return original_view()
            self.service.platform.open.side_effect = OSError('no handler') if mode == 'open' else None
            with patch.object(notebook, '_write', side_effect=write), patch.object(knowledge, 'public_view', side_effect=view):
                if mode == 'generation':
                    original_text = Path.write_text
                    def write_text(path, *args, **kwargs):
                        if path.suffix == '.md':
                            raise OSError('disk full')
                        return original_text(path, *args, **kwargs)
                    with patch.object(Path, 'write_text', new=write_text):
                        result = self.summary()
                else:
                    result = self.summary()
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['stage'], mode)
            self.assertEqual(result['answer'], 'generated [1]')
            self.assertTrue(result['note_id'])
            self.assertTrue(result['path'])
            self.assertEqual(len(self.llm.calls), 1)
            self.assert_clean()
        self.assertEqual(len(notebook._find(notebook._read(), self.sid)['notes']), 5)

    def test_model_wait_does_not_hold_storage_and_rejects_other_shared_operations(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def model(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('not released')
            return dict(message=dict(role='assistant', content='generated [1]'), usage={})
        results = []
        with patch.object(self.llm, 'chat', side_effect=model):
            thread = threading.Thread(target=lambda: results.append(self.summary()))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(self.service.load_notebook()['ok'])
                self.assertEqual(self.question()['change_state'], 'not_changed')
                notebook.begin_notebook_turn(self.sid, 'other writer')
            finally:
                release.set(); thread.join(5)
        self.assertTrue(results[0]['ok'], results)
        self.assert_clean()

    def test_real_sdk_rejects_incomplete_finishes_for_both_paths(self):
        for finish in ('length', 'content_filter', None, 'tool_calls', 'stop'):
            def response(request):
                message = dict(role='assistant', content='real SDK body')
                if finish == 'tool_calls':
                    message['tool_calls'] = [dict(id='call', type='function', function=dict(name='tool', arguments='{}'))]
                if json.loads(request.content).get('stream'):
                    delta = dict(message)
                    if 'tool_calls' in delta:
                        delta['tool_calls'] = [dict(delta['tool_calls'][0], index=0)]
                    chunk = dict(id='fixture', object='chat.completion.chunk', created=0, model='fixture',
                        choices=[dict(index=0, finish_reason=finish, delta=delta)])
                    return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n',
                        headers={'Content-Type': 'text/event-stream'})
                return httpx.Response(200, json=dict(id='fixture', object='chat.completion', created=0, model='fixture',
                    choices=[dict(index=0, finish_reason=finish, message=message)]))
            with httpx.Client(transport=httpx.MockTransport(response)) as http:
                with OpenAI(api_key='fixture', http_client=http, max_retries=0) as sdk:
                    self.llm = LLM(model='fixture', client=sdk)
                    self.conversation.agent.llm = self.llm
                    for call in (self.question, self.summary):
                        result = call()
                        self.assertEqual(result['ok'], finish == 'stop', result)
                        self.assert_clean()

    def test_generation_runs_without_gui_imports(self):
        script = '''
import sys, unittest
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from tests.test_notebook_generation import NotebookGenerationTests
names = [n for n in unittest.defaultTestLoader.getTestCaseNames(NotebookGenerationTests)
         if n != 'test_generation_runs_without_gui_imports']
result = unittest.TextTestRunner().run(unittest.TestSuite(NotebookGenerationTests(n) for n in names))
assert_no_gui()
assert result.wasSuccessful()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
            encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'}, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
