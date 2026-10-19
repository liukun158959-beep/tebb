"""真实本地 WebSocket 与无头对话链路；模型和用户文件全部隔离。"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from atlas.testing import FakeLLM

from desk_companion import assistant, memory, state, usage
from desk_companion.local_api import server
from desk_companion.local_api.host import HeadlessApp


class LocalApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": str(root)}).start()
        patch.object(memory, "memory_path", return_value=root / "memory" / "chat.jsonl").start()
        patch.object(state, "state_path", return_value=root / "state.json").start()
        patch.object(usage, "usage_path", return_value=root / "usage.jsonl").start()
        self.host = HeadlessApp()
        from desk_companion.tasks import TaskManager
        self.host._tasks = TaskManager(self.host, command=[sys.executable, "-u", "-c", "exec(open('tests/worker_fixture.py', encoding='utf-8').read())"])
        self.addCleanup(self.host._tasks.shutdown)
        with patch.object(assistant, "require_llm_env", return_value={
            "ATLAS_API_KEY": "test-only", "ATLAS_BASE_URL": "http://example.invalid/v1", "ATLAS_MODEL": "fake",
        }):
            self.host.agent = assistant.build_agent(self.host)
        self.host.agent.llm = FakeLLM(["本地流式回归正常。"])
        self.host.agent.llm.sampling = None
        patch.object(self.host, "engage_model").start()
        patch.object(server, "HOST", self.host).start()
        patch.object(server, "TOKEN", "test-token").start()
        self.service = await serve(server._handler, "127.0.0.1", 0, process_request=server._health)
        self.addAsyncCleanup(self.stop_service)
        self.port = self.service.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/ws?token=test-token"

    async def stop_service(self):
        self.service.close()
        await self.service.wait_closed()

    async def test_health_and_readonly_rpc(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        await writer.wait_closed()
        self.assertIn(b"200 OK", response)
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps({"type": "rpc", "id": "1", "method": "load_chat_log", "args": {}}))
            message = json.loads(await asyncio.wait_for(ws.recv(), 5))
        self.assertTrue(message["result"]["ok"])
        self.assertTrue(message["result"]["result"]["ok"])

    async def test_headless_sessions_clear_and_isolate_data_without_gui_methods(self):
        from contextlib import ExitStack
        memory.append_chat('user', 'selected question', self.host.state.session_id)
        memory.append_chat('user', 'other question', 'other-session')
        original = self.host.state.session_id

        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args or {})))
                message = json.loads(await asyncio.wait_for(ws.recv(), 10))
            self.assertTrue(message['result']['ok'], message)
            return message['result']['result']

        # Headless clear must work even though pet is None.
        self.assertIsNone(self.host.pet)
        self.assertTrue((await rpc('clear_chat'))['ok'])
        self.assertEqual(memory.list_chat(original), [])
        self.assertEqual(len(memory.list_chat('other-session')), 1)
        with ExitStack() as stack:
            for method in ('clear_chat_history', 'new_chat_session', 'switch_chat_session', 'board_chat', 'board_memory'):
                stack.enter_context(patch.object(self.host, method, side_effect=AssertionError('inherited GUI method')))
            self.assertEqual((await rpc('new_chat_session'))['session_id'], original)
            self.assertEqual((await rpc('switch_chat_session', {'session_id': 'other-session'}))['session_id'], 'other-session')
            loaded = await rpc('load_chat_log')
            self.assertEqual(loaded['items'][0]['text'], 'other question')
            self.assertEqual((await rpc('load_memory'))['total'], 1)
            self.host.conversation.running = True
            for method in ('clear_chat', 'new_chat_session', 'switch_chat_session'):
                result = await rpc(method, {'session_id': original} if method == 'switch_chat_session' else None)
                self.assertFalse(result['ok'])
            self.assertTrue((await rpc('load_chat_log'))['ok'])
            self.host.conversation.running = False
            self.assertTrue((await rpc('clear_chat'))['ok'])
            self.assertEqual(self.host.state.session_id, 'other-session')

    async def test_session_rpc_blocks_pending_and_tail_tasks_but_keeps_reads_available(self):
        import threading
        from desk_companion.tasks import TaskManager
        previous = self.host.tasks
        previous.shutdown()
        with patch.object(TaskManager, '_schedule', return_value=None):
            manager = TaskManager(self.host, path=previous.path)
        self.host._tasks = manager
        self.addCleanup(manager.shutdown)
        sid = self.host.state.session_id
        memory.append_chat('user', 'keep current history', sid)
        memory.append_chat('user', 'other history', 'other-session')
        task = manager.submit('pending request', sid)
        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args or {})))
                value = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertTrue(value['result']['ok'], value)
            return value['result']['result']
        for method in ('new_chat_session', 'switch_chat_session', 'clear_chat'):
            value = await rpc(method, {'session_id': 'other-session'} if method == 'switch_chat_session' else None)
            self.assertFalse(value['ok'], value)
        self.assertEqual(manager.get(task)['state'], 'queued')
        with self.host.turn_lock:
            self.assertTrue((await rpc('load_chat_log'))['ok'])
            self.assertTrue((await rpc('load_memory'))['ok'])
        manager.update(task, state='cancelled')
        manager.active[task] = dict(session=sid, cancel=threading.Event())
        try:
            self.assertFalse((await rpc('clear_chat'))['ok'])
        finally:
            manager.active.pop(task)
        other = manager.submit('other pending request', 'other-session')
        self.assertTrue((await rpc('clear_chat'))['ok'])
        self.assertEqual(self.host.state.session_id, sid)
        self.assertEqual(manager.get(other)['state'], 'queued')
        self.assertEqual(manager.get(task)['state'], 'cancelled')

    async def test_feishu_settings_rpc_reports_invalid_secret_without_echoing_it(self):
        with patch("desk_companion.feishu_agent._run_lark", side_effect=RuntimeError("invalid_client test-only-secret")):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "rpc", "id": "feishu-check",
                    "method": "check_feishu_agent_connection", "args": {"profile": "test-profile"}}))
                raw = await asyncio.wait_for(ws.recv(), 5)
                message = json.loads(raw)
        self.assertTrue(message["result"]["ok"])
        self.assertFalse(message["result"]["result"]["ok"])
        self.assertIn("App Secret 无效", message["result"]["result"]["error"])
        self.assertNotIn("test-only-secret", raw)

    async def test_settings_rpc_calls_independent_service_and_preserves_contracts(self):
        import httpx
        from desk_companion import envconf, model_network
        from contextlib import ExitStack

        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args or {})))
                message = json.loads(await asyncio.wait_for(ws.recv(), 10))
            self.assertEqual(message['id'], method)
            self.assertTrue(message['result']['ok'], message)
            self.assertNotIn('PRIVATE_RPC_KEY', json.dumps(message))
            self.assertNotIn('PRIVATE_PROXY_KEY', json.dumps(message))
            return message['result']['result']

        service = self.host.assistant_settings
        self.assertIs(service.conversation, self.host.conversation)
        with ExitStack() as stack:
            # The data RPC must no longer depend on inherited App configuration methods.
            for method in ('board_persona', 'save_persona', 'board_model', 'board_models',
                           'save_model_entry', 'delete_model_entry', 'use_model', 'save_model', 'test_model'):
                stack.enter_context(patch.object(self.host, method, side_effect=AssertionError('inherited GUI method')))
            rebuild = stack.enter_context(patch.object(self.host.conversation, 'rebuild_agent'))
            binding = stack.enter_context(patch.object(self.host.conversation, 'bind_active_llm'))
            self.assertTrue((await rpc('load_persona'))['ok'])
            self.assertTrue((await rpc('save_persona', {'payload': dict(persona='RPC persona', max_steps=7, nudge_enabled=False)}))['ok'])
            self.assertEqual((await rpc('load_persona'))['persona'], 'RPC persona')
            self.assertEqual(state.load_state(0, 0).persona, 'RPC persona')
            first = await rpc('save_model_entry', {'payload': dict(base_url='https://example.invalid/v1', model='one', api_key='PRIVATE_RPC_KEY')})
            one = first['active']
            self.assertEqual((await rpc('load_model'))['model'], 'one')
            self.assertTrue((await rpc('load_model'))['has_key'])
            second = await rpc('save_model_entry', {'payload': dict(base_url='https://example.invalid/v1', model='two', copy_key_from=one)})
            two = second['items'][1]['id']
            self.assertEqual(len((await rpc('load_models'))['items']), 2)
            self.assertEqual((await rpc('use_model', {'model_id': two}))['active'], two)
            self.assertEqual((await rpc('delete_model_entry', {'model_id': one}))['active'], two)
            self.assertFalse((await rpc('delete_model_entry', {'model_id': two}))['ok'])
            self.assertTrue((await rpc('save_model', {'payload': dict(base_url='https://example.invalid/v1', model='legacy',
                input_cny_per_mtok=1, output_cny_per_mtok=2)}))['ok'])
            self.assertEqual((await rpc('load_model'))['output_cny_per_mtok'], 2.0)
            self.assertEqual(envconf.parse_env_file()['ATLAS_API_KEY'], 'PRIVATE_RPC_KEY')
            def transport(timeout):
                return httpx.Client(timeout=timeout, transport=httpx.MockTransport(lambda _: httpx.Response(200,
                    json=dict(choices=[dict(index=0, finish_reason='stop', message=dict(role='assistant', content='pong'))]))))
            with patch.object(model_network, 'http_client', side_effect=transport):
                result = await rpc('test_model', {'payload': dict(base_url='https://example.invalid/v1', model='probe', id=two)})
            self.assertEqual(result, dict(ok=True, message='连通正常。 回复：pong'))
            self.assertEqual(rebuild.call_count, 2)
            self.assertEqual(binding.call_count, 4)
            self.host.conversation.running = True
            saved = state.state_path().read_bytes()
            self.assertFalse((await rpc('save_persona', {'payload': dict(persona='rejected', max_steps=3, nudge_enabled=True)}))['ok'])
            self.assertEqual(state.state_path().read_bytes(), saved)
            self.host.conversation.running = False
            self.assertEqual((await rpc('load_model_network'))['mode'], 'system')
            result = await rpc('save_model_network', {'payload': dict(mode='custom', proxy_url='http://localhost:7890', password='PRIVATE_PROXY_KEY')})
            self.assertTrue(result['has_password'])
            self.assertEqual((await rpc('load_model_network'))['proxy_url'], 'http://localhost:7890')

    async def test_legacy_save_rpc_preserves_invalid_config_and_reports_saved_initialization_failure(self):
        from desk_companion import envconf
        envconf.write_llm_env(api_key='PRIVATE_RPC_KEY', base_url='https://example.invalid/v1', model='before')
        before_env = envconf.env_path().read_bytes()
        before_state = state.state_path().read_bytes()

        async def save(payload):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id='save', method='save_model', args={'payload': payload})))
                message = json.loads(await asyncio.wait_for(ws.recv(), 10))
            self.assertTrue(message['result']['ok'], message)
            self.assertNotIn('PRIVATE_RPC_KEY', json.dumps(message))
            return message['result']['result']

        payload = dict(base_url='https://example.invalid/v1', model='after',
                       input_cny_per_mtok=1, output_cny_per_mtok=2)
        with patch.object(self.host.conversation, 'rebuild_agent', side_effect=RuntimeError('fixture init error')) as rebuild:
            for change in ({'output_cny_per_mtok': None}, {'input_cny_per_mtok': float('nan')}):
                result = await save({**payload, **change})
                self.assertFalse(result['ok'])
                self.assertEqual(result['save_state'], 'not_saved')
                self.assertEqual(envconf.env_path().read_bytes(), before_env)
                self.assertEqual(state.state_path().read_bytes(), before_state)
                self.assertEqual(self.host.state.model_prices, {})
            rebuild.assert_not_called()
            result = await save(payload)
        self.assertFalse(result['ok'])
        self.assertEqual(result['save_state'], 'saved')
        self.assertIn('已保存', result['error'])
        self.assertIn('初始化失败', result['error'])
        self.assertEqual(envconf.public_llm_env()['model'], 'after')
        self.assertEqual(state.load_state(0, 0).model_prices['after']['output_cny_per_mtok'], 2.0)

    async def test_manual_update_check_rpc_is_available_while_chat_lock_is_held(self):
        with patch("desk_companion.release_info.check_updates", return_value={"ok": True, "latest_version": "0.3.0"}):
            with self.host.turn_lock:
                async with connect(self.url, proxy=None) as ws:
                    await ws.send(json.dumps({"type": "rpc", "id": "update", "method": "check_updates", "args": {}}))
                    message = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertTrue(message["result"]["ok"])
            self.assertEqual(message["result"]["result"]["latest_version"], "0.3.0")

    async def test_terminal_worker_uses_shared_rpc_without_waiting_for_agent_turn_lock(self):
        from desk_companion import terminal, agent_debug
        from unittest.mock import Mock
        import base64
        controller = Mock()
        controller.start.return_value = {"ok": True, "job": {"id": "j1"}}
        controller.read.return_value = {"ok": True, "job": {"id": "j1", "state": "succeeded", "output_bytes": 6, "exit_code": 0},
                                        "data": base64.b64encode(b"hello\n").decode(), "offset": 6}
        tool = next(s for s in terminal.tool_specs(lambda: "worker-session") if s["name"] == "execute_command")
        self.assertEqual(tool["retry_max"], 0)
        self.assertFalse(self.host.agent.tools.get("execute_command").isIdempotent)
        self.assertEqual(self.host.agent.tools.get("execute_command").retry_max, 0)
        with patch.object(terminal, "_manager", controller), patch.dict(os.environ, {
                "DESK_TERMINAL_PORT": str(self.port), "DESK_TERMINAL_TOKEN": "test-token"}):
            with self.host.turn_lock, agent_debug.context(task_id="worker-task"):
                result = await asyncio.wait_for(asyncio.to_thread(tool["func"], {"command": "echo hello"}), 5)
        self.assertEqual(json.loads(result)["output"], "hello\n")
        self.assertEqual(controller.start.call_args.args[1], "worker-session")
        self.assertEqual(controller.start.call_args.args[4:6], ("agent", "worker-task"))

    async def test_attachment_cancel_and_picker_are_available_while_agent_lock_is_held(self):
        import uuid
        ident = uuid.uuid4().hex
        with self.host.turn_lock:
            for method in ("cancel_local_source_request", "pick_local_sources"):
                async with connect(self.url, proxy=None) as ws:
                    await ws.send(json.dumps({"type": "rpc", "id": method, "method": method,
                                              "args": {"request_id": ident}}))
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 5))["result"]
                    self.assertTrue(reply["ok"], reply)
                    if method == "pick_local_sources":
                        self.assertTrue(reply["result"]["cancelled"])

    async def test_video_settings_rpc_does_not_block_on_desktop_turn_and_rejects_bad_values(self):
        with self.host.turn_lock:
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "rpc", "id": "video", "method": "save_video_settings",
                                         "args": {"proxy": "http://127.0.0.1:7890", "cookie_file": ""}}))
                saved = json.loads(await asyncio.wait_for(ws.recv(), 5))
                self.assertTrue(saved["result"]["ok"])
                await ws.send(json.dumps({"type": "rpc", "id": "video", "method": "load_video_settings"}))
                loaded = json.loads(await asyncio.wait_for(ws.recv(), 5))
                self.assertEqual(loaded["result"]["result"]["settings"]["proxy"], "http://127.0.0.1:7890")
                await ws.send(json.dumps({"type": "rpc", "id": "video", "method": "save_video_settings",
                                         "args": {"proxy": "http://user:secret@proxy:80", "cookie_file": ""}}))
                invalid = await asyncio.wait_for(ws.recv(), 5)
                self.assertFalse(json.loads(invalid)["result"]["ok"])
                self.assertNotIn("secret@proxy", invalid)

    async def test_shutdown_requires_exact_authorization_and_only_signals_the_server(self):
        shutdown = asyncio.Event()
        async def request(auth):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            writer.write(("GET /shutdown HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n" + auth + "\r\n").encode())
            await writer.drain()
            result = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            await writer.wait_closed()
            return result
        with patch.object(server, "SHUTDOWN", shutdown):
            self.assertIn(b"401", await request(""))
            self.assertIn(b"401", await request("Authorization: Bearer test-token-suffix\r\n"))
            self.assertFalse(shutdown.is_set())
            self.assertIn(b"200", await request("Authorization: Bearer test-token\r\n"))
            self.assertTrue(shutdown.is_set())

    async def test_chat_stream_persists_metadata_and_releases_busy_state(self):
        frames = []
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps({"type": "chat", "text": "打个招呼", "chips": {},
                                     "sampling": {"reasoning_effort": "low", "temperature": 0.5, "top_p": 1}}))
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
                frames.append(frame)
                if frame["type"] in ("done", "error"):
                    break
        self.assertEqual(frames[-1], {"type": "done", "data": "本地流式回归正常。"})
        self.assertTrue(any(frame["type"] == "token" for frame in frames))
        rows = memory.list_chat(self.host.state.session_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["react_loops"], 2)
        task = self.host.tasks.list()["items"][0]
        events = self.host.tasks.get(task["id"])["events"]
        tool = next(e["data"] for e in events if e["kind"] == "tool_end")
        self.assertIn("input", tool)
        self.assertIn("离线工具结果", json.dumps(tool["result"], ensure_ascii=False))
        self.assertGreater(rows[-1]["total_tokens"], 0)
        self.assertFalse(self.host._agent_running)
        self.assertIsNone(self.host.agent.llm.sampling)

    async def test_unknown_rpc_and_missing_sampling_report_errors(self):
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps({"type": "rpc", "id": "bad", "method": "open_url"}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertFalse(reply["result"]["ok"])
            await ws.send(json.dumps({"type": "chat", "text": "缺参数"}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertEqual(reply["type"], "error")
        self.assertEqual(memory.list_chat(self.host.state.session_id), [])


    async def test_video_page_rpc_runs_independent_tasks_and_continues_after_disconnect(self):
        original = self.host.state.session_id
        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "rpc", "id": method, "method": method, "args": args or {}}))
                envelope = json.loads(await asyncio.wait_for(ws.recv(), 30))["result"]
                self.assertTrue(envelope["ok"], envelope)
                return envelope["result"]
        started = await rpc("start_video_task", {"url": "https://youtu.be/abcdefghijk"})
        task_id = started["task_id"]
        # 提交连接关闭后，独立 worker 仍执行；原桌面会话不被切换。
        finished = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task_id), 30)
        self.assertEqual(finished["state"], "succeeded")
        self.assertEqual(self.host.state.session_id, original)
        self.assertEqual([row["id"] for row in (await rpc("list_video_tasks"))["items"]], [task_id])
        followup = await rpc("continue_video_task", {"task_id": task_id, "text": "继续解释"})
        child = self.host.tasks.get(followup["task_id"])
        self.assertEqual(child["session"], finished["session"])
        self.assertEqual(child["source"]["workflow"], "video")
        self.assertFalse(child["source"]["send_back"])

    async def test_video_login_rpc_roundtrip_returns_only_safe_status_and_validates_platform(self):
        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "rpc", "id": method, "method": method, "args": args or {}}))
                return json.loads(await asyncio.wait_for(ws.recv(), 10))["result"]
        from tests.test_video_login import cookie
        saved = await rpc("save_video_login", {"platform": "Bilibili", "cookies": [cookie()]})
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["result"]["items"][0]["state"], "saved")
        self.assertNotIn("test-private-cookie", json.dumps(saved))
        loaded = await rpc("load_video_login")
        self.assertEqual(loaded["result"]["items"][0]["state"], "saved")
        self.assertNotIn("encrypted", json.dumps(loaded))
        invalid = await rpc("save_video_login", {"platform": "Bilibili", "cookies": [cookie("evil.test")]})
        self.assertFalse(invalid["ok"])
        self.assertNotIn("test-private-cookie", json.dumps(invalid))
        cleared = await rpc("clear_video_login", {"platform": "Bilibili"})
        self.assertEqual(cleared["result"]["items"][0]["state"], "missing")


if __name__ == "__main__":
    unittest.main()
