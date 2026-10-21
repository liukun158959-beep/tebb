"""Composition and real authenticated backend without legacy GUI imports."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


class HeadlessHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=str(self.root))
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_composition_keeps_saved_state_and_single_lazy_managers(self):
        from desk_companion.state import load_state
        from desk_companion.local_api.host import HeadlessApp
        saved = load_state(531, 842)
        saved.persona = 'saved persona'; saved.save()
        before = (self.root/'user_state.json').read_bytes()
        host = HeadlessApp()
        self.assertEqual((host.state.x, host.state.y, host.state.persona), (531, 842, 'saved persona'))
        self.assertEqual((self.root/'user_state.json').read_bytes(), before)
        self.assertIs(host.chat_sessions.conversation, host.conversation)
        self.assertIs(host.assistant_settings.conversation, host.conversation)
        self.assertIsNone(host._tasks); self.assertIsNone(host._collaboration)
        self.assertFalse(hasattr(host, 'pet')); self.assertFalse(hasattr(host, 'board'))
        for name, module, symbol in [('tasks','desk_companion.tasks','TaskManager'),
                                      ('collaboration','desk_companion.collaboration','Collaboration')]:
            with patch(module+'.'+symbol, return_value=object()) as constructor:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    objects = list(pool.map(lambda _: getattr(host, name), range(16)))
            constructor.assert_called_once()
            self.assertTrue(all(obj is objects[0] for obj in objects))

    def test_legacy_guards_keep_pet_tail_protection(self):
        from desk_companion.app import App
        host = SimpleNamespace(pet=SimpleNamespace(busy=True))
        for name in ('_session_mutation_guard','_notebook_start_guard',
                     '_knowledge_start_guard','_review_start_guard'):
            guard = getattr(App, name)
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, '助手正在回复'):
                with guard(host, 'session') if name == '_session_mutation_guard' else guard(host):
                    self.fail('busy legacy desktop was admitted')

    def test_explicit_feishu_task_mode_preserves_recovery_and_legacy_path(self):
        from desk_companion.feishu_agent import FeishuAgent, Inbox
        from desk_companion.local_api.host import HeadlessApp
        host = HeadlessApp()
        host._tasks = Mock()
        gateway = host.feishu_agent
        self.assertTrue(gateway._task_backed)
        gateway._binding = dict(app_id='app', owner_id='owner')
        gateway._inbox = Inbox(self.root/'inbox.db')
        host.tasks.list.return_value = dict(items=[])
        gateway._recover_task_answers(); gateway._recover_continuations()
        host.tasks.list.assert_called_once_with('feishu')
        row = dict(kind='text', content='question', app='app', chat='chat', sender='owner')
        with patch.object(gateway, '_run_agent', return_value='task answer') as execute:
            self.assertEqual(gateway._answer(row), 'task answer')
        execute.assert_called_once()
        legacy = FeishuAgent(Mock())
        legacy._recover_task_answers(); legacy._recover_continuations()
        legacy.host.tasks.list.assert_not_called()
        legacy._inbox = gateway._inbox
        legacy.host.run_channel_chat.return_value = 'legacy answer'
        self.assertEqual(legacy._answer(row), 'legacy answer')
        legacy.host.run_channel_chat.assert_called_once()

    def test_catalog_partial_success_and_failures_keep_existing_contract(self):
        from desk_companion import workbench_queries as q
        from desk_companion.wiki_connection import CatalogIncomplete
        with patch('desk_companion.skill_catalog.list_skills', return_value=[]), \
             patch('desk_companion.github.list_owned_repos', side_effect=RuntimeError('github failed')):
            result = q.load_composer_options()
        self.assertTrue(result['ok']); self.assertFalse(result['github']['ok'])
        with patch('desk_companion.skill_catalog.list_skills', side_effect=RuntimeError('skills failed')):
            self.assertEqual(q.load_composer_options(), dict(ok=False, error='skills failed'))
        incomplete = CatalogIncomplete([dict(token='a', title='wiki version')], RuntimeError('timeout'))
        with patch('desk_companion.feishu_docs.list_my_docx', return_value=[dict(token='a', title='document')]), \
             patch('desk_companion.wiki_connection.catalog', side_effect=incomplete), \
             patch('desk_companion.wiki_connection.load_settings', return_value=dict(wiki_url='url')), \
             patch('desk_companion.wiki_connection.enrich_catalog', side_effect=lambda rows: rows):
            result = q.list_feishu_docs()
        self.assertTrue(result['ok']); self.assertTrue(result['wiki_connected'])
        self.assertEqual(result['items'], [dict(token='a', title='wiki version')])
        self.assertEqual(str(incomplete), result['error'])

    def test_feishu_recovery_and_dispatch_do_not_import_api_host(self):
        script = '''
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from desk_companion.feishu_agent import FeishuAgent, Inbox
from desk_companion.paths import data_root
host = SimpleNamespace(tasks=Mock())
host.tasks.list.return_value = dict(items=[])
gateway = FeishuAgent(host, task_backed=True)
gateway._binding = dict(app_id='app', owner_id='owner')
gateway._inbox = Inbox(data_root()/'inbox.db')
gateway._recover_task_answers()
gateway._recover_continuations()
host.tasks.list.assert_called_once_with('feishu')
with patch.object(gateway, '_run_agent', return_value='answer') as run:
    assert gateway._answer(dict(kind='text',content='question',app='app',chat='chat',sender='owner')) == 'answer'
    run.assert_called_once()
assert_no_gui()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True,
            text=True, encoding='utf-8', timeout=20, env={**os.environ, 'PYTHONUTF8':'1'},
            cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_invalid_saved_state_fails_before_starting_managers_or_feishu(self):
        saved = self.root/'user_state.json'
        saved.write_text('{invalid', encoding='utf-8')
        result = subprocess.run([sys.executable, '-u', '-m', 'tests.headless_backend_fixture',
            '--port', '0', '--token', 'offline-test-token'], capture_output=True,
            text=True, encoding='utf-8', timeout=20, env={**os.environ, 'PYTHONUTF8':'1'},
            cwd=Path(__file__).resolve().parents[1])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('不是合法 JSON', result.stderr)
        self.assertNotIn('local_api ready', result.stdout)
        self.assertIn('backend GUI boundary verified', result.stdout)
        self.assertFalse((self.root/'memory/tasks.sqlite3').exists())
        self.assertEqual(saved.read_text(encoding='utf-8'), '{invalid')

    def test_real_backend_start_rpc_chat_and_shutdown_without_gui(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        output = self.root/'backend.log'
        with output.open('w', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, '-u', '-m', 'tests.headless_backend_fixture',
                '--port', str(port), '--token', 'offline-test-token'], stdout=log, stderr=log,
                env={**os.environ, 'PYTHONUTF8':'1'}, cwd=Path(__file__).resolve().parents[1])
            try:
                deadline = time.monotonic()+35
                while time.monotonic()<deadline:
                    self.assertIsNone(child.poll(), output.read_text(encoding='utf-8'))
                    try:
                        status, _ = self.http(port, '/health')
                        if status == 200: break
                    except OSError: pass
                    time.sleep(.05)
                else: self.fail('backend readiness timeout: '+output.read_text(encoding='utf-8'))
                asyncio.run(self.check_rpc(port))
                self.assertEqual(self.http(port, '/shutdown')[0], 401)
                self.assertIsNone(child.poll())
                self.assertEqual(self.http(port, '/shutdown', 'offline-test-token')[0], 200)
                self.assertEqual(child.wait(timeout=25), 0, output.read_text(encoding='utf-8'))
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=10)
        self.assertIn('backend GUI boundary verified', output.read_text(encoding='utf-8'))

    @staticmethod
    def http(port, path, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', path, headers={'Authorization':'Bearer '+token} if token else {})
            response = connection.getresponse()
            return response.status, response.read()
        finally: connection.close()

    async def check_rpc(self, port):
        url = f'ws://127.0.0.1:{port}/ws?token=offline-test-token'
        with self.assertRaises(ConnectionClosed):
            async with connect(url.replace('offline-test-token','wrong'), proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', method='load_persona')))
                await ws.recv()
        async with connect(url, proxy=None) as ws:
            for method in ('load_composer_options','list_mcp_tools','list_feishu_docs','load_log_errors',
                           'load_skills','load_usage','load_github','load_persona','clear_chat','load_work_board'):
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method)))
                frame = json.loads(await asyncio.wait_for(ws.recv(), 10))
                self.assertEqual(frame['id'], method)
                self.assertTrue(frame['result']['ok'], frame)
                self.assertTrue(frame['result']['result']['ok'], frame)
            action = dict(type='create', request_id='headless-board-create', fields=dict(title='offline business item'))
            await ws.send(json.dumps(dict(type='rpc', id='create-work', method='change_work_item', args=dict(action=action))))
            result = json.loads(await asyncio.wait_for(ws.recv(), 10))['result']['result']
            self.assertTrue(result['ok'], result)
            item = result['items'][0]
            action = dict(type='move', request_id='headless-board-move', id=item['id'], version=item['version'], state='review')
            await ws.send(json.dumps(dict(type='rpc', id='move-work', method='change_work_item', args=dict(action=action))))
            result = json.loads(await asyncio.wait_for(ws.recv(), 10))['result']['result']
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['items'][0]['state'], 'review')
            for method in ('close_board','send_chat','does_not_exist'):
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method)))
                self.assertFalse(json.loads(await ws.recv())['result']['ok'])
            await ws.send(json.dumps(dict(type='chat', text='offline question', chips={},
                sampling=dict(reasoning_effort='low',temperature=.5,top_p=1))))
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 25))
                if frame['type'] in {'done','error'}: break
            self.assertEqual(frame, dict(type='done', data='本地流式回归正常。'))
            await ws.send(json.dumps(dict(type='rpc', id='history', method='load_chat_log')))
            history = json.loads(await ws.recv())['result']['result']['items']
            self.assertEqual(len(history), 2)
