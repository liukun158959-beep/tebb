"""资讯流程的来源约束、补跑、隔离、恢复与去重；不操作真实账号。"""
import json
import os
import tempfile
import threading
import sys
import asyncio
import unittest
from email.message import Message
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desk_companion import automation, news, news_controller, news_publish
from desk_companion.automation_service import AutomationService
from desk_companion.memory import TZ


def fixture():
    cfg = {**news.DEFAULTS, "profile": "test", "wiki_url": "https://example.feishu.cn/wiki/testwiki",
           "base_url": "https://example.feishu.cn/base/testbase", "base_token": "testbase", "table_id": "tbltest", "chat_id": "oc_test"}
    day = "2026-10-08"
    companies = ["阿里", "腾讯", "Google", "Microsoft", "NVIDIA"]
    items = [{"id": news.digest(str(i)), "title": f"Official Project {i}", "title_zh": f"官方技术 {i}", "url": f"https://github.com/example/project{i}",
              "summary": "已核实功能", "detail": "此处详细解释官方发布内容、技术机制与适用场景。"*8, "key_points": ["可核实机制", "适用场景"],
              "company": companies[i], "region": news.COMPANIES[companies[i]], "value": "建议先测试", "caution": "尚未在项目验证",
              "category": "Agent 工程", "published": day, "date_verified": True} for i in range(5)]
    return {"id": news.run_id_for(day, cfg), "day": day, "settings": cfg, "receipts": {}, "warnings": [],
            "status": "draft", "report": {"lead": "五项工具能力值得试用", "cover_title": "大厂开源五项进展", "items": items, "candidate_count": 12}}


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": str(self.root)}).start()

    def test_unknown_links_duplicate_sources_and_private_urls_are_rejected(self):
        candidates = [{"id": "known", "title": "官方说明", "url": "https://github.com/example/repo", "published": "", "date_verified": False}]
        value = {"lead": "重点", "items": [{"source_id": "invented", "category": "工程", "summary": "编造", "value": "测试"}] * 5}
        with self.assertRaises(ValueError):
            news.validate_report(value, candidates, 5)
        value["items"] = [{"source_id": "known", "category": "工程", "summary": "功能", "value": "测试"}] * 5
        with self.assertRaises(ValueError):
            news.validate_report(value, candidates, 5)
        for url in ("https://localhost/test", "http://github.com/test", "https://github.com.evil.invalid/test", "https://user:pass@github.com/test", "https://github.com:8080/test"):
            self.assertFalse(news.primary_url(url))
        self.assertEqual(news.primary_url("https://github.com/example/repo#readme"), "https://github.com/example/repo")

    def test_preview_never_calls_publisher_and_retains_verified_sources(self):
        run = fixture()
        events = []
        request = {"news_settings": run["settings"], "news_day": run["day"], "news_run_id": run["id"], "news_publish": False}
        with patch.object(news, "collect", return_value=([{"id": "source"}] * 5, ["某来源不可读"])), \
             patch.object(news, "summarize", return_value=run["report"]), patch.object(news_publish, "publish") as publish:
            news.execute(request, lambda kind, data: events.append((kind, data)))
        publish.assert_not_called()
        saved = news.load_run(run["id"])
        self.assertEqual(saved["status"], "draft")
        self.assertEqual(len(saved["candidates"]), 5)
        self.assertEqual(events[-1][0], "result")

    def test_failed_collection_keeps_diagnostics_without_publishing(self):
        run = fixture()
        request = {"news_settings": run["settings"], "news_day": run["day"], "news_run_id": run["id"]}
        with patch.object(news, "collect", return_value=([{"id": "one"}], ["官方源超时"])), patch.object(news, "summarize") as summarize:
            news.execute(request, lambda *_: None)
        summarize.assert_not_called()
        saved = news.load_run(run["id"])
        self.assertEqual(saved["warnings"], ["官方源超时"])
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["candidates"], [{"id": "one"}])

    def test_five_translated_items_have_separate_briefs_and_details(self):
        r = fixture()
        value = {"lead": r["report"]["lead"], "cover_title": r["report"]["cover_title"],
                 "items": [{**i, "source_id": i["id"]} for i in r["report"]["items"]]}
        result = news.validate_report(value, r["report"]["items"], 5)
        self.assertEqual(len(result["items"]), 5)
        self.assertEqual(result["cover_title"], value["cover_title"])
        self.assertFalse(result["cover_title_fallback"])
        self.assertIn("（官方技术 0）", news.display_title(result["items"][0]))
        self.assertGreater(len(result["items"][0]["detail"]), len(result["items"][0]["summary"]))
        for field, bad in (("title_zh", "English only"), ("detail", "太短")):
            old = value["items"][0][field]
            value["items"][0][field] = bad
            with self.assertRaises(ValueError):
                news.validate_report(value, r["report"]["items"], 5)
            value["items"][0][field] = old
        value["items"].pop()
        with self.assertRaises(ValueError):
            news.validate_report(value, r["report"]["items"], 5)

    def test_company_identity_is_official_and_repository_pages_are_deduplicated(self):
        self.assertEqual(news.company_for("https://github.com/QwenLM/Qwen3"), "阿里")
        self.assertEqual(news.company_for("https://github.com/google-deepmind/project"), "Google")
        self.assertFalse(news.company_for("https://github.com/random-person/qwen-mirror"))
        self.assertFalse(news.company_for("https://arxiv.org/html/test", "University Abstract We compare Microsoft models"))
        self.assertEqual(news.project_key("https://github.com/QwenLM/Qwen3/releases"), news.project_key("https://github.com/QwenLM/Qwen3/blob/main/README.md"))
        self.assertEqual(news.repository_source("https://github.com/QwenLM/Qwen3/discussions/123"),
                         ("https://github.com/QwenLM/Qwen3", "QwenLM/Qwen3"))

    def test_github_rate_limit_falls_back_to_same_official_readme_without_guessing_date(self):
        from urllib.error import HTTPError
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"# Official README\nVerified implementation"
        opener = Mock()
        opener.open.side_effect = [HTTPError("api", 403, "rate limit", {}, None), response,
                                    HTTPError("api", 403, "rate limit", {}, None)]
        with patch.object(news.urllib.request, "build_opener", return_value=opener):
            result = news.fetch_source("https://github.com/NVIDIA/OpenShell")
        self.assertIn("Verified implementation", result["text"])
        self.assertEqual(result["published"], "")
        self.assertEqual(opener.open.call_args_list[1].args[0].full_url,
                         "https://raw.githubusercontent.com/NVIDIA/OpenShell/HEAD/README.md")

    def test_invalid_summary_is_repaired_once_and_remains_validated(self):
        r = fixture()
        correct = {"lead": r["report"]["lead"], "cover_title": r["report"]["cover_title"],
                   "items": [{**i, "source_id": i["id"]} for i in r["report"]["items"]]}
        broken = {**correct}
        broken.pop("lead")
        responses = [{"message": {"content": json.dumps(value)}} for value in (broken, correct)]
        client = Mock()
        client.chat.side_effect = responses
        events = []
        with patch("atlas.LLM", return_value=client), patch("desk_companion.model_catalog.require_active",
                return_value={"api_key": "test", "base_url": "https://example.invalid/v1", "model": "test"}):
            result = news.summarize(r["day"], r["settings"], r["report"]["items"], lambda k, d: events.append((k, d)))
        self.assertEqual(len(result["items"]), 5)
        self.assertEqual(client.chat.call_count, 2)
        self.assertTrue(any(k == "status" and "修正一次" in d for k, d in events))

    def test_cover_layout_errors_use_neutral_title_without_another_model_call(self):
        r = fixture()
        value = {"lead": r["report"]["lead"],
                 "items": [{**i, "source_id": i["id"]} for i in r["report"]["items"]]}
        client = Mock()
        with patch("atlas.LLM", return_value=client), patch("desk_companion.model_catalog.require_active",
                return_value={"api_key": "test", "base_url": "https://example.invalid/v1", "model": "test"}):
            for bad in (None, "", "   ", 42, "很长的海报标题" * 5):
                with self.subTest(title=bad):
                    client.reset_mock()
                    client.chat.return_value = {"message": {"content": json.dumps({**value, "cover_title": bad})}}
                    result = news.summarize(r["day"], r["settings"], r["report"]["items"], lambda *_: None)
                    self.assertEqual(result["cover_title"], "大厂 AI/Agent 技术动向")
                    self.assertTrue(result["cover_title_fallback"])
                    self.assertEqual(client.chat.call_count, 1)
                    self.assertEqual(len(result["items"]), 5)

    def test_content_errors_remain_blocking_but_retryable_and_never_publish(self):
        r = fixture()
        request = {"news_settings": r["settings"], "news_day": r["day"],
                   "news_run_id": r["id"], "news_publish": True}
        errors = [(news.ReportValidationError("摘要缺少英文标题的中文翻译，未发布。"), True),
                  (news.ReportValidationError("技术摘要缺少有效字段，未发布。"), True),
                  (news.ReportValidationError("摘要引用了未知或重复来源，未发布。"), True),
                  (json.JSONDecodeError("bad", "", 0), True),
                  (news_publish.platform_error({"ok": False, "error": {"type": "network", "subtype": "transport"}}), True),
                  (news_publish.platform_error({"ok": False, "error": {"type": "auth", "subtype": "token_missing"}}), False),
                  (news_publish.platform_error({"ok": False, "error": {"type": "authorization", "subtype": "missing_scope"}}), False),
                  (RuntimeError("飞书缺少权限。"), False),
                  (RuntimeError("上次发送尚未确认，请核实回执。"), False)]
        for error, retryable in errors:
            with self.subTest(error=str(error)), \
                 patch.object(news, "collect", return_value=(r["report"]["items"], [])), \
                 patch.object(news, "summarize", side_effect=error), \
                 patch.object(news_publish, "publish") as publish:
                news.execute(request, lambda *_: None)
                saved = news.load_run(r["id"])
                self.assertEqual(saved["status"], "failed")
                self.assertEqual(saved["retryable"], retryable)
                if isinstance(error, news.ReportValidationError):
                    self.assertEqual(saved["error"], str(error))
                self.assertFalse(saved.get("report"))
                self.assertEqual(saved["receipts"], {})
                publish.assert_not_called()

    def test_default_topics_migrate_without_changing_publication_targets(self):
        cfg = {**fixture()["settings"], "topics": news.LEGACY_TOPICS, "highlights": 3}
        news.atomic_json(news.settings_path(), cfg)
        migrated = news.load_settings()
        self.assertEqual(migrated["topics"], news.DEFAULTS["topics"])
        self.assertEqual(migrated["highlights"], 5)
        self.assertEqual(migrated["chat_id"], cfg["chat_id"])

    def test_search_index_date_is_not_treated_as_verified_publication(self):
        cfg = {**news.DEFAULTS, "topics": ["Agent"]}
        payload = {"results": [{"title": "Official", "link": "https://github.com/microsoft/repo", "published_date": "2026-10-08"}]}
        with patch("desk_companion.envconf.require_llm_env", return_value={"ATLAS_BASE_URL": "https://example.invalid/v1", "ATLAS_API_KEY": "test"}), \
             patch("desk_companion.web_search._post", return_value=payload), \
             patch("desk_companion.web_search.format_search"), \
             patch.object(news, "fetch_source", return_value={"text": "official article", "published": ""}):
            rows, _ = news.collect("2026-10-08", cfg, lambda *_: None)
        self.assertFalse(rows[0]["date_verified"])
        self.assertEqual(rows[0]["published"], "")

    def test_publication_date_comes_from_official_metadata_or_arxiv_version(self):
        htmls = [('<meta name="ms.date" content="2026-10-02T00:00:00Z"><p>Official documentation</p>',
                  "https://learn.microsoft.com/en-us/test", "2026-10-02T00:00:00Z"),
                 ('<p>arXiv:2610.04116v1 [cs.AI] 02 Oct 2026</p><p>Other example date: 01 Jan 2000</p>',
                  "https://arxiv.org/html/2610.04116v1", "2026-10-02")]
        for html, url, expected in htmls:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.headers = Message()
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
            response.read.return_value = html.encode()
            opener = Mock()
            opener.open.return_value = response
            with patch.object(news.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(news.fetch_source(url)["published"], expected)

    def test_latest_missed_slot_runs_once_without_replaying_days(self):
        base = dict(name="日报", action="ai_news", enabled=True, cadence="daily", weekdays=[], hour=9, minute=0)
        automation.upsert_job(base)
        store = automation.load_store()
        job = store["jobs"][0]
        now = datetime(2026, 10, 8, 14, 0, tzinfo=TZ)
        job["created_at"] = (now - timedelta(days=10)).isoformat()
        store["last_alive_at"] = (now - timedelta(days=5)).isoformat()
        self.assertFalse(automation._mark_missed(store, now))
        self.assertEqual(automation._due_jobs(store, now), [job])
        latest = automation._latest_slot(now, job)
        self.assertEqual(latest, now.replace(hour=9))
        job["last_run_slot"] = automation._slot_key(latest, job)
        self.assertEqual(automation._due_jobs(store, now), [])
        job["last_result"] = "running"
        self.assertTrue(automation._abandon_queued(store))
        self.assertEqual(automation._due_jobs(store, now), [job])

    def test_disabled_new_jobs_and_legacy_actions_do_not_catch_up(self):
        now = datetime(2026, 10, 8, 14, tzinfo=TZ)
        for action in ("ai_news", "retro_gen"):
            automation.upsert_job(dict(name=action, action=action, enabled=False, cadence="daily", weekdays=[], hour=9, minute=0))
        store = automation.load_store()
        self.assertFalse(automation._due_jobs(store, now))
        store["jobs"][1].update(enabled=True, created_at=(now - timedelta(days=3)).isoformat())
        self.assertFalse(automation._due_jobs(store, now))

    def test_document_response_loss_reconciles_by_title_without_new_document(self):
        run = fixture()
        target = {"space_id": "123", "node_token": "parent"}
        workspace = self.root / "draft-unique"
        workspace.mkdir()
        draft = workspace / "draft.xml"
        created = []
        def save(**fields):
            run.update(fields)
        def cli(_cfg, args, **kwargs):
            if args[:2] == ["wiki", "+node-list"]:
                return {"nodes": created, "has_more": False}
            if "init-draft" in args:
                return {"workspace": "draft-unique", "draft_path": "draft-unique/draft.xml"}
            if "parse" in args:
                return {"assessment": {"status": "passed"}}
            if args[:2] == ["docs", "+create"]:
                created.append({"title": news_publish.doc_title(run), "obj_type": "docx", "obj_token": "doc-created", "node_token": "wiki-created"})
                raise news_publish.PublishError("连接中断，回执丢失")
            self.fail(args)
        with patch.object(news_publish, "cli", side_effect=cli):
            with self.assertRaises(news_publish.PublishError):
                news_publish.create_document(run, target, save, lambda *_: None)
            self.assertTrue(run["receipts"]["doc_pending"])
            self.assertFalse(workspace.exists())
            news_publish.create_document(run, target, save, lambda *_: None)
        self.assertEqual(run["doc_id"], "doc-created")
        self.assertEqual(len(created), 1)

    def test_record_response_loss_reconciles_exact_key_and_never_recreates(self):
        run = fixture()
        run["doc_url"] = "https://example.feishu.cn/docx/test"
        records = []
        creates = []
        def cli(_cfg, args, **kwargs):
            if args[1] == "+record-search":
                self.assertIn("--filter-json", args)
                return {"records": records}
            creates.append(args)
            records.append({"id": "rec_created"})
            raise news_publish.PublishError("响应丢失")
        with patch.object(news_publish, "cli", side_effect=cli):
            with self.assertRaises(news_publish.PublishError):
                news_publish.ensure_record(run, run["report"]["items"][0], "base", lambda **kw: run.update(kw), lambda *_: None)
            news_publish.ensure_record(run, run["report"]["items"][0], "base", lambda **kw: run.update(kw), lambda *_: None)
        self.assertEqual(len(creates), 1)
        self.assertEqual(list(run["receipts"]["records"].values()), ["rec_created"])

    def test_unknown_record_result_without_match_does_not_write_again(self):
        run = fixture()
        key = news.digest([run["day"], run["report"]["items"][0]["url"]])
        run["receipts"]["records_pending"] = [key]
        with patch.object(news_publish, "cli", return_value={"records": []}) as cli:
            with self.assertRaises(news_publish.PublishError):
                news_publish.ensure_record(run, run["report"]["items"][0], "base", lambda **kw: None, lambda *_: None)
        self.assertEqual(cli.call_count, 1)

    def test_typed_base_ack_without_id_is_reconciled_and_never_duplicated(self):
        run = fixture()
        run["doc_url"] = "https://example.feishu.cn/docx/doc"
        with patch.object(news_publish, "cli", side_effect=[{"record_id_list": [], "has_more": False},
                {"created": True, "record": {"create": {"状态": "已归档"}}},
                {"record_id_list": ["rec_typed"], "has_more": False}]) as cli:
            news_publish.ensure_record(run, run["report"]["items"][0], "base", lambda **kw: run.update(kw), lambda *_: None)
            news_publish.ensure_record(run, run["report"]["items"][0], "base", lambda **kw: run.update(kw), lambda *_: None)
        self.assertEqual(cli.call_count, 3)
        self.assertEqual(list(run["receipts"]["records"].values()), ["rec_typed"])
        self.assertEqual(run["receipts"]["records_pending"], [])

    def test_group_send_expired_idempotency_window_requires_reconciliation(self):
        run = fixture()
        run.update(doc_id="doc", doc_url="https://example.feishu.cn/docx/doc")
        run["receipts"] = {"doc_verified": True, "image_key": "img_v3_test", "message_pending_at": 1,
                           "records": {news.digest([run["day"], i["url"]]): "rec" for i in run["report"]["items"]}}
        with patch.object(news_publish, "validate_targets", return_value={"base_token": "base"}), \
             patch.object(news_publish, "render_cover", return_value=self.root / "image.png"), patch.object(news_publish, "cli") as cli:
            with self.assertRaises(news_publish.PublishError):
                news_publish.publish(run, lambda **kw: run.update(kw), lambda *_: None)
        cli.assert_not_called()

    def test_card_has_image_grouped_evidence_and_correct_target_links(self):
        run = fixture()
        run["doc_url"] = "https://example.feishu.cn/docx/doc"
        card = news_publish.news_card(run, "img_v3_test")
        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(len(card["body"]["elements"]), 3)
        raw = json.dumps(card, ensure_ascii=False)
        self.assertIn("img_v3_test", raw)
        self.assertIn(run["doc_url"], raw)
        for item in run["report"]["items"]:
            self.assertIn(item["url"], news_publish.document_xml(run, "sample.png"))
            self.assertIn(item["title_zh"], raw)
            self.assertNotIn(item["detail"], raw)
        image = news_publish.render_cover(run)
        from PIL import Image
        with Image.open(image) as img:
            self.assertEqual(img.size, (1440, 810))

        def check(elements):
            for element in elements:
                if element["tag"] == "markdown":
                    self.assertNotIn("font_color", element)
                check(element.get("elements", []))
                for column in element.get("columns", []):
                    check(column.get("elements", []))
        check(card["body"]["elements"])

    def test_cli_platform_rejection_is_reported_as_confirmed_instead_of_network(self):
        raw = "CLI failed.\n" + json.dumps({"ok": False, "error": {"type": "api", "subtype": "unknown", "code": 230099,
            "message": "unknown property font_color", "secret": "must-not-be-shown"}})
        with patch.object(news_publish, "_run_lark", side_effect=RuntimeError(raw)):
            with self.assertRaises(news_publish.PublishError) as caught:
                news_publish.cli(fixture()["settings"], ["im", "+messages-send"])
        self.assertTrue(caught.exception.confirmed)
        self.assertFalse(caught.exception.retryable)
        self.assertIn("卡片格式", str(caught.exception))
        self.assertNotIn("must-not-be-shown", str(caught.exception))

    def test_controller_rejects_changed_targets_before_submit(self):
        run = fixture()
        news.atomic_json(news.run_path(run["id"]), run)
        changed = {**run["settings"], "chat_id": "oc_other"}
        with patch.object(news, "load_settings", return_value=changed):
            with self.assertRaises(ValueError):
                news_controller.start(Mock(), publish=True, run_id=run["id"])

    def test_manual_preview_submits_fixed_workflow_and_fifteen_minute_limit(self):
        run = fixture()
        manager = Mock()
        manager.cv = threading.Condition(threading.RLock())
        manager.list.return_value = {"items": []}
        manager.submit.return_value = "task"
        with patch.object(news, "load_settings", return_value=run["settings"]):
            started = news_controller.start(manager, publish=False)
        self.assertEqual(started["task_id"], "task")
        req = manager.submit.call_args.kwargs
        self.assertEqual(req["workflow"], "ai_news")
        self.assertFalse(req["news_publish"])
        self.assertEqual(req["task_limits"]["task_timeout"], 900)

    def test_background_news_poll_does_not_steal_chat_progress_focus(self):
        from desk_companion.bridge import Bridge
        manager = Mock()
        manager.get.return_value = {"session": "news"}
        host = SimpleNamespace(tasks=manager, _focused_task="chat")
        with patch("desk_companion.memory.list_chat", return_value=[]):
            Bridge(host).get_agent_task("news-task", focus=False)
            self.assertEqual(host._focused_task, "chat")
            Bridge(host).get_agent_task("news-task")
            self.assertEqual(host._focused_task, "news-task")

    def test_target_validation_rejects_wrong_fields_before_any_write(self):
        run = fixture()
        with patch.object(news_publish, "cli", side_effect=[{"space_id": "1", "node_token": "wiki"}, {"base_token": "base"},
                    {"fields": [{"name": name, "type": "number"} for name in news.FIELDS]}]) as cli:
            with self.assertRaises(ValueError):
                news_publish.validate_targets(run["settings"])
        self.assertEqual(cli.call_count, 3)

    def test_real_worker_creates_preview_without_cli_or_shared_chat_history(self):
        from desk_companion.tasks import TaskManager
        run = fixture()
        manager = TaskManager(command=[sys.executable, "-u", "-c", "exec(open('tests/news_worker_fixture.py',encoding='utf-8').read())"])
        self.addCleanup(manager.shutdown)
        task_id = manager.submit("预览", "news-isolated", "automation", workflow="ai_news",
            news_settings=run["settings"], news_day=run["day"], news_run_id=run["id"], news_publish=False,
            task_limits={"call_timeout": 90, "task_timeout": 900})
        result = manager.wait(task_id)
        self.assertEqual(result["state"], "succeeded")
        self.assertIn("离线流程正常", result["answer"])
        self.assertEqual(news.load_run(run["id"])["status"], "draft")
        self.assertFalse((self.root / "memory/chat.jsonl").exists())

    def test_independent_news_can_start_while_legacy_host_is_busy(self):
        scheduler = AutomationService(busy=lambda: True)
        scheduler._queued = {"retro_gen": "retro", "ai_news": "news"}
        with patch.object(scheduler, "_execute") as execute, patch.object(scheduler, "_notify"):
            scheduler._loop()
        execute.assert_called_once_with("news")
        self.assertEqual(scheduler._queued, {"retro_gen": "retro"})

    def test_scheduled_retry_once_shares_remaining_fifteen_minute_budget(self):
        run = fixture()
        manager = Mock()
        manager.get.side_effect = [dict(state="failed", started=__import__('time').time(), error="临时错误"),
                                   dict(state="succeeded", started=__import__('time').time(), error="")]
        scheduler = AutomationService(tasks=lambda: manager)
        with patch.object(news, "load_run", return_value={"retryable": True}), \
             patch.object(news_controller, "start", return_value={"task_id": "retry"}) as start, \
             patch.object(scheduler, "_news_result") as result:
            scheduler._monitor_news("job", {"task_id": "first", "run_id": run["id"]})
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["attempt"], 1)
        self.assertLess(start.call_args.kwargs["seconds"], 900)
        self.assertEqual(result.call_args.args[:2], ("job", "ok"))


class SchedulerApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_cover_requires_auth_and_rejects_traversal(self):
        from desk_companion.local_api import server
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": folder}), patch.object(server, "TOKEN", "test"):
            connection = SimpleNamespace(respond=lambda code, body: SimpleNamespace(status_code=code, body=body))
            rid = "a" * 32
            cover = news.run_path(rid).with_suffix(".png")
            cover.parent.mkdir(parents=True)
            cover.write_bytes(b'png test')
            self.assertEqual(server._news_cover(connection, f"/news-cover/{rid}").status_code, 401)
            self.assertEqual(server._news_cover(connection, "/news-cover/../../secret?token=test").status_code, 404)
            self.assertEqual(server._news_cover(connection, f"/news-cover/{rid}?token=test").body, b'png test')

    async def test_headless_api_drives_and_stops_scheduler(self):
        from desk_companion.local_api.server import scheduled_jobs
        shutdown = asyncio.Event()
        loop = asyncio.get_running_loop()
        ticks = []
        def tick():
            ticks.append(1)
            loop.call_soon_threadsafe(shutdown.set)
        await asyncio.wait_for(scheduled_jobs(SimpleNamespace(automation=SimpleNamespace(tick=tick)), shutdown), 3)
        self.assertEqual(ticks, [1])


if __name__ == "__main__":
    unittest.main()
