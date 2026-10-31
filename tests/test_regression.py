"""离线回归：只用临时数据，不操作个人账号、游戏或记忆。"""
from __future__ import annotations

import importlib
import json
import pkgutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import desk_companion
from desk_companion import (
    assistant, automation, board_data, context_pack, facts,
    knowledge, memory, model_catalog, notebook, skill_catalog, web_search,
)
from desk_companion.bridge import Bridge
from desk_companion.local_api import server


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(patch.stopall)
        patch.object(memory, "memory_path", return_value=self.root / "chat.jsonl").start()

    def test_all_backend_modules_import(self):
        for item in pkgutil.walk_packages(desk_companion.__path__, "desk_companion."):
            with self.subTest(module=item.name):
                importlib.import_module(item.name)

    def test_rpc_methods_exist_and_ui_methods_are_excluded(self):
        for name in server.RPC_METHODS:
            with self.subTest(method=name):
                self.assertTrue(callable(getattr(Bridge, name, None)))
        self.assertNotIn("open_url", server.RPC_METHODS)
        self.assertNotIn("close_board", server.RPC_METHODS)
        self.assertFalse(server._dispatch("not_a_method", {})["ok"])

    def test_frontend_rpc_names_exist(self):
        import re
        source = Path(desk_companion.__file__).parents[1] / "client/src/client/App.tsx"
        methods = re.findall(r'(?:backend|info),\s*"([a-z_]+)"', source.read_text(encoding="utf-8"))
        self.assertGreater(len(methods), 15)
        self.assertFalse(set(methods) - server.RPC_METHODS)

    def test_agent_tool_registration_and_one_reply(self):
        from atlas.testing import FakeLLM
        host = SimpleNamespace(state=SimpleNamespace(persona="你是凯尔希。", max_steps=8),
                               ui=lambda fn: fn(), on_llm_delta=lambda _: None,
                               on_stream_status=lambda _: None)
        with patch('desk_companion.model_catalog.require_active', return_value={
            'id':'fixture','api_key':'test-only','base_url':'http://example.invalid/v1','model':'test',
        }):
            agent = assistant.build_agent(host)
        names = {tool["function"]["name"] for tool in agent.tools.to_openai_tools()}
        self.assertTrue({"remember_fact", "forget_fact", "web_search", "create_calendar_event"} <= names)
        self.assertFalse(any("arknights" in name or name.startswith("maa_") for name in names))
        agent.llm = FakeLLM([{"role": "assistant", "content": "回归正常。"}])
        result = agent.run("打个招呼", run_id="regression")
        self.assertIn("回归正常", str(result))
        self.assertEqual(assistant.react_loop_count(agent, "regression"), 1)

    def test_history_isolated_and_deduplicated(self):
        for role, text, sid in [("user", "重复", "one"), ("pet", "回答", "one"),
                                ("user", "重复", "one"), ("user", "别的线程", "two")]:
            memory.append_chat(role, text, sid)
        self.assertEqual([row["text"] for row in memory.session_for_model("one")], ["回答", "重复"])

    def test_fact_requires_current_quote_and_can_be_edited(self):
        args = {"text": "我喜欢 Python", "quote": "喜欢 Python"}
        self.assertNotIn("已记下", facts.remember_fact(args))
        with facts.bind_turn_user("我喜欢 Python，请记住"):
            self.assertIn("已记下", facts.remember_fact(args))
        facts.update_user_fact(facts.read_facts()[0]["id"], "我喜欢 TypeScript")
        self.assertEqual(facts.read_facts()[0]["writer"], "user")
        self.assertIn("已忘掉", facts.forget_fact({"text": "我喜欢 TypeScript"}))
        self.assertEqual(facts.read_facts(), [])

    def test_context_pack_keeps_originals_and_invalidates_on_delete(self):
        for role, body in [("user", "旧问题"), ("pet", "旧回答"), ("user", "新问题"), ("pet", "新回答")]:
            memory.append_chat(role, body, "s")
        rows = context_pack.prepare_injection("s", "系统", "", exclude_user=None,
                                             complete=lambda _: "旧问题已回答。", manual=True)
        self.assertIn("旧问题已回答", rows[0]["text"])
        self.assertEqual(len(memory.list_chat("s")), 4)
        context_pack.drop_pack_if_covered("s", "user", "旧问题")
        self.assertEqual(context_pack.context_view("s", "系统")["summary"], "")

    def test_unknown_window_is_not_rejected_by_a_fixed_character_token_guess(self):
        rows=context_pack.prepare_injection("s", "系统", "字" * 128000, exclude_user=None,
            complete=lambda _: self.fail("不能按猜测容量自动摘要"),manual=False,
            save=lambda *_:self.fail("不能自动改写摘要"))
        self.assertIsInstance(rows,list)

    def test_models_hide_keys_and_keep_one_entry(self):
        patch.object(model_catalog, "catalog_path", return_value=self.root / "models.json").start()
        patch.object(model_catalog, "parse_env_file", return_value={}).start()
        patch.object(model_catalog, "write_llm_env").start()
        first = model_catalog.upsert_entry({"base_url": "https://example.invalid/v1", "model": "one", "api_key": "test-key"})
        one = first["active"]
        second = model_catalog.upsert_entry({"base_url": "https://example.invalid/v1", "model": "two", "copy_key_from": one})
        two = second["items"][1]["id"]
        self.assertNotIn("test-key", json.dumps(model_catalog.public_catalog(second)))
        self.assertEqual(model_catalog.activate(two)["model"], "two")
        model_catalog.delete_entry(one)
        with self.assertRaises(RuntimeError):
            model_catalog.delete_entry(two)

    def test_calendar_cross_day_and_next_event(self):
        now = datetime(2026, 10, 8, 22, 0, tzinfo=memory.TZ)
        item = {"summary": "跨日", "event_id": "test", "start": "2026-10-08T23:00:00+08:00",
                "end": "2026-10-09T01:00:00+08:00"}
        rows = board_data.parse_agenda([item])
        self.assertIn("10月9日", rows[0]["end"])
        self.assertEqual(board_data._next_event(rows, now)["summary"], "跨日")


    def test_search_results_require_links(self):
        result = web_search.format_search({"results": [{"title": "来源", "link": "https://example.invalid", "snippet": "样本"}]})
        self.assertIn("https://example.invalid", result)
        with self.assertRaises(RuntimeError):
            web_search.format_search({"results": [{"title": "没有链接"}]})
        with self.assertRaises(RuntimeError):
            web_search.search_endpoint("https://example.invalid")

    def test_mcp_only_accepts_stdio_config(self):
        from desk_companion.mcp_client import parse_mcp_config
        self.assertIn("test", parse_mcp_config({"mcpServers": {"test": {"command": "python", "args": []}}}, "test"))
        with self.assertRaises(RuntimeError):
            parse_mcp_config({"mcpServers": {"test": {"url": "https://example.invalid"}}}, "test")

    def test_knowledge_uses_selected_sources_and_numbered_cites(self):
        hit = {"doc": "样本文档", "title": "章节", "text": "只有样本知识。", "score": 1.0, "doc_id": "doc-1"}
        with patch.object(knowledge, "_retrieve", return_value={"question": "问题", "candidates": [hit], "kept": [hit]}) as retrieve:
            result = knowledge.ask_sources("问题", ["doc-1"], lambda system, user: "只有样本知识。[1]")
        retrieve.assert_called_once_with("问题", {"doc-1"})
        self.assertEqual(result["cites"][0]["doc_id"], "doc-1")
        with self.assertRaises(RuntimeError):
            knowledge.ask_sources("问题", [], lambda *_: self.fail("未选来源不能调用模型"))

    def test_knowledge_chunk_strategies_keep_content(self):
        for strategy in ("fixed", "structure", "overlap", "multi", "parent"):
            settings = {**knowledge.default_settings(), "chunk_strategy": strategy, "chunk_size": 32, "overlap": 8}
            with self.subTest(strategy=strategy):
                chunks = knowledge._split("# 标题\n\n第一段。这里有内容。\n\n第二段。继续说明。" * 5, settings)
                self.assertTrue(chunks)
                self.assertTrue(all(chunk["text"] for chunk in chunks))

    def test_notebook_exports_and_deletes_only_temp_files(self):
        with patch.object(knowledge, "public_view", return_value={"docs": []}):
            sid = notebook.new_notebook()["session_id"]
            cites = [{"n": 1, "doc": "来源", "title": "章节", "text": "原文", "doc_id": "doc-1"}]
            notebook.begin_notebook_turn(sid, "问题")
            notebook.finish_notebook_turn(sid, "回答[1]", cites)
            nid = notebook.save_note(sid, "问题", "回答[1]", cites)["note_id"]
            path = Path(notebook.write_note_markdown(sid, nid)["path"])
            self.assertTrue(path.is_relative_to(self.root))
            self.assertIn("原文", path.read_text(encoding="utf-8"))
            self.assertEqual(notebook.selected_answers(sid, [nid])[0]["answer"], "回答[1]")
            notebook.delete_note(sid, nid)
            self.assertFalse(path.exists())

    def test_automation_roundtrip_without_running_actions(self):
        patch.object(automation, "jobs_path", return_value=self.root / "automation.json").start()
        automation.upsert_job({"name": "样本", "action": "retro_gen", "enabled": False,
                               "cadence": "daily", "weekdays": [], "hour": 12, "minute": 0})
        self.assertFalse(automation.list_snapshot()["items"][0]["enabled"])

    def test_skills_still_available(self):
        skills = skill_catalog.list_skills()
        self.assertEqual({item["id"] for item in skills}, {"weekly-retro", "feishu-doc-writing", "github-repo-summary"})
        self.assertTrue(all(item["body"].strip() for item in skills))

    def test_legacy_entry_resources_remain(self):
        root = Path(desk_companion.__file__).parents[1]
        for relative in ("desk_companion/ui/board.html", "desk_companion/ui/tokens.css",
                         "desk_companion/ui/fonts/ZCOOLKuaiLe-Regular.ttf",
                         "desk_companion/ui/vendor/marked.min.js", "desk_companion/ui/vendor/purify.min.js",
                         "pet-ui/public/vendor/marked.min.js",
                         "pet-ui/public/vendor/purify.min.js"):
            with self.subTest(resource=relative):
                self.assertTrue((root / relative).is_file())


if __name__ == "__main__":
    unittest.main()
