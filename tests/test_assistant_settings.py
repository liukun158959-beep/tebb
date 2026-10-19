"""Model/persona configuration with temporary data and an offline SDK transport."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

from desk_companion import envconf, model_catalog, model_network, state
from desk_companion.assistant_settings import AssistantSettingsService
from desk_companion.conversation import ConversationService


class SettingsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=folder.name)
        env.start(); self.addCleanup(env.stop)
        self.state = state.load_state(11, 22)
        self.conversation = ConversationService(self.state)
        self.conversation.rebuild_agent = Mock()
        self.conversation.bind_active_llm = Mock()
        self.service = AssistantSettingsService(self.conversation)

    def first_model(self):
        result = self.service.save_model_entry(dict(base_url='https://example.invalid/v1', model='one', api_key='PRIVATE_MODEL_KEY'))
        self.assertTrue(result['ok'], result)
        return result['active']

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}

    def test_persona_uses_shared_state_rebuilds_and_preserves_unrelated_fields(self):
        sid = self.state.session_id
        result = self.service.save_persona(dict(persona=' 自定义研究伙伴 ', max_steps=12, nudge_enabled=False))
        self.assertTrue(result['ok'], result)
        self.conversation.rebuild_agent.assert_called_once_with()
        saved = state.load_state(0, 0)
        self.assertEqual((saved.persona, saved.max_steps, saved.nudge_enabled), ('自定义研究伙伴', 12, False))
        self.assertEqual((saved.x, saved.y, saved.session_id), (11, 22, sid))
        view = self.service.load_persona()
        self.assertEqual(view['persona'], self.conversation.state.persona)
        self.assertIn('tool_contract', view)
        self.assertEqual(self.service.load_model()['agent_ok'], False)
        self.conversation.agent = Mock()
        self.conversation.agent_error = 'fixture error'
        self.assertEqual(self.service.load_model()['agent_error'], 'fixture error')
        self.assertTrue(self.service.load_model()['agent_ok'])

    def test_invalid_persona_never_writes_or_rebuilds(self):
        before = self.snapshot()
        for payload in (None, {}, dict(persona='x', max_steps=0, nudge_enabled=True),
                        dict(persona='x', max_steps=3, nudge_enabled='true')):
            with self.subTest(payload=payload):
                self.assertFalse(self.service.save_persona(payload)['ok'])
                self.assertEqual(self.snapshot(), before)
        self.conversation.rebuild_agent.assert_not_called()

    def test_catalog_copy_edit_switch_delete_and_public_key_redaction(self):
        one = self.first_model()
        second = self.service.save_model_entry(dict(base_url='https://example.invalid/v1', model='two', copy_key_from=one))
        two = second['items'][1]['id']
        self.assertEqual(second['active'], one)
        edited = self.service.save_model_entry(dict(id=two, base_url='https://example.invalid/v1', model='edited'))
        self.assertTrue(edited['ok'], edited)
        self.assertEqual(model_catalog.require_item(two)['api_key'], 'PRIVATE_MODEL_KEY')
        self.assertEqual(self.service.use_model(two)['active'], two)
        self.assertEqual(envconf.public_llm_env()['model'], 'edited')
        self.assertEqual(self.service.delete_model_entry(two)['active'], one)
        self.assertEqual(envconf.public_llm_env()['model'], 'one')
        self.assertFalse(self.service.delete_model_entry(one)['ok'])
        self.assertEqual(self.conversation.bind_active_llm.call_count, 5)
        for view in (second, edited, self.service.load_models(), self.service.load_model()):
            self.assertNotIn('PRIVATE_MODEL_KEY', json.dumps(view))
        self.assertTrue(self.service.load_model()['has_key'])

    def test_busy_state_blocks_all_mutations_but_not_reads(self):
        one = self.first_model()
        before = self.snapshot()
        self.conversation.bind_active_llm.reset_mock()
        self.conversation.running = True
        calls = [lambda: self.service.save_persona(dict(persona='x', max_steps=1, nudge_enabled=True)),
                 lambda: self.service.save_model_entry(dict(id=one, model='changed')),
                 lambda: self.service.delete_model_entry(one), lambda: self.service.use_model(one),
                 lambda: self.service.save_model(dict(model='changed'))]
        for call in calls:
            result = call()
            self.assertFalse(result['ok'], result)
            self.assertIn('助手正在回复', result['error'])
        self.assertEqual(self.snapshot(), before)
        self.conversation.rebuild_agent.assert_not_called()
        self.conversation.bind_active_llm.assert_not_called()
        for call in (self.service.load_persona, self.service.load_model, self.service.load_models):
            self.assertTrue(call()['ok'])

    def test_catalog_binds_the_shared_agent_with_owned_client(self):
        agent = Mock()
        self.conversation.agent = agent
        self.conversation.bind_active_llm.side_effect = lambda: ConversationService.bind_active_llm(self.conversation)
        with patch.object(model_network, 'openai_client') as client:
            one = self.first_model()
            agent.llm.bind_client.assert_called_with(client.return_value, model='one', owned=True)
            second = self.service.save_model_entry(dict(base_url='https://example.invalid/v1', model='two', copy_key_from=one))
            self.assertTrue(self.service.use_model(second['items'][1]['id'])['ok'])
            agent.llm.bind_client.assert_called_with(client.return_value, model='two', owned=True)
        self.assertIs(self.conversation.agent, agent)
        self.conversation.rebuild_agent.assert_not_called()

    def test_legacy_desktop_methods_share_configuration_service(self):
        from desk_companion.app import App
        app = App.__new__(App)
        app.state = self.state
        app.conversation = self.conversation
        app.assistant_settings = self.service
        self.assertTrue(app.save_persona(dict(persona='legacy desktop', max_steps=6, nudge_enabled=True))['ok'])
        self.assertEqual(app.board_persona()['persona'], self.service.load_persona()['persona'])
        self.assertTrue(app.save_model_entry(dict(base_url='https://example.invalid/v1', model='desktop', api_key='fixture'))['ok'])
        self.assertEqual(app.board_models(), self.service.load_models())
        self.assertEqual(app.board_model()['model'], 'desktop')

    def test_legacy_save_keeps_key_comments_prices_and_rebuild_order(self):
        self.first_model()
        with envconf.env_path().open('a', encoding='utf-8') as file:
            file.write('# retained comment\nOTHER_SETTING=retained\n')
        def rebuilt():
            self.assertEqual(envconf.public_llm_env()['model'], 'legacy')
            self.assertEqual(state.load_state(0, 0).model_prices['legacy']['input_cny_per_mtok'], 2.0)
        self.conversation.rebuild_agent.side_effect = rebuilt
        result = self.service.save_model(dict(base_url='https://example.invalid/v1', model='legacy',
                                             input_cny_per_mtok=2, output_cny_per_mtok=4))
        self.assertTrue(result['ok'], result)
        self.assertEqual(envconf.parse_env_file()['ATLAS_API_KEY'], 'PRIVATE_MODEL_KEY')
        self.assertIn('# retained comment\nOTHER_SETTING=retained', envconf.env_path().read_text('utf-8'))
        self.assertEqual(self.service.load_model()['output_cny_per_mtok'], 4.0)
        self.conversation.rebuild_agent.side_effect = None
        self.assertTrue(self.service.save_model(dict(base_url='https://example.invalid/v1', model='legacy'))['ok'])
        self.assertNotIn('legacy', state.load_state(0, 0).model_prices)

    def test_model_test_uses_saved_keys_and_closes_offline_clients_without_saving(self):
        one = self.first_model()
        before = self.snapshot()
        clients, seen, timeouts = [], [], []
        def transport(timeout):
            timeouts.append(timeout)
            def handle(request):
                seen.append((request.headers['authorization'], json.loads(request.content)))
                return httpx.Response(200, json=dict(choices=[dict(index=0, finish_reason='stop', message=dict(role='assistant', content='pong'))]))
            client = httpx.Client(transport=httpx.MockTransport(handle), timeout=timeout)
            clients.append(client)
            return client
        self.conversation.running = True  # Testing a connection does not alter a running Agent.
        with patch.object(model_network, 'http_client', side_effect=transport):
            for extra, key in (({'api_key': 'EXPLICIT_KEY'}, 'EXPLICIT_KEY'), ({'id': one}, 'PRIVATE_MODEL_KEY'), ({}, 'PRIVATE_MODEL_KEY')):
                result = self.service.test_model(dict(base_url='https://example.invalid/v1', model='probe', **extra))
                self.assertEqual(result, dict(ok=True, message='连通正常。 回复：pong'))
                self.assertEqual(seen[-1][0], 'Bearer ' + key)
                self.assertEqual(seen[-1][1], dict(model='probe', messages=[dict(role='user', content='ping')], max_tokens=8))
        self.assertEqual(timeouts, [20, 20, 20])
        self.assertTrue(all(client.is_closed for client in clients))
        self.assertEqual(self.snapshot(), before)
        self.conversation.rebuild_agent.assert_not_called()

    def test_model_test_reports_validation_and_network_failure_without_persisting(self):
        before = self.snapshot()
        with patch.object(model_network, 'openai_client', side_effect=RuntimeError('fixture offline')) as create:
            self.assertFalse(self.service.test_model(None)['ok'])
            self.assertFalse(self.service.test_model({})['ok'])
            create.assert_not_called()
            result = self.service.test_model(dict(base_url='https://example.invalid/v1', model='probe', api_key='fixture'))
            self.assertEqual(result, dict(ok=False, error='fixture offline'))
        self.assertEqual(self.snapshot(), before)

    def test_rebuild_and_binding_failures_keep_existing_error_contract(self):
        self.conversation.rebuild_agent.side_effect = RuntimeError('rebuild failed')
        result = self.service.save_persona(dict(persona='changed', max_steps=5, nudge_enabled=True))
        self.assertEqual(result, dict(ok=False, error='rebuild failed'))
        self.assertEqual(state.load_state(0, 0).persona, 'changed')
        self.conversation.bind_active_llm.side_effect = RuntimeError('binding failed')
        result = self.service.save_model_entry(dict(base_url='https://example.invalid/v1', model='saved', api_key='fixture'))
        self.assertEqual(result, dict(ok=False, error='binding failed'))
        self.assertEqual(model_catalog.require_active()['model'], 'saved')

    def test_service_can_execute_all_operations_with_gui_imports_forbidden(self):
        script = '''
import sys, unittest
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from tests.test_assistant_settings import SettingsTests
names = [name for name in unittest.defaultTestLoader.getTestCaseNames(SettingsTests)
         if name not in ('test_service_can_execute_all_operations_with_gui_imports_forbidden',
                         'test_legacy_desktop_methods_share_configuration_service')]
result = unittest.TextTestRunner().run(unittest.TestSuite(SettingsTests(name) for name in names))
assert_no_gui()
assert result.wasSuccessful()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, encoding='utf-8',
                                cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'PYTHONUTF8': '1'}, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
