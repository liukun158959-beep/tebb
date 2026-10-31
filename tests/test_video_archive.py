"""自动归档使用真实本地字幕和回执；飞书写入由 mock 隔离。"""
import json
import os
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from desk_companion import video, wiki_connection
from desk_companion.video_archive import VideoArchive
from tests.test_video import SOURCE, URL

WIKI = "https://example.feishu.cn/wiki/root"
PARENT = "https://example.feishu.cn/wiki/videos"
CFG = {"auto_video_save": True, "wiki_url": WIKI, "video_parent_url": PARENT,
       "video_parent_token": "videos", "profile": "my-app"}


class ArchiveTests(unittest.TestCase):
    def test_budget_denial_precedes_export_pending_and_preserves_note(self):
        from desk_companion.task_budgets import Ledger,installed
        self.archive.record('read_video',self.source)
        ledger=Ledger();root=ledger.create('archive-negative','probe');ledger.bind(root,'archive-task',root_member=True)
        with installed('archive-task'),patch('desk_companion.feishu_auth.create_markdown_doc')as create:
            self.archive.finish('已生成笔记')
        create.assert_not_called()
        self.assertEqual(video.get_source('session',self.source['source_id']).get('exports',{}),{})
        self.assertIn('未开始自动保存',str(self.events))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": self.temp.name})
        env.start(); self.addCleanup(env.stop)
        extractor = patch.object(video, "extract", return_value=json.loads(json.dumps(SOURCE)))
        extractor.start(); self.addCleanup(extractor.stop)
        self.source = video.read_video("session", URL)
        self.events = []
        self.request = {"text": "请读取视频", "session_id": "session", "video_archive_config": CFG}
        self.archive = VideoArchive(self.request, lambda *event: self.events.append(event))

    def test_complete_read_archives_under_parent_and_manual_save_reuses_receipt(self):
        self.archive.record("read_video", json.dumps(self.source))
        with patch("desk_companion.feishu_auth.create_markdown_doc", return_value={"url": PARENT + "/note"}) as create:
            self.archive.finish("概念定义、关系和拓展")
            self.archive.finish("概念定义、关系和拓展")
            video.export_summary("session", self.source["source_id"], "概念定义、关系和拓展")
            self.assertEqual(create.call_count, 1)
            self.assertEqual(create.call_args.kwargs, {"parent_token": "videos", "profile": "my-app"})
            self.assertNotIn(URL, create.call_args.args[1])
        self.assertTrue(any(PARENT + "/note" in str(event) for event in self.events))

    def test_unknown_save_never_retries_and_answer_remains_available(self):
        self.archive.record("read_video", self.source)
        with patch("desk_companion.feishu_auth.create_markdown_doc", side_effect=TimeoutError()) as create:
            self.archive.finish("已生成的笔记")
            self.archive.finish("已生成的笔记")
            self.assertEqual(create.call_count, 1)
        self.assertIn("答案已保留", str(self.events))
        self.assertEqual(next(iter(video.get_source("session", self.source["source_id"])["exports"].values()))["state"], "pending")

    def test_incomplete_pagination_does_not_save_then_full_read_saves(self):
        page = {**self.source["transcript"], "items": self.source["transcript"]["items"][:1], "next_offset": 1, "complete": False}
        self.archive.record("read_video", {"transcript": page})
        with patch("desk_companion.feishu_auth.create_markdown_doc", return_value={"url": "saved"}) as create:
            self.archive.finish("不完整笔记")
            create.assert_not_called()
            self.archive.record("read_video_transcript", video.transcript_page(video.get_source("session", self.source["source_id"]), 1))
            self.archive.finish("完整笔记")
            create.assert_called_once()

    def test_disable_opt_out_followups_and_manual_save_do_not_archive(self):
        for change in ({"video_archive_config": {}}, {"text": "读视频但不要保存"}, {"text": "don't save this video"}, {"auto_video_archive": False}):
            arch = VideoArchive({**self.request, **change}, lambda *e: None)
            arch.record("read_video", self.source)
            with patch("desk_companion.feishu_auth.create_markdown_doc") as create:
                arch.finish("笔记")
                create.assert_not_called()
        self.archive.record("read_video", self.source)
        self.archive.manual_save = True
        with patch("desk_companion.feishu_auth.create_markdown_doc") as create:
            self.archive.finish("笔记")
            create.assert_not_called()

    def test_missing_and_truncated_subtitles_do_not_archive(self):
        for change in ({"subtitle_status": "missing"}, {"truncated": True}):
            value = video.get_source("session", self.source["source_id"])
            video.atomic_write(video.source_path("session", value["source_id"]), {**value, **change})
            self.archive.record("read_video", self.source)
            with patch("desk_companion.feishu_auth.create_markdown_doc") as create:
                self.archive.finish("笔记")
                create.assert_not_called()

    def test_video_workspace_generated_request_is_not_an_opt_out(self):
        from types import SimpleNamespace
        from desk_companion.video_controller import start
        requests = []
        host = SimpleNamespace(tasks=SimpleNamespace(submit=lambda text, *a, **kw: requests.append(text) or "task"))
        start(host, URL)
        self.request["text"] = requests[0]
        self.archive.record("read_video", self.source)
        with patch("desk_companion.feishu_auth.create_markdown_doc", return_value={"url": "saved"}) as create:
            self.archive.finish("笔记")
            create.assert_called_once()


class WikiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": self.temp.name})
        env.start(); self.addCleanup(env.stop)

    def test_connection_verifies_same_space_before_persisting(self):
        payload = {**CFG, "wiki_url": WIKI + "?fromScene=spaceOverview"}
        with patch.object(wiki_connection, "cli", side_effect=[{"space_id": "1", "title": "AI日报"},
                     {"space_id": "2", "node_token": "videos", "obj_type": "docx"}]):
            with self.assertRaises(ValueError): wiki_connection.save_settings(payload)
        self.assertFalse(wiki_connection.settings_path().exists())
        with patch.object(wiki_connection, "cli", side_effect=[{"space_id": "1", "title": "AI日报"},
                     {"space_id": "1", "node_token": "videos", "obj_type": "docx", "title": "视频笔记"}]):
            result = wiki_connection.save_settings(payload)
        self.assertEqual(result["settings"]["wiki_url"], WIKI)
        self.assertEqual(wiki_connection.load_settings()["video_parent_token"], "videos")
        self.assertTrue(result["settings"]["auto_video_save"])

    def test_blank_disabled_connection_disconnects_and_urls_reject_non_wiki_hosts(self):
        wiki_connection.save_settings(dict(wiki_connection.DEFAULTS))
        self.assertEqual(wiki_connection.catalog(), [])
        for url in ("https://evil.com/wiki/x", "https://example.feishu.cn.evil.com/wiki/x", "http://example.feishu.cn/wiki/x", "https://user:pwd@example.feishu.cn/wiki/x", "https://example.feishu.cn/docx/x"):
            with self.assertRaises(ValueError): wiki_connection.wiki_url(url)

    def test_recursive_catalog_exhausts_pages_and_skips_shortcut_children(self):
        root = {"space_id": "1"}
        def node(token, child=False, **extra):
            return {"node_token": token, "obj_token": "obj"+token, "title": token, "obj_type": "docx", "has_child": child, **extra}
        pages = [root, {"nodes": [node("daily", True), node("video", True, node_type="shortcut")], "has_more": True, "page_token": "p2"},
                 {"nodes": [node("overview")], "has_more": False}, {"nodes": [node("report")], "has_more": False}]
        with patch.object(wiki_connection, "cli", side_effect=pages) as cli:
            rows = wiki_connection.catalog(CFG)
        self.assertEqual([r["title"] for r in rows], ["daily", "video", "overview", "report"])
        self.assertEqual(rows[-1]["url"], "https://example.feishu.cn/wiki/report")
        self.assertIn("p2", cli.call_args_list[2].args[1])
        self.assertNotIn("video", cli.call_args_list[3].args[1])

    def test_catalog_bad_cursor_is_failure_not_silent_truncation(self):
        with patch.object(wiki_connection, "cli", side_effect=[{"space_id": "1"}, {"nodes": [], "has_more": True}]):
            with self.assertRaises(RuntimeError): wiki_connection.catalog(CFG)

    def test_markdown_creator_uses_selected_profile_and_parent(self):
        from desk_companion.feishu_auth import create_markdown_doc
        with patch("desk_companion.feishu_auth._run_lark", return_value=json.dumps({"ok": True, "data": {"document": {"url": "saved", "document_id": "doc"}}})) as cli:
            create_markdown_doc("笔记", "原样笔记", parent_token="videos", profile="my-app")
        args = cli.call_args.args[0]
        self.assertEqual(args[:2], ["--profile", "my-app"])
        self.assertEqual(args[-2:], ["--parent-token", "videos"])
        self.assertEqual(cli.call_args.kwargs["stdin"], "原样笔记")


class WorkerArchiveTests(unittest.TestCase):
    def test_real_worker_read_model_answer_archive_and_persistent_receipt(self):
        from desk_companion.tasks import TaskManager
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": temp}):
            manager = TaskManager(command=[sys.executable, "-u", "-c", "exec(open('tests/worker_fixture.py', encoding='utf-8').read())"])
            try:
                task = manager.wait(manager.submit("视频归档测试", "worker-session", video_archive_config=CFG))
                self.assertEqual(task["state"], "succeeded", task)
                self.assertEqual(task["answer"], "本地流式回归正常。")
                document = json.loads((Path(temp) / "archive_created.json").read_text("utf-8"))
                self.assertEqual(document["parent_token"], "videos")
                self.assertEqual(document["profile"], "my-app")
                self.assertIn(task["answer"], document["body"])
                source = video.sources("worker-session")[0]
                receipt = next(iter(video.get_source("worker-session", source["source_id"])["exports"].values()))
                self.assertEqual(receipt["url"], "https://example.feishu.cn/wiki/note")
                self.assertTrue(any(e["kind"] == "tool_end" and e["data"].get("tool") == "archive_video_notes" for e in task["events"]))
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
