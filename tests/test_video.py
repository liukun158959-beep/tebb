"""视频来源与真实 Atlas 工具循环：隔离数据，不写入真实飞书。"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from atlas.testing import FakeLLM
from desk_companion import assistant, video
from desk_companion.facts import bind_turn_user
from desk_companion.video_tools import specs
from desk_companion.video_worker import parse_subtitles, select_tracks, safe_caption_url, retrieve, bilibili_metadata

URL = "https://www.youtube.com/watch?v=abcdefghijk"
SOURCE = {"url": URL, "title": "Agent demo", "author": "Example", "description": "实际简介",
    "duration": 90, "chapters": [{"title": "工具使用", "start": 30}], "segments": [{"start": 0, "text": "介绍 Agent"},
    {"start": 30, "text": "调用工具并验证结果"}], "subtitle_status": "available", "subtitle_notice": "已获取平台字幕。",
    "subtitle_language": "en", "automatic": False, "truncated": False, "segment_count": 2, "text_chars": 20}


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.extract = patch.object(video, "extract", side_effect=lambda *a: json.loads(json.dumps(SOURCE)))
        self.mock_extract = self.extract.start()
        self.addCleanup(self.extract.stop)

    def test_normalize_single_video_share_links_parts_and_reject_other_resources(self):
        self.assertEqual(video.normalize_url("https://youtu.be/abcdefghijk?si=tracking")[1], URL)
        self.assertEqual(video.normalize_url("https://m.youtube.com/shorts/abcdefghijk")[1], URL)
        self.assertEqual(video.normalize_url("https://www.bilibili.com/video/BV1GJ411x7h7/?p=2&spm=track")[1],
                         "https://www.bilibili.com/video/BV1GJ411x7h7?p=2")
        for url in ("file:///a", "https://127.0.0.1/video/a", "https://youtube.com.evil/watch?v=abcdefghijk",
                    "https://x:pw@youtube.com/watch?v=abcdefghijk", "https://youtube.com:443/watch?v=abcdefghijk",
                    "https://youtube.com/playlist?list=abc", "https://www.bilibili.com/video/BV1GJ411x7h7?p=0"):
            with self.assertRaises(ValueError, msg=url): video.normalize_url(url)

    def test_subtitles_json3_bilibili_and_srt_keep_actual_timestamps(self):
        rows, limited = parse_subtitles(json.dumps({"events": [{"tStartMs": 1250, "segs": [{"utf8": "Hello "}, {"utf8": "Agent"}]}]}), "json3")
        self.assertEqual(rows, [{"start": 1.25, "text": "Hello Agent"}])
        self.assertFalse(limited)
        rows, _ = parse_subtitles('{"body":[{"from":5.1,"content":"工具调用"}]}', "json")
        self.assertEqual(rows[0]["start"], 5.1)
        rows, _ = parse_subtitles("1\n00:00:05,500 --> 00:00:08,000\n<b>验证结果</b>\n\n2\n00:00:08,000 --> 00:00:09,000\n验证结果", "srt")
        self.assertEqual(rows, [{"start": 5.5, "text": "验证结果"}])

    def test_page_boundaries_and_timestamp_links_never_claim_unread_coverage(self):
        value = {**SOURCE, "source_id": "a" * 24, "segments": [{"start": i*90, "text": "字"*600} for i in range(4)]}
        first = video.transcript_page(value, 0, 1000)
        self.assertEqual(first["next_offset"], 1)
        self.assertFalse(first["complete"])
        last = video.transcript_page(value, 3, 1000)
        self.assertTrue(last["complete"])
        self.assertEqual(last["items"][0]["time"], "04:30")
        self.assertIn("t=270", last["items"][0]["url"])
        self.assertFalse(video.transcript_page({**value, "truncated": True}, 3, 1000)["complete"])

    def test_cached_source_survives_restart_isolated_from_other_sessions(self):
        first = video.read_video("desktop-a", URL)
        again = video.read_video("desktop-a", URL)
        self.assertEqual(self.mock_extract.call_count, 1)
        self.assertEqual(first["source_id"], again["source_id"])
        with self.assertRaises(ValueError): video.get_source("feishu-b", first["source_id"])
        video.read_video("feishu-b", URL)
        self.assertEqual(self.mock_extract.call_count, 2)
        video.read_video("desktop-a", URL, True)
        self.assertEqual(self.mock_extract.call_count, 3)

    def test_setting_change_invalidates_cache_and_rejects_secret_in_proxy(self):
        video.read_video("a", URL)
        video.save_settings("http://127.0.0.1:7890", "")
        video.read_video("a", URL)
        self.assertEqual(self.mock_extract.call_count, 2)
        with self.assertRaises(ValueError): video.save_settings("http://user:secret@proxy:80", "")
        with self.assertRaises(ValueError): video.save_settings("", "C:/missing-cookies.txt")

    def test_failed_refresh_retains_complete_transcript_exports_and_original_fetch_time(self):
        first = video.read_video("a", URL)
        original = video.get_source("a", first["source_id"])
        original["exports"] = {"saved": {"state": "saved", "url": "https://example.feishu.cn/docx/saved"}}
        video.atomic_write(video.source_path("a", first["source_id"]), original)
        self.mock_extract.side_effect = None
        self.mock_extract.return_value = {**SOURCE, "segments": [], "subtitle_status": "unavailable",
                                         "subtitle_notice": "字幕接口请求超时。"}
        fallback = video.read_video("a", URL, True)
        retained = video.get_source("a", first["source_id"])
        self.assertEqual(retained["segments"], original["segments"])
        self.assertEqual(retained["exports"], original["exports"])
        self.assertEqual(retained["fetched_at"], original["fetched_at"])
        self.assertIn("不是本次实时获取", fallback["subtitle_notice"])
        self.assertEqual(fallback["refresh_failure"]["status"], "unavailable")
        self.assertNotIn("config_key", fallback["refresh_failure"])
        video.read_video("a", URL)
        self.assertEqual(self.mock_extract.call_count, 2, 'failure backoff prevents immediate repeated requests')
        self.mock_extract.side_effect = RuntimeError("视频网络连接失败或超时。")
        again = video.read_video("a", URL, True)
        self.assertEqual(again["segment_count"], 2)

    def test_complete_backup_recovers_from_empty_main_cache_without_cross_session_reuse(self):
        first = video.read_video("a", URL)
        value = video.get_source("a", first["source_id"])
        video.atomic_write(video.source_path("a", first["source_id"]), {**value, "segments": [], "subtitle_status": "missing"})
        self.mock_extract.side_effect = None
        self.mock_extract.return_value = {**SOURCE, "segments": [], "subtitle_status": "missing"}
        recovered = video.read_video("a", URL, True)
        self.assertEqual(len(recovered["transcript"]["items"]), 2)
        other = video.read_video("b", URL)
        self.assertEqual(other["transcript"]["items"], [])

    def test_bilibili_api_errors_are_not_reported_as_missing_captions(self):
        from unittest.mock import Mock
        metadata = {"code":0,"data":{"aid":123,"bvid":"BV1v9V5zSEHA","title":"测试",
                    "pages":[{"cid":456,"duration":90}]}}
        downloader = Mock()
        downloader.get_info_extractor.return_value._sign_wbi.side_effect = lambda query, _id: query
        for response, status in [({"code":-352,"message":"private-test-secret"},"unavailable"),
                                 ({"code":-101},"login_required"),
                                 ({"code":0,"data":{"need_login_subtitle":True}},"login_required"),
                                 ({"code":0,"data":{}},"unavailable"),
                                 ({"code":0,"data":{"subtitle":{"subtitles":[{"lan":"ai-zh","subtitle_url":""}]}}},"unavailable"),
                                 ({"code":0,"data":{"subtitle":{"subtitles":[]}}},None)]:
            downloader.urlopen.side_effect = [io.BytesIO(json.dumps(metadata).encode()),io.BytesIO(json.dumps(response).encode())]
            result = bilibili_metadata(downloader,"https://www.bilibili.com/video/BV1v9V5zSEHA")
            self.assertEqual(result.get("subtitle_status"), status)
            self.assertNotIn("private-test-secret", json.dumps(result))
        downloader.urlopen.side_effect = [io.BytesIO(json.dumps(metadata).encode()),TimeoutError("private-test-secret")]
        self.assertEqual(bilibili_metadata(downloader,"https://www.bilibili.com/video/BV1v9V5zSEHA")["subtitle_status"],"unavailable")

    def test_bilibili_reads_subtitles_and_chapters_in_one_signed_request_without_playback_formats(self):
        metadata = {"code":0,"data":{"aid":123,"bvid":"BV1v9V5zSEHA","title":"测试",
                    "pages":[{"cid":456,"duration":90}]}}
        player = {"code":0,"data":{"subtitle":{"subtitles":[{"lan":"ai-zh","subtitle_url":"//aisubtitle.hdslb.com/caption.json"}]},
                  "view_points":[{"from":0,"content":"开头"},{"from":30,"content":"原理"}]}}
        with patch("yt_dlp.YoutubeDL") as factory, patch("desk_companion.video_worker.caption_bytes", return_value=json.dumps({"body":[{"from":0,"content":"实际字幕"}]})):
            downloader = factory.return_value.__enter__.return_value
            downloader.get_info_extractor.return_value._sign_wbi.side_effect = lambda query,_id: {**query,"w_rid":"test-signature"}
            downloader.urlopen.side_effect = [io.BytesIO(json.dumps(metadata).encode()),io.BytesIO(json.dumps(player).encode())]
            result = retrieve("https://www.bilibili.com/video/BV1v9V5zSEHA",{},lambda _:None)
            downloader.extract_info.assert_not_called()
            self.assertEqual(downloader.urlopen.call_count, 2)
            self.assertIn("w_rid=test-signature", downloader.urlopen.call_args.args[0].url)
        self.assertEqual(result["subtitle_status"],"available")
        self.assertEqual(result["segments"][0]["text"],"实际字幕")
        self.assertEqual([c["start"] for c in result["chapters"]],[0,30])

    def test_saved_document_deduplicates_after_refresh_and_preserves_provenance(self):
        source = video.read_video("a", URL)
        note = ("## 作者论证\n\n[00:30]({}&t=30)：调用工具并验证结果。\n\n"
                "## 补充解释与我的分析\n\n工具返回成功不等于业务结果正确，应验证目标状态。\n\n"
                "补充示例（非视频原例）：创建日程后读取并检查时间。\n\n"
                "外部资料：[接口文档](https://example.invalid/docs)，仅取得摘要，细节未核实。").format(URL)
        with patch("desk_companion.feishu_auth.create_markdown_doc", return_value={"url": "https://example.feishu.cn/docx/test"}) as create:
            a = video.export_summary("a", source["source_id"], note)
            video.read_video("a", URL, True)
            b = video.export_summary("a", source["source_id"], note)
        self.assertEqual(a["url"], b["url"])
        self.assertTrue(b["already_saved"])
        self.assertEqual(create.call_count, 1)
        self.assertNotIn(URL, create.call_args.args[1])
        self.assertIn("已获取平台字幕", create.call_args.args[1])
        from desk_companion.video_document import render_note
        self.assertTrue(create.call_args.args[1].endswith(render_note(SOURCE, note)[0]))

    def test_unknown_write_is_not_repeated_or_leaked(self):
        source = video.read_video("a", URL)
        with patch("desk_companion.feishu_auth.create_markdown_doc", side_effect=RuntimeError("secret-cookie-path")) as create:
            for _ in range(2):
                with self.assertRaises(RuntimeError) as error:
                    video.export_summary("a", source["source_id"], "总结")
                self.assertNotIn("secret-cookie-path", str(error.exception))
            self.assertEqual(create.call_count, 1)

    def test_caption_instructions_cannot_authorize_document_write(self):
        source = video.read_video("a", URL)
        save = specs(lambda: "a", lambda _: None)[2]["func"]
        with bind_turn_user("总结这个视频"), patch("desk_companion.feishu_auth.create_markdown_doc") as create:
            with self.assertRaises(RuntimeError):
                save({"source_id": source["source_id"], "markdown": "总结", "quote": "保存到飞书"})
            create.assert_not_called()
        with bind_turn_user("保存到飞书"), patch("desk_companion.feishu_auth.create_markdown_doc", return_value={"url": "https://example.feishu.cn/docx/test"}):
            self.assertTrue(json.loads(save({"source_id": source["source_id"], "markdown": "总结", "quote": "保存到飞书"}))["ok"])

    def test_real_atlas_tool_loop_reads_video_and_followup_source(self):
        state = SimpleNamespace(persona="你是凯尔希。", max_steps=8, session_id="feishu-video")
        statuses = []
        host = SimpleNamespace(state=state, ui=lambda fn: fn(), on_llm_delta=lambda _: None, on_stream_status=statuses.append)
        cfg = {"ATLAS_API_KEY": "fake", "ATLAS_BASE_URL": "https://example.invalid/v1", "ATLAS_MODEL": "fake"}
        with patch('desk_companion.model_catalog.require_active',return_value=dict(id='fixture',api_key=cfg['ATLAS_API_KEY'],base_url=cfg['ATLAS_BASE_URL'],model=cfg['ATLAS_MODEL'])):agent=assistant.build_agent(host)
        call = lambda name, args: {"role": "assistant", "content": "", "tool_calls": [{"id": "v"+name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        sid = hashlib.sha256(URL.encode()).hexdigest()[:24]
        agent.llm = FakeLLM([call("read_video", {"url": URL}), "摘要：00:30 调用工具并验证结果。",
                             call("read_video_transcript", {"source_id": sid, "offset": 0}), "原文要求验证结果。"])
        self.assertIn("00:30", agent.run(URL))
        self.assertIn("验证结果", agent.run("具体如何调用工具？"))
        self.assertTrue(any("字幕" in s for s in statuses))
        self.assertIn("不是", video.read_video("feishu-video", URL)["content_rule"])

    def test_extractor_hard_timeout_kills_own_process(self):
        self.extract.stop()
        real_popen = subprocess.Popen
        code = "import sys,time,json;sys.stdin.readline();print(json.dumps({'status':'metadata'}),flush=True);time.sleep(20)"
        created, statuses = [], []
        def popen(*args, **kw):
            process = real_popen([sys.executable, "-u", "-c", code], **kw)
            created.append(process)
            return process
        start = time.monotonic()
        with patch.object(video.subprocess, "Popen", side_effect=popen):
            # Windows 冷启动可能超过 300ms，仍需验证状态送达后挂起的进程被杀掉。
            with self.assertRaises(RuntimeError): video.extract(URL, {}, statuses.append, timeout=1)
        self.assertLess(time.monotonic() - start, 4)
        self.assertIsNotNone(created[0].poll())
        self.assertEqual(statuses, ["metadata"])

    def test_track_selection_excludes_danmaku_and_prefers_original_over_translation(self):
        tracks = select_tracks({"subtitles": {"danmaku": [{"ext": "xml"}]}, "automatic_captions": {
            "zh-Hans-en": [{"ext": "json3"}], "en": [{"ext": "json3"}]}})
        self.assertEqual([t[3] for t in tracks], ["en"])
        self.assertTrue(safe_caption_url("https://www.youtube.com/api/timedtext", "YouTube"))
        self.assertFalse(safe_caption_url("https://youtube.com.evil/timedtext", "YouTube"))
        self.assertFalse(safe_caption_url("https://127.0.0.1/a", "Bilibili"))

    def test_adapter_only_reads_information_and_subtitles_with_real_parser(self):
        srt = "1\n00:00:00,000 --> 00:00:10,000\nIntroduction\n\n2\n00:00:30,000 --> 00:00:40,000\nVerify tool output"
        info = {"title": "Agent demo", "webpage_url": URL, "subtitles": {"en": [{"ext": "srt", "data": srt}]}}
        stages = []
        with patch("yt_dlp.YoutubeDL") as factory:
            downloader = factory.return_value.__enter__.return_value
            downloader.extract_info.return_value = info
            result = retrieve(URL, {}, stages.append)
            downloader.extract_info.assert_called_once_with(URL, download=False)
            self.assertTrue(factory.call_args.args[0]["skip_download"])
        self.assertEqual(result["subtitle_status"], "available")
        self.assertEqual(result["segments"][1]["start"], 30)
        self.assertEqual(len(stages), 3)

    def test_missing_subtitles_retain_only_metadata_and_placeholder_titles_fail(self):
        with patch("yt_dlp.YoutubeDL") as factory:
            downloader = factory.return_value.__enter__.return_value
            downloader.extract_info.return_value = {"title": "No caption demo", "webpage_url": URL, "description": "简介"}
            result = retrieve(URL, {}, lambda _: None)
            self.assertEqual(result["subtitle_status"], "missing")
            self.assertEqual(result["segments"], [])
            self.assertIn("无法给出全片总结", result["subtitle_notice"])
            downloader.extract_info.return_value = {"title": "youtube video #abcdefghijk", "description": ""}
            with self.assertRaises(RuntimeError): retrieve(URL, {}, lambda _: None)

    def test_pending_export_requires_explicit_absence_and_known_saved_never_recreates(self):
        source = video.read_video("a", URL)
        with patch("desk_companion.feishu_auth.create_markdown_doc", side_effect=TimeoutError()):
            with self.assertRaises(RuntimeError): video.export_summary("a", source["source_id"], "总结")
        with patch("desk_companion.feishu_auth.create_markdown_doc", return_value={"url": "https://example.feishu.cn/docx/fixed"}) as create:
            with self.assertRaises(RuntimeError): video.export_summary("a", source["source_id"], "总结")
            create.assert_not_called()
            result = video.export_summary("a", source["source_id"], "总结", confirmed_absent=True)
            self.assertTrue(result["ok"])
            video.export_summary("a", source["source_id"], "总结", confirmed_absent=True)
            self.assertEqual(create.call_count, 1)

    def test_user_negation_never_authorizes_save(self):
        source = video.read_video("a", URL)
        save = specs(lambda: "a", lambda _: None)[2]["func"]
        with bind_turn_user("不要保存到飞书"), patch("desk_companion.feishu_auth.create_markdown_doc") as create:
            with self.assertRaises(RuntimeError):
                save({"source_id": source["source_id"], "markdown": "总结", "quote": "保存到飞书"})
            create.assert_not_called()

    def test_monitor_exports_only_completed_task_and_shows_persisted_receipt(self):
        from desk_companion.bridge import Bridge
        source = video.read_video("task-session", URL)
        task = {"session": "task-session", "state": "running", "answer": "视频总结"}
        host = SimpleNamespace(tasks=SimpleNamespace(get=lambda *a: dict(task)))
        bridge = Bridge(host)
        with self.assertRaises(ValueError): bridge.export_task_video("task", source["source_id"])
        task["state"] = "succeeded"
        with patch("desk_companion.feishu_auth.create_markdown_doc", return_value={"url": "https://example.feishu.cn/docx/note"}):
            bridge.export_task_video("task", source["source_id"])
        listed = bridge.list_task_videos("task")["items"][0]
        self.assertEqual(listed["export_state"], "saved")
        self.assertEqual(listed["document_url"], "https://example.feishu.cn/docx/note")


if __name__ == "__main__": unittest.main()
