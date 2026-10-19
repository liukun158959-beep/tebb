import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from desk_companion import memory, state, usage

SAMPLING = dict(reasoning_effort='low', temperature=.5, top_p=1)


class ConversationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=str(self.root))
        env.start(); self.addCleanup(env.stop)
        settings = state.load_state(123, 456)
        settings.session_id = 'selected-desktop'
        settings.save()
        self.saved_state = state.state_path().read_bytes()

    def run_worker(self, text='查询测试', *, warm=False, **options):
        request = dict(text=text, session_id='worker-session', channel='desktop',
                       sampling=SAMPLING,
                       limits=dict(call_timeout=30, task_timeout=60), **options)
        command = [sys.executable, '-u', 'tests/gui_free_worker_fixture.py'] + (['--standby'] if warm else [])
        result = subprocess.run(command,
            input=json.dumps(request), capture_output=True, text=True, encoding='utf-8',
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONUTF8': '1', 'DESK_TEST_KNOWLEDGE': '1' if options.get('knowledge') else '0'}, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertFalse([e for e in events if e['kind'] == 'failure'], result.stdout + result.stderr)
        return events

    def test_real_tool_worker_has_no_gui_and_preserves_history_usage_and_state(self):
        memory.append_chat('user', '之前的问题', 'worker-session')
        memory.append_chat('pet', '之前的回答', 'worker-session')
        events = self.run_worker(warm=True)
        self.assertIn('standby_ready', {e['kind'] for e in events})
        self.assertTrue(self.root.joinpath('tool_called').exists())
        self.assertTrue({'llm_start', 'llm_end', 'tool_start', 'tool_end', 'token', 'result'} <= {e['kind'] for e in events})
        rows = memory.list_chat('worker-session')
        self.assertEqual([row['role'] for row in rows], ['user', 'pet', 'user', 'pet'])
        self.assertEqual(rows[-1]['text'], '本地流式回归正常。')
        self.assertEqual(rows[-1]['react_loops'], 2)
        self.assertGreater(usage.summarize_usage()['total']['total_tokens'], 0)
        self.assertEqual(state.state_path().read_bytes(), self.saved_state)
        self.assertEqual(memory.list_chat('selected-desktop'), [])

    def test_cli_request_uses_existing_composer_without_gui_requirements(self):
        from desk_companion.board_workbench import BoardWorkbench
        text = BoardWorkbench()._compose_turn('查询', {'cli': ['get_today_agenda']})
        self.assertIn('必须调用工具 get_today_agenda', text)
        with self.assertRaisesRegex(RuntimeError,'必须同时选'):
            BoardWorkbench()._compose_turn('查询', {'cli':['github_recent']})
        with self.assertRaisesRegex(RuntimeError,'没有 CLI 工具'):
            BoardWorkbench()._compose_turn('查询', {'cli':['unknown']})
        text = BoardWorkbench()._compose_turn('查询', {'cli':['github_recent'], 'github':'sample/repository'})
        self.assertIn('sample/repository',text)

    def test_knowledge_worker_has_no_gui_and_retains_retrieval_trace(self):
        events = self.run_worker('资料问题', knowledge=True)
        self.assertTrue({'knowledge', 'token', 'result', 'llm_start', 'llm_end'} <= {e['kind'] for e in events})
        self.assertFalse([e for e in events if e['kind'] == 'tool_start'])
        rows = memory.list_chat('worker-session')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]['text'], '依据资料的知识回答。')
        self.assertEqual(rows[-1]['knowledge']['kept'][0]['doc'], '隔离测试文档')
        self.assertEqual(rows[-1]['knowledge']['kept'][0]['text'], '只使用这一段样本资料。')
        self.assertEqual(rows[-1]['total_tokens'], 15)
        self.assertEqual(state.state_path().read_bytes(), self.saved_state)

    def test_resumed_worker_does_not_repeat_completed_write(self):
        import hashlib
        fingerprint = hashlib.sha256(b'channel_write{}').hexdigest()
        events = self.run_worker('写入测试', completed_writes=[fingerprint], resume_note='\n继续已保存的任务。')
        self.assertFalse(self.root.joinpath('write_count').exists())
        result = next(e['data']['result'] for e in events if e['kind'] == 'tool_end')
        self.assertIn('不再重复执行', result['output'])


class ConversationServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=self.folder.name)
        env.start(); self.addCleanup(env.stop)

    def make_service(self, script):
        from atlas.testing import FakeLLM
        from desk_companion import assistant
        from desk_companion.conversation import ConversationService
        settings = state.load_state(0, 0, persist=False)
        settings.session_id = 'unit-session'
        self.service = ConversationService(settings)
        with patch.object(assistant, 'require_llm_env', return_value=dict(
                ATLAS_API_KEY='fixture', ATLAS_BASE_URL='https://example.invalid/v1', ATLAS_MODEL='fake')), \
             patch('atlas.LLM', return_value=FakeLLM(script)), \
             patch('desk_companion.model_catalog.bind_llm'):
            self.service.agent = assistant.build_agent(self.service)
        self.service.engage_model = Mock()
        return self.service

    def run_turn(self, text='问题', chips=None, **kwargs):
        return self.service.run_chat(text, chips or {}, kwargs.pop('delta_sink', lambda _: None),
            lambda _: None, SAMPLING, kwargs.pop('think_sink', lambda _: None), **kwargs)

    def test_reasoning_metadata_does_not_enter_the_next_model_context(self):
        def answer(*_):
            self.service.agent.llm.on_reasoning('PRIVATE_THOUGHT')
            return dict(role='assistant', content='第一条回答')
        service = self.make_service([answer, '第二条回答'])
        thoughts = []
        self.run_turn(think_sink=thoughts.append)
        self.run_turn('下一条问题')
        self.assertEqual(thoughts, ['PRIVATE_THOUGHT'])
        rows = memory.list_chat('unit-session')
        self.assertEqual(rows[1]['thinking'], 'PRIVATE_THOUGHT')
        self.assertNotIn('PRIVATE_THOUGHT', json.dumps(service.agent.llm.calls[1]['messages']))
        self.assertFalse(service.running)
        self.assertIsNone(service.agent.llm.sampling)
        self.assertIsNone(service.agent.llm.on_reasoning)

    def test_document_skill_and_attachment_bodies_are_not_persisted_as_user_text(self):
        self.make_service(['回答'])
        chips = dict(skills=['sample'], docs=[dict(id='https://example.invalid/docx/test',label='样本文档')], attachments=['file-1'])
        with patch('desk_companion.skill_catalog.skill_turn_block', return_value='PRIVATE_SKILL_BODY'), \
             patch('desk_companion.feishu_docs.doc_turn_block', return_value='PRIVATE_DOC_BODY'), \
             patch('desk_companion.local_sources.get', return_value=dict(name='样本附件')), \
             patch('desk_companion.local_sources.turn_block', return_value='PRIVATE_FILE_BODY'):
            self.run_turn(chips=chips)
        model_text = self.service.agent.llm.calls[0]['messages'][-1]['content']
        stored = memory.list_chat('unit-session')[0]['text']
        for body in ('PRIVATE_SKILL_BODY','PRIVATE_DOC_BODY','PRIVATE_FILE_BODY'):
            self.assertIn(body, model_text)
            self.assertNotIn(body, stored)

    def test_reasoning_callback_failure_keeps_partial_and_allows_another_turn(self):
        def first(*_):
            self.service.agent.llm.on_reasoning('部分思考')
            return dict(role='assistant',content='不应作为成功回复')
        self.make_service([first, '重试回答'])
        pieces = []
        def fail(piece):
            pieces.append(piece)
            raise ValueError('consumer stopped')
        with self.assertRaisesRegex(ValueError, 'consumer stopped'):
            self.run_turn(think_sink=fail)
        self.assertEqual(pieces, ['部分思考'])
        self.assertFalse(self.service.running)
        self.assertIsNone(self.service.agent.llm.on_reasoning)
        self.assertIsNone(self.service.agent.llm.sampling)
        self.assertEqual([r['role'] for r in memory.list_chat('unit-session')], ['user'])
        self.assertEqual(self.run_turn('继续'), '重试回答')

    def test_mcp_cleanup_failure_still_releases_turn_state(self):
        self.make_service(['回答'])
        chips = dict(mcp=[dict(server='sample',tool='lookup')])
        pieces = []
        with patch('desk_companion.mcp_client.attach_mcp_tools', return_value=['temporary']) as attach, \
             patch('desk_companion.mcp_client.detach_mcp_tools', side_effect=RuntimeError('cleanup failed')) as detach:
            with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                self.run_turn(chips=chips, delta_sink=pieces.append)
        attach.assert_called_once(); detach.assert_called_once()
        self.assertFalse(self.service.running)
        self.assertIsNone(self.service.agent.llm.sampling)
        self.assertEqual(pieces,['回答'])
        self.service.on_llm_delta('not part of a turn')
        self.assertEqual(pieces,['回答'])

    def test_knowledge_failure_restores_sampling_and_busy_state(self):
        self.make_service(['unused'])
        with patch('desk_companion.knowledge._retrieve', side_effect=RuntimeError('no source')):
            with self.assertRaisesRegex(RuntimeError,'no source'):
                self.run_turn(knowledge=True)
        self.assertFalse(self.service.running)
        self.assertIsNone(self.service.agent.llm.sampling)
        self.assertIsNone(self.service.agent.llm.on_reasoning)
        self.assertEqual([r['role'] for r in memory.list_chat('unit-session')], ['user'])

    def test_empty_model_reply_still_records_usage_before_reporting_failure(self):
        self.make_service([''])
        with self.assertRaisesRegex(RuntimeError,'模型没有给出回复'):
            self.run_turn()
        self.assertEqual(usage.summarize_usage()['total']['total_tokens'],15)
        self.assertEqual([r['role'] for r in memory.list_chat('unit-session')],['user'])
        self.assertFalse(self.service.running)

    def test_worker_settings_do_not_create_ui_state_or_change_selected_session(self):
        from desk_companion.conversation import ConversationService
        service = ConversationService.for_session('only-this-task')
        self.assertEqual(service.state.session_id, 'only-this-task')
        self.assertFalse(state.state_path().exists())
        service.state.save()
        self.assertFalse(state.state_path().exists())

    def test_worker_normalizes_legacy_persona_without_writing_settings_or_backup(self):
        from desk_companion.conversation import ConversationService
        settings = state.load_state(123,456)
        settings.persona = state.LEGACY_PERSONA
        settings.save()
        saved = state.state_path().read_bytes()
        service = ConversationService.for_session('only-this-task')
        self.assertEqual(service.state.persona,state.DEFAULT_PERSONA)
        self.assertEqual(state.state_path().read_bytes(),saved)
        self.assertFalse(state.state_path().with_name('user_state.before-tebb-persona.json').exists())

    def test_legacy_desktop_reuses_service_without_duplicate_history(self):
        from desk_companion.app import App
        service = self.make_service(['桌面回答'])
        app = App.__new__(App)
        app.state = service.state
        app.conversation = service
        app.pet = Mock()
        app.ui = lambda fn: fn()
        app._card_mode = 'chat'
        app._eval_bubble = Mock(); app._eval_board = Mock(); app.show_bubble = Mock()
        memory.append_chat('user','桌面问题','unit-session')
        with patch.object(service, 'run_agent', wraps=service.run_agent) as execute:
            app._run_agent('桌面问题')
        execute.assert_called_once()
        rows = memory.list_chat('unit-session')
        self.assertEqual([(r['role'],r['text']) for r in rows], [('user','桌面问题'),('pet','桌面回答')])
        user_messages = [m for m in service.agent.llm.calls[0]['messages'] if m['role']=='user']
        self.assertEqual(len(user_messages),1)
        self.assertFalse(app._agent_running)
