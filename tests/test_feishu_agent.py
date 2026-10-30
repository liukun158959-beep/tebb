"""飞书通道离线回归：真实 Agent 工具循环与 CLI 子进程协议，全部临时数据。"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from atlas.testing import FakeLLM
from desk_companion import assistant, memory, logutil
from desk_companion.feishu_agent import EVENT_KEY, FeishuAgent, Inbox, accepted, chunks, identify
from desk_companion.feishu_connection import ConnectionFailure, classify
from desk_companion.local_api.host import HeadlessApp
from desk_companion.local_api import server

BINDING = {"profile": "test-profile", "app_id": "test-app", "app_name": "测试机器人",
           "owner_id": "ou_owner", "owner_name": "测试用户"}


def event(message="om_1", text="打个招呼", **changes):
    result = {"type": EVENT_KEY, "sender_type": "user", "sender_id": "ou_owner", "chat_type": "p2p",
              "message_id": message, "chat_id": "oc_private", "content": text, "message_type": "text",
              "create_time": str(int(time.time() * 1000))}
    return {**result, **changes}


class FeishuAgentTests(unittest.TestCase):
    def test_stop_waits_for_inflight_worker_database_cleanup(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        def work():
            with self.gateway._inbox.connect() as db:
                entered.set()
                release.wait(3)
                db.execute('SELECT 1')
        self.gateway._worker = threading.Thread(target=work)
        self.gateway._worker.start()
        self.assertTrue(entered.wait(2))
        closing = threading.Thread(target=lambda: (self.gateway.stop(False), stopped.set()))
        closing.start()
        try:
            self.assertFalse(stopped.wait(.1), 'stop returned with the database still in use')
        finally:
            release.set()
            closing.join(2)
            self.gateway._worker.join(2)
        self.assertTrue(stopped.is_set())
        self.assertFalse(self.gateway._worker.is_alive())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": str(self.root)}).start()
        patch.object(logutil, "LOG_PATH", self.root / "desk_companion.log").start()
        self.host = Mock()
        self.host.run_channel_chat.return_value = "你好。"
        self.gateway = FeishuAgent(self.host)
        self.gateway._binding = BINDING.copy()
        self.gateway._inbox = Inbox(self.root / "inbox.sqlite3")
        self.addCleanup(self.gateway.stop, False)

    def queue(self, payload=None):
        self.gateway._inbox.add("test-app", payload or event())
        return self.gateway._inbox.take("test-app")

    def test_only_fresh_owner_private_messages_are_accepted(self):
        self.assertTrue(accepted(event(), "ou_owner"))
        for changes in ({"sender_id": "ou_intruder"}, {"sender_type": "bot"}, {"chat_type": "group"},
                        {"message_id": ""}, {"chat_id": ""}, {"create_time": "garbage"},
                        {"create_time": "1"}, {"type": "other.event"}):
            self.assertFalse(accepted(event(**changes), "ou_owner"), changes)
        self.assertFalse(accepted({"schema": "2.0", "event": event()}, "ou_owner"))

    def test_message_id_deduplicates_across_restart_and_event_ids(self):
        inbox = self.gateway._inbox
        self.assertTrue(inbox.add("test-app", event(event_id="delivery1")))
        restarted = Inbox(inbox.path)
        self.assertFalse(restarted.add("test-app", event(event_id="delivery2")))
        self.assertTrue(restarted.add("other-app", event()))
        sid = inbox.session("test-app", "oc_private", "ou_owner")
        self.assertEqual(restarted.session("test-app", "oc_private", "ou_owner"), sid)
        self.assertNotEqual(restarted.session("other-app", "oc_private", "ou_owner"), sid)

    def test_interrupted_tools_are_not_reexecuted_and_old_pending_messages_expire(self):
        self.queue()
        inbox = self.gateway._inbox
        inbox.recover("test-app")
        row = inbox.take("test-app")
        self.assertEqual(row["state"], "answered")
        self.assertIn("避免重复执行", row["answer"])
        inbox.update("test-app", row["id"], state="sent")
        inbox.add("test-app", event("om_old"))
        with inbox.connect() as db:
            db.execute("UPDATE messages SET updated=? WHERE id='om_old'", (time.time() - 700,))
        self.assertIsNone(inbox.take("test-app"))

    def test_partial_reply_retries_only_unsent_chunks_without_rerunning_agent(self):
        self.host.run_channel_chat.return_value = "字" * 7200
        row = self.queue()
        calls = []
        def send(args):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError("network")
            return '{"ok":true,"identity":"bot","data":{}}'
        with patch("desk_companion.feishu_agent._run_lark", side_effect=send):
            with self.assertRaises(RuntimeError):
                self.gateway.process_message(row)
            retry = self.gateway._inbox.take("test-app")
            self.assertEqual(retry["state"], "answered")
            self.assertEqual(retry["sent"], 1)
            self.gateway.process_message(retry)
        self.host.run_channel_chat.assert_called_once()
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1][-1], calls[2][-1])
        self.assertNotEqual(calls[0][-1], calls[1][-1])
        self.assertTrue(all("--as" in args and args[args.index("--as") + 1] == "bot" for args in calls))
        self.assertIsNone(self.gateway._inbox.take("test-app"))
        self.assertEqual("".join(chunks("😀" * 9000)), "😀" * 9000)

    def test_new_skill_knowledge_and_mcp_commands_reach_expected_capabilities(self):
        original = self.gateway._inbox.session("test-app", "oc_private", "ou_owner")
        def command(text):
            return self.gateway._answer({"app": "test-app", "chat": "oc_private", "sender": "ou_owner", "kind": "text", "content": text})
        self.assertIn("旧历史", command("/new"))
        self.assertNotEqual(original, self.gateway._inbox.session("test-app", "oc_private", "ou_owner"))
        command("/skill weekly-retro 整理本周工作")
        self.assertEqual(self.host.run_channel_chat.call_args.args[2], {"skills": ["weekly-retro"]})
        command("/kb\n有哪些准备事项")
        self.assertTrue(self.host.run_channel_chat.call_args.args[3])
        command("/mcp weather/get_weather 北京天气")
        self.assertEqual(self.host.run_channel_chat.call_args.args[2], {"mcp": [{"server": "weather", "tool": "get_weather"}]})
        calls = self.host.run_channel_chat.call_count
        self.assertIn("用法", command("/skill"))
        self.assertEqual(self.host.run_channel_chat.call_count, calls)

    def test_cli_ready_ndjson_duplicate_filter_and_graceful_stdin_close(self):
        stub = self.root / "cli_stub.py"
        events = [event(sender_id="ou_intruder"), event(chat_type="group"), event(sender_type="bot"), event(), event(event_id="duplicate")]
        stub.write_text("import sys, json\nprint('[event] ready event_key=im.message.receive_v1', file=sys.stderr, flush=True)\n"
                        + f"events=json.loads({json.dumps(json.dumps(events, ensure_ascii=False), ensure_ascii=False)})\n"
                        + "for value in events: print(json.dumps(value), flush=True)\nsys.stdin.read()\n", encoding="utf-8")
        replied = threading.Event()
        def reply(_args):
            replied.set()
            return '{"ok":true,"data":{}}'
        with patch("desk_companion.feishu_agent.identify", return_value=BINDING.copy()), \
             patch("desk_companion.feishu_agent._lark_cmd", return_value=[sys.executable, str(stub)]), \
             patch("desk_companion.feishu_agent._run_lark", side_effect=reply):
            self.gateway.enable()
            self.assertTrue(replied.wait(5))
            self.assertTrue(self.gateway.status()["connected"])
            process = self.gateway._process
            self.gateway.stop()
            self.assertEqual(process.poll(), 0)
        self.host.run_channel_chat.assert_called_once()
        saved = json.loads((self.root / "feishu_agent.json").read_text(encoding="utf-8"))
        self.assertFalse(saved["enabled"])
        self.assertNotIn("secret", saved)

    def test_remote_connection_failure_is_visible_and_does_not_run_agent(self):
        done = threading.Event()
        process = SimpleNamespace(stderr=io.StringIO(json.dumps({"ok": False, "error": {
            "type": "validation", "subtype": "failed_precondition", "message": "remote exists"}}, indent=2)))
        self.gateway._stderr(process, done)
        self.assertTrue(done.is_set())
        self.assertFalse(self.gateway.status()["connected"])
        self.assertIn("本机 CLI 无法远程停止", self.gateway.status()["error"])
        self.host.run_channel_chat.assert_not_called()

    def test_quitting_during_identity_check_does_not_spawn_a_new_listener(self):
        started, release = threading.Event(), threading.Event()
        failures = []
        def identity():
            started.set()
            release.wait(3)
            return BINDING.copy()
        def enable():
            try:
                self.gateway.enable()
            except RuntimeError as exc:
                failures.append(str(exc))
        with patch("desk_companion.feishu_agent.identify", side_effect=identity), \
             patch("desk_companion.feishu_agent._lark_cmd") as spawn:
            opening = threading.Thread(target=enable)
            opening.start()
            self.assertTrue(started.wait(2))
            closing = threading.Thread(target=self.gateway.stop, args=(False,))
            closing.start()
            self.assertTrue(self.gateway._closing.wait(2))
            release.set()
            opening.join(3)
            closing.join(3)
            spawn.assert_not_called()
        self.assertTrue(failures)
        self.assertFalse((self.root / "feishu_agent.json").exists())

    def test_real_agent_tool_loop_and_history_do_not_expose_desktop_context(self):
        from desk_companion.tasks import TaskManager
        host = HeadlessApp()
        desktop = host.state.session_id
        memory.append_chat("user", "仅在桌面保存的内容", desktop)
        host._tasks = TaskManager(host, command=[sys.executable, "-u", "-c", "exec(open('tests/worker_fixture.py', encoding='utf-8').read())"])
        self.addCleanup(host._tasks.shutdown)
        remote = "feishu-isolated-test"
        answer = host.run_channel_chat("离线查询", remote, {})
        self.assertEqual(answer, "本地流式回归正常。")
        self.assertTrue((self.root / "tool_called").exists())
        self.assertNotIn("仅在桌面保存的内容", (self.root / "seen.json").read_text("utf-8"))
        self.assertEqual(host.state.session_id, desktop)
        self.assertEqual(json.loads((self.root / "user_state.json").read_text(encoding="utf-8"))["session_id"], desktop)
        self.assertEqual(memory.list_chat(remote)[-1]["react_loops"], 2)
        self.assertEqual(len(memory.list_chat(desktop)), 1)
        self.assertFalse(host._agent_running)

    def test_remote_wait_does_not_block_desktop_read_rpc(self):
        host = HeadlessApp()
        desktop = host.state.session_id
        started, release, read_done = threading.Event(), threading.Event(), threading.Event()
        result = {}
        def run(*_args, **_kwargs):
            started.set()
            self.assertTrue(release.wait(3))
            return {"state": "succeeded", "answer": "远程回答"}
        def read():
            result.update(server._dispatch("load_chat_log", {}))
            read_done.set()
        host._tasks = Mock()
        host._tasks.wait.side_effect = run
        with patch.object(server, "HOST", host):
            remote = threading.Thread(target=host.run_channel_chat, args=("远程问题", "feishu-test", {}))
            remote.start()
            self.assertTrue(started.wait(2))
            reader = threading.Thread(target=read)
            reader.start()
            try:
                self.assertTrue(read_done.wait(.5))
            finally:
                release.set()
                remote.join(3)
                reader.join(3)
        self.assertTrue(read_done.is_set())
        self.assertEqual(result["result"]["session_id"], desktop)

    def test_settings_persist_without_switching_default_profile_and_rebind_is_explicit(self):
        self.gateway._save(False)
        profiles = {"ok": True, "profiles": [{"name": "dedicated"}, {"name": "test-profile"}]}
        with patch.object(self.gateway, "profiles", return_value=profiles), patch("desk_companion.feishu_agent._run_lark") as cli:
            result = self.gateway.save_settings("dedicated", False, False, 5, 60)
            cli.assert_not_called()
        self.assertEqual(result["binding"], {})
        fresh = FeishuAgent(self.host)
        self.assertEqual(fresh.settings(), {"profile": "dedicated", "auto_start": False,
            "auto_reconnect": False, "retry_min": 5, "retry_max": 60})
        fresh._binding = BINDING.copy()
        fresh._save(True)
        self.assertEqual(fresh.settings()["retry_max"], 60)
        with patch.object(fresh, "enable") as enable:
            fresh.autostart()
            enable.assert_not_called()

    def test_invalid_settings_and_changes_while_listening_are_rejected(self):
        for low, high in ((0, 30), (30, 2), (2, 301), (True, 30), (2.5, 30)):
            with self.assertRaises(RuntimeError):
                self.gateway.save_settings("test-profile", True, True, low, high)
        self.gateway._supervisor = Mock()
        self.gateway._supervisor.is_alive.return_value = True
        with self.assertRaisesRegex(RuntimeError, "先停止"):
            self.gateway.save_settings("test-profile", True, True, 2, 30)
        self.gateway._supervisor = None

    def test_credential_update_uses_stdin_same_named_profile_and_redacts_errors(self):
        profiles = {"profiles": [{"name": "test-profile", "appId": "test-app", "brand": "feishu"}]}
        with patch.object(self.gateway, "profiles", return_value=profiles), \
             patch("desk_companion.feishu_agent._run_lark", return_value="{}") as cli:
            self.assertTrue(self.gateway.update_credentials("test-profile", "test-only-secret")["ok"])
            args, kwargs = cli.call_args
            self.assertNotIn("test-only-secret", args[0])
            self.assertEqual(kwargs["stdin"], "test-only-secret\n")
            self.assertIn("--name", args[0])
            self.assertNotIn("--use", args[0])
        with patch.object(self.gateway, "profiles", return_value=profiles), \
             patch("desk_companion.feishu_agent._run_lark", side_effect=RuntimeError("test-only-secret")):
            with self.assertRaises(RuntimeError) as failure:
                self.gateway.update_credentials("test-profile", "test-only-secret")
            self.assertNotIn("test-only-secret", str(failure.exception))
        self.assertFalse((self.root / "feishu_agent.json").exists())

    def test_hermes_environment_allows_only_existing_profile_secret_update(self):
        profiles = {"profiles": [{"name": "test-profile", "appId": "test-app", "brand": "feishu"}]}
        with patch.dict(os.environ, {"HERMES_HOME": "test-hermes-home"}), \
             patch.object(self.gateway, "profiles", return_value=profiles), \
             patch("desk_companion.feishu_agent._run_lark", return_value="{}") as cli:
            self.gateway.update_credentials("test-profile", "test-only-secret")
            command = cli.call_args.args[0]
            self.assertIn("--force-init", command)
            self.assertEqual(command[:2], ["--profile", "test-profile"])
            self.assertEqual(command[command.index("--name") + 1], "test-profile")
            self.assertEqual(command[command.index("--app-id") + 1], "test-app")
            self.assertNotIn("--new", command)
            self.assertNotIn("--use", command)
            self.assertEqual(os.environ["HERMES_HOME"], "test-hermes-home")
            cli.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "不存在"):
                self.gateway.update_credentials("unknown", "test-only-secret")
            cli.assert_not_called()

    def test_invalid_secret_after_cli_save_has_actionable_redacted_error(self):
        profiles = {"profiles": [{"name": "test-profile", "appId": "test-app", "brand": "feishu"}]}
        with patch.object(self.gateway, "profiles", return_value=profiles), \
             patch("desk_companion.feishu_agent._run_lark", side_effect=["", RuntimeError("invalid_client test-only-secret")]):
            with self.assertRaisesRegex(RuntimeError, "飞书校验未通过") as failure:
                self.gateway.update_credentials("test-profile", "test-only-secret")
            self.assertNotIn("test-only-secret", str(failure.exception))

    def test_connection_check_distinguishes_remote_occupancy_and_invalid_credentials(self):
        for count, running in ((0, False), (1, False), (1, True)):
            replies = [json.dumps({"ok": True, "data": {"online_instance_cnt": count}}),
                       json.dumps({"apps": [{"app_id": "test-app", "running": running}]}),
                       json.dumps({"appId": "test-app"})]
            with patch("desk_companion.feishu_agent._run_lark", side_effect=replies) as cli:
                result = self.gateway.check_connection("test-profile")
                self.assertTrue(result["ok"])
                self.assertEqual(result["count"], count)
                self.assertEqual(result["local_running"], running)
                self.assertEqual(cli.call_args_list[0].args[0][:2], ["--profile", "test-profile"])
        with patch("desk_companion.feishu_agent._run_lark", side_effect=RuntimeError("invalid_client test-only-secret")):
            result = self.gateway.check_connection("test-profile")
            self.assertFalse(result["ok"])
            self.assertIn("App Secret 无效", result["error"])
            self.assertNotIn("test-only-secret", result["error"])

    def test_identity_uses_selected_profile_and_not_the_global_default(self):
        values = [{"profile": "dedicated", "appId": "test-app"}, {"appId": "test-app", "identities": {
            "bot": {"available": True, "verified": True}, "user": {"available": True, "openId": "ou_owner"}}}]
        with patch("desk_companion.feishu_agent._run_lark", side_effect=[json.dumps(row) for row in values]) as cli:
            self.assertEqual(identify("dedicated")["profile"], "dedicated")
            self.assertTrue(all(call.args[0][:2] == ["--profile", "dedicated"] for call in cli.call_args_list))

    def test_successful_cli_exit_with_invalid_bot_secret_has_actionable_error(self):
        # auth status exits successfully even when an individual identity fails verification.
        for message in ("Bot identity: verify failed: The client secret is invalid.", "invalid_client"):
            values = [{"profile": "test-profile", "appId": "test-app"}, {"identities": {
                "bot": {"available": False, "verified": False, "message": message},
                "user": {"available": True, "openId": "ou_owner"}}}]
            with self.subTest(message=message), patch("desk_companion.feishu_agent._run_lark",
                    side_effect=[json.dumps(row) for row in values]):
                with self.assertRaisesRegex(RuntimeError, "App Secret 无效.*长连接设置"):
                    identify("test-profile")

    def test_bot_error_does_not_echo_untrusted_cli_message(self):
        values = [{"profile": "test-profile", "appId": "test-app"}, {"identities": {
            "bot": {"available": False, "verified": False, "message": "invalid_client private-secret"}}}]
        with patch("desk_companion.feishu_agent._run_lark", side_effect=[json.dumps(row) for row in values]):
            with self.assertRaises(RuntimeError) as failure:
                identify("test-profile")
        self.assertNotIn("private-secret", str(failure.exception))

    def test_network_retry_uses_saved_interval_and_can_be_disabled(self):
        stub = self.root / "network_exit.py"
        stub.write_text("import sys\nsys.exit(4)\n", encoding="utf-8")
        for reconnect in (True, False):
            with self.subTest(reconnect=reconnect):
                gateway = FeishuAgent(self.host)
                (self.root / "feishu_agent.json").write_text(json.dumps({"profile": "test-profile",
                    "auto_reconnect": reconnect, "retry_min": 7, "retry_max": 60}), encoding="utf-8")
                with patch("desk_companion.feishu_agent.identify", return_value=BINDING.copy()), \
                     patch("desk_companion.feishu_agent._lark_cmd", return_value=[sys.executable, str(stub)]), \
                     patch.object(gateway._stop, "wait", return_value=True) as wait:
                    try:
                        gateway.enable()
                        gateway._supervisor.join(3)
                        self.assertFalse(gateway._supervisor.is_alive())
                        if reconnect:
                            wait.assert_called_once_with(7)
                        else:
                            wait.assert_not_called()
                            self.assertIn("自动重连已关闭", gateway.status()["error"])
                    finally:
                        gateway.stop(False)

    def test_transient_bot_verification_has_safe_retryable_reason(self):
        values = [{"profile": "test-profile", "appId": "test-app"}, {"identities": {
            "bot": {"available": False, "verified": False, "message": "verify failed: TLS timeout private-secret"}}}]
        with patch("desk_companion.feishu_agent._run_lark", side_effect=[json.dumps(row) for row in values]):
            with self.assertRaises(ConnectionFailure) as failure:
                identify("test-profile")
        self.assertTrue(failure.exception.retryable)
        self.assertEqual(failure.exception.kind, "network")
        self.assertNotIn("private-secret", str(failure.exception))
        self.assertEqual(classify({"status": "not_configured"}, verification=True).kind, "configuration")

    def test_startup_identity_recovers_after_transient_failure_without_manual_login(self):
        self.gateway._save(True)
        config = json.loads((self.root/"feishu_agent.json").read_text(encoding="utf-8"))
        config.update(profile="test-profile", retry_min=1, retry_max=4)
        (self.root/"feishu_agent.json").write_text(json.dumps(config))
        ready = threading.Event()
        with patch("desk_companion.feishu_agent.identify", side_effect=[classify("TLS timeout"), BINDING.copy()]) as identity, \
             patch.object(self.gateway, "_start", side_effect=ready.set):
            self.gateway.autostart()
            self.assertEqual(self.gateway.status()["state"], "reconnecting")
            self.assertGreater(self.gateway.status()["next_retry_at"], time.time())
            self.assertTrue(ready.wait(3))
            self.gateway._identity_retry.join(1)
        self.assertEqual(identity.call_count, 2)
        self.assertEqual(self.gateway._binding, BINDING)
        self.host.run_channel_chat.assert_not_called()

    def test_identity_retry_can_be_cancelled_and_never_changes_owner(self):
        self.gateway._save(True)
        with patch("desk_companion.feishu_agent.identify", side_effect=classify("network failed")) as identity, \
             patch.object(self.gateway, "_start") as start:
            self.gateway.enable()
            self.gateway.stop()
            self.assertFalse(self.gateway._identity_retry.is_alive())
            self.assertEqual(self.gateway.status()["next_retry_at"], 0)
            identity.assert_called_once()
            start.assert_not_called()
        self.assertFalse(json.loads((self.root/"feishu_agent.json").read_text(encoding="utf-8"))["enabled"])

    def test_permanent_credentials_do_not_retry_or_echo_cli_secrets(self):
        with patch("desk_companion.feishu_agent.identify", side_effect=classify("invalid_client private-secret")), \
             patch.object(self.gateway, "_start") as start:
            with self.assertRaisesRegex(ConnectionFailure, "App Secret 无效"):
                self.gateway.enable()
            start.assert_not_called()
        self.assertIsNone(self.gateway._identity_retry)
        self.assertEqual(self.gateway.status()["state"], "error")
        self.assertNotIn("private-secret", json.dumps(self.gateway.status()))

    def test_retry_refuses_changed_application_or_owner(self):
        self.gateway._save(True)
        config=json.loads((self.root/"feishu_agent.json").read_text(encoding="utf-8"))
        config.update(profile="test-profile", retry_min=1, retry_max=4)
        (self.root/"feishu_agent.json").write_text(json.dumps(config))
        with patch("desk_companion.feishu_agent.identify", side_effect=[classify("network failed"), {**BINDING, "owner_id": "ou_other"}]), \
             patch.object(self.gateway, "_start") as start:
            self.gateway.enable()
            self.gateway._identity_retry.join(3)
            start.assert_not_called()
        self.assertEqual(self.gateway.status()["failure_kind"], "binding")
        self.assertEqual(json.loads((self.root/"feishu_agent.json").read_text(encoding="utf-8"))["binding"]["owner_id"], "ou_owner")

    def test_api_setup_network_exit_one_reconnects_then_accepts_ready(self):
        stub=self.root/"flaky_event.py"
        counter=self.root/"attempts"
        stub.write_text("import sys,json\nfrom pathlib import Path\n" +
            f"p=Path({str(counter)!r})\nn=int(p.read_text())+1 if p.exists() else 1\np.write_text(str(n))\n" +
            "if n==1:\n print(json.dumps({'ok':False,'error':{'type':'network','message':'TLS timeout private-secret'}}),file=sys.stderr,flush=True)\n sys.exit(1)\n" +
            f"print('[event] ready event_key={EVENT_KEY}',file=sys.stderr,flush=True)\nsys.stdin.read()\n", "utf-8")
        (self.root/"feishu_agent.json").write_text(json.dumps({"retry_min":1,"retry_max":4}))
        with patch("desk_companion.feishu_agent.identify", return_value=BINDING.copy()), \
             patch("desk_companion.feishu_agent._lark_cmd", return_value=[sys.executable,str(stub)]), \
             patch.object(self.gateway,"_consume_menu"):
            self.gateway.enable()
            self.assertTrue(self.gateway._ready.wait(5))
            self.assertEqual(counter.read_text(), "2")
            self.assertTrue(self.gateway.status()["connected"])
            self.assertTrue(any(e["stage"]=="reconnecting" and e["kind"]=="network" for e in self.gateway.status()["connection_events"]))
            self.assertNotIn("private-secret", json.dumps(self.gateway.status()))
            self.gateway.stop()

    def test_local_connection_check_is_scoped_to_selected_application(self):
        values=[{"ok":True,"data":{"online_instance_cnt":0}},
                {"apps":[{"app_id":"other-app","running":True},{"app_id":"test-app","running":False}]},
                {"appId":"test-app"}]
        with patch("desk_companion.feishu_agent._run_lark", side_effect=[json.dumps(v) for v in values]):
            self.assertFalse(self.gateway.check_connection("test-profile")["local_running"])


if __name__ == "__main__":
    unittest.main()
