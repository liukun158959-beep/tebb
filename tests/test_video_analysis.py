"""长字幕边界、可恢复分段与保存门禁；不访问线上服务。"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from desk_companion import video, video_analysis as analysis


class FakeLLM:
    model = "fake"

    def __init__(self, fail_at=0):
        self.calls = []
        self.fail_at = fail_at

    def chat(self, messages, on_delta=None):
        self.calls.append(messages)
        if len(self.calls) == self.fail_at:
            raise TimeoutError()
        final = "最终只输出学习笔记" in messages[0]["content"]
        text = "# 学习笔记\n概念、关系和补充例子。" if final else json.dumps({"notes": "本段机制、术语与出处。", "verify": []})
        on_delta(text)
        return {"message": {"content": text}, "usage": {"prompt_tokens": 3, "completion_tokens": 2}}


class VideoAnalysisTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": temp.name})
        env.start(); self.addCleanup(env.stop)
        self.source = {"source_id": "a" * 24, "title": "概念教程", "url": "https://youtu.be/abcdefghijk",
            "subtitle_status": "available", "chapters": [{"start": 0, "title": "概念"}, {"start": 10, "title": "应用"}],
            "segments": [{"start": i, "text": str(i) + "字幕" * 900} for i in range(15)]}
        self.request = {"session_id": "session", "text": "分析，不要联网", "limits": {"call_timeout": 90},
                        "sampling": {"reasoning_effort": "low", "temperature": .5, "top_p": 1}}
        self.events = []

    def emit(self, *event):
        self.events.append(event)

    def test_partition_covers_every_character_and_respects_chapters_and_size(self):
        self.source["segments"][0]["text"] = "长" * 14000
        groups = analysis.chunks(self.source)
        self.assertTrue(all(sum(len(x["text"]) for x in g["items"]) <= analysis.CHUNK_CHARS for g in groups))
        for i, row in enumerate(self.source["segments"]):
            self.assertEqual("".join(x["text"] for g in groups for x in g["items"] if x["index"] == i), row["text"])
        self.assertEqual({g["chapter"] for g in groups}, {"概念", "应用"})

    def test_interrupted_analysis_reuses_only_completed_chunks(self):
        first = FakeLLM(fail_at=2)
        with self.assertRaises(TimeoutError):
            analysis.analyze(self.request, self.emit, first, self.source)
        second = FakeLLM()
        with patch("desk_companion.web_search.web_search") as search:
            answer = analysis.analyze(self.request, self.emit, second, self.source)
            search.assert_not_called()
        self.assertIn("学习笔记", answer)
        self.assertEqual(len(second.calls), len(analysis.chunks(self.source)))  # one cached map, plus synthesis
        final_input = json.loads(second.calls[-1][1]["content"])
        self.assertEqual(final_input["coverage"]["segments"], 15)
        self.assertEqual(len(final_input["notes"]), len(analysis.chunks(self.source)))
        self.assertFalse(final_input["coverage"]["visuals_read"])
        self.assertTrue(any(k == "llm_progress" for k, _ in self.events))

    def test_changed_transcript_or_goal_invalidates_cached_notes(self):
        analysis.analyze(self.request, self.emit, FakeLLM(), self.source)
        self.source["segments"][-1]["text"] += "新内容"
        llm = FakeLLM()
        analysis.analyze(self.request, self.emit, llm, self.source)
        self.assertEqual(len(llm.calls), len(analysis.chunks(self.source)) + 1)
        llm = FakeLLM()
        analysis.analyze({**self.request, "video_focus": "仅讲概念"}, self.emit, llm, self.source)
        self.assertEqual(len(llm.calls), len(analysis.chunks(self.source)) + 1)

    def test_checkpoints_do_not_pollute_video_source_catalog(self):
        source = {**self.source, "fetched_at": 1, "segment_count": 15}
        video.atomic_write(video.source_path("session", source["source_id"]), source)
        analysis.analyze(self.request, self.emit, FakeLLM(), source)
        self.assertEqual([s["source_id"] for s in video.sources("session")], [source["source_id"]])

    def test_failed_analysis_does_not_archive_or_claim_complete_answer(self):
        video.atomic_write(video.source_path("session", "a" * 24), self.source)
        from types import SimpleNamespace
        llm = SimpleNamespace(bind_client=lambda *args, **kwargs: None)
        with patch.object(video, "read_video", return_value={"source_id": "a" * 24}), \
             patch("atlas.LLM", return_value=llm), \
             patch("desk_companion.model_catalog.require_active", return_value={"model": "fake", "api_key": "fake", "base_url": "http://fake"}), \
             patch.object(analysis, "analyze", side_effect=TimeoutError()), \
             patch("desk_companion.video_archive.VideoArchive.finish") as archive:
            with self.assertRaises(TimeoutError):
                analysis.execute({**self.request, "video_url": self.source["url"]}, self.emit)
            archive.assert_not_called()
        self.assertFalse(any(k == "result" for k, _ in self.events))

    def test_missing_subtitles_emit_failure_with_reason_and_never_claim_task_completed(self):
        missing = {**self.source, "segments":[], "subtitle_status":"unavailable", "subtitle_notice":"B站字幕接口暂未成功返回（-352），请稍后重试。"}
        video.atomic_write(video.source_path("session", "a"*24),missing)
        with patch.object(video, "read_video", return_value={"source_id":"a"*24,"subtitle_status":"unavailable"}), \
             patch("atlas.LLM") as model, patch.object(analysis,"analyze") as analyze, \
             patch("desk_companion.video_archive.VideoArchive.finish") as archive:
            analysis.execute({**self.request,"video_url":self.source["url"]},self.emit)
        model.assert_not_called(); analyze.assert_not_called(); archive.assert_not_called()
        failure = next(data for kind,data in self.events if kind=="failure")
        self.assertIn("-352",failure["message"])
        self.assertFalse(any(kind=="result" for kind,_ in self.events))

    def test_source_read_failure_keeps_actionable_network_reason(self):
        with patch.object(video,"read_video",side_effect=RuntimeError("视频网络连接失败或超时。")):
            analysis.execute({**self.request,"video_url":self.source["url"]},self.emit)
        failure=next(data for kind,data in self.events if kind=="failure")
        self.assertIn("网络连接失败",failure["message"])
        self.assertFalse(any(kind=="result" for kind,_ in self.events))

    def test_invalid_model_format_gets_one_repair_before_checkpoint(self):
        llm = FakeLLM()
        chat = llm.chat
        first = True
        def invalid_once(messages, on_delta=None):
            nonlocal first
            if first:
                first = False
                llm.calls.append(messages)
                return {"message": {"content": "格式错误的片段"}, "usage": {}}
            return chat(messages, on_delta)
        llm.chat = invalid_once
        answer = analysis.analyze(self.request, self.emit, llm, self.source)
        self.assertIn("学习笔记", answer)
        self.assertEqual(len(llm.calls), len(analysis.chunks(self.source)) + 2)
        self.assertTrue(any("校正一次" in str(data) for kind, data in self.events if kind == "status"))

    def test_single_query_string_is_normalized_without_a_repair_call(self):
        # Actual failing response: valid notes, verify supplied as a string.
        llm = FakeLLM()
        chat = llm.chat
        def single_query(messages, on_delta=None):
            result = chat(messages, on_delta)
            if "最终只输出学习笔记" not in messages[0]["content"]:
                result["message"]["content"] = json.dumps({"notes": "MCP 规定 Host 与 Server 的通信，不限定 Host 与模型的交互格式。", "verify": "Cline MCP XML protocol ReAct"})
            return result
        llm.chat = single_query
        analysis.analyze(self.request, self.emit, llm, self.source)
        self.assertEqual(len(llm.calls), len(analysis.chunks(self.source)) + 1)
        self.assertEqual(analysis.parse_note('{"notes":"有效笔记","verify":""}')["verify"], [])
        for value in (None, {}, [1], "x" * 201):
            with self.assertRaises(ValueError):
                analysis.parse_note(json.dumps({"notes": "笔记", "verify": value}))

    def test_connection_failure_retries_once_and_keeps_completed_maps(self):
        import httpx
        llm = FakeLLM()
        chat = llm.chat
        first = True
        def disconnected_once(messages, on_delta=None):
            nonlocal first
            if first:
                first = False
                llm.calls.append(messages)
                raise httpx.ConnectError("UNEXPECTED_EOF_WHILE_READING")
            return chat(messages, on_delta)
        llm.chat = disconnected_once
        analysis.analyze(self.request, self.emit, llm, self.source)
        self.assertEqual(len(llm.calls), len(analysis.chunks(self.source)) + 2)
        self.assertTrue(any("重试一次" in str(d) for k, d in self.events if k == "status"))

    def test_failed_repair_preserves_draft_and_resume_does_not_repeat_extraction(self):
        import httpx
        llm = FakeLLM()
        def failed_repair(messages, on_delta=None):
            llm.calls.append(messages)
            if len(llm.calls) == 1:
                return {"message": {"content": "真实片段笔记，但缺少JSON外层"}}
            raise httpx.ConnectError("secret-url-and-key")
        llm.chat = failed_repair
        with self.assertRaises(httpx.ConnectError) as caught:
            analysis.analyze(self.request, self.emit, llm, self.source)
        self.assertEqual(len(llm.calls), 3)  # extraction, repair, one transport retry
        details = analysis.failure_details(caught.exception)
        self.assertEqual((details["completed"], details["total"]), (0, len(analysis.chunks(self.source))))
        self.assertIn("连接中断", details["message"])
        self.assertNotIn("secret", json.dumps(details))
        self.assertEqual(len(list((video.source_dir("session") / "analysis").glob("*.drafts.json"))), 1)
        second = FakeLLM()
        analysis.analyze(self.request, self.emit, second, self.source)
        self.assertIn("修正为严格JSON", second.calls[0][0]["content"])
        self.assertEqual(json.loads(second.calls[0][1]["content"])["draft"], "真实片段笔记，但缺少JSON外层")
        self.assertEqual(len(second.calls), len(analysis.chunks(self.source)) + 1)

    def test_partial_final_answer_is_not_duplicated_by_transport_retry(self):
        import httpx
        llm = FakeLLM()
        chat = llm.chat
        def partial_final(messages, on_delta=None):
            if "最终只输出学习笔记" in messages[0]["content"]:
                llm.calls.append(messages)
                on_delta("部分答案")
                raise httpx.ReadError("reset")
            return chat(messages, on_delta)
        llm.chat = partial_final
        with self.assertRaises(httpx.ReadError) as caught:
            analysis.analyze(self.request, self.emit, llm, self.source)
        self.assertEqual(len(llm.calls), len(analysis.chunks(self.source)) + 1)
        self.assertEqual([d for k,d in self.events if k == "token"], ["部分答案"])
        self.assertEqual(analysis.failure_details(caught.exception)["completed"], len(analysis.chunks(self.source)))

    def test_twice_invalid_format_reports_exact_phase_and_never_completes_chunk(self):
        llm = FakeLLM()
        def invalid(messages, on_delta=None):
            llm.calls.append(messages)
            return {"message": {"content": "still invalid"}}
        llm.chat = invalid
        with self.assertRaises(ValueError) as caught:
            analysis.analyze(self.request, self.emit, llm, self.source)
        self.assertEqual(len(llm.calls), 2)
        details = analysis.failure_details(caught.exception)
        self.assertIn("片段 1/", details["phase"])
        self.assertIn("格式校正", details["phase"])
        self.assertEqual(details["completed"], 0)
        second = FakeLLM()
        analysis.analyze(self.request, self.emit, second, self.source)
        self.assertIn("修正为严格JSON", second.calls[0][0]["content"])

    def test_plain_link_detection_does_not_hijack_mixed_tasks(self):
        self.assertTrue(analysis.initial_video_url(self.source["url"]))
        self.assertTrue(analysis.initial_video_url("帮我总结下这个视频：" + self.source["url"]))
        for text in ("总结并创建日程 " + self.source["url"], "https://youtube.com.evil/watch?v=abcdefghijk", "https://localhost/video"):
            self.assertIsNone(analysis.initial_video_url(text))


if __name__ == "__main__":
    unittest.main()
