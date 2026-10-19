"""仅离线测试启动：真实 worker/Atlas 循环，模型脚本在子进程内注入。"""
from pathlib import Path
import sys
from unittest.mock import patch
from atlas.testing import FakeLLM
from desk_companion import assistant, model_catalog, video
from desk_companion.task_worker import main
from desk_companion.paths import data_root

original = assistant.build_agent

def video_llm(**kwargs):
    import json
    llm = FakeLLM([json.dumps({"notes": "00:00 Agent 决策与执行的测试字幕要点", "verify": []}, ensure_ascii=False), "视频学习笔记：Agent 决策与执行。"])
    return llm

def build(host):
    agent = original(host)
    def query(messages, tools):
        (data_root() / "seen.json").write_text(__import__('json').dumps(messages, ensure_ascii=False), "utf-8")
        if "视频归档测试" in messages[-1].get("content", ""):
            return {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function",
                    "function": {"name": "read_video", "arguments": '{"url":"https://www.youtube.com/watch?v=abcdefghijk"}'}}]}
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function",
                "function": {"name": "channel_write" if "写入测试" in messages[-1].get("content", "") else "channel_lookup", "arguments": "{}"}}]}
    def lookup(args):
        (data_root() / "tool_called").write_text("1", "utf-8")
        return "离线工具结果"
    agent.tools.register(name="channel_lookup", description="离线查询", parameters={"type":"object","properties":{}},
                         func=lookup, isReadOnly=True)
    def write(args):
        path = data_root() / "write_count"
        value = int(path.read_text("utf-8")) if path.exists() else 0
        path.write_text(str(value + 1), "utf-8")
        return "已完成测试写入"
    agent.tools.register(name="channel_write", description="离线写入测试", parameters={"type":"object","properties":{}},
                         func=write, retry_max=0)
    agent.llm = FakeLLM([query, "本地流式回归正常。"])
    return agent

def create_note(title, body, **kwargs):
    import json
    (data_root() / "archive_created.json").write_text(json.dumps({"title": title, "body": body, **kwargs}, ensure_ascii=False), "utf-8")
    return {"url": "https://example.feishu.cn/wiki/note"}

def extract_video(*args, **kwargs):
    return {"url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "视频归档测试", "author": "离线作者", "chapters": [],
            "segments": [{"start": 0, "text": "Agent 决定下一步"}], "subtitle_status": "available", "subtitle_notice": "已取得测试字幕", "truncated": False}

with patch.object(assistant, "require_llm_env", return_value={"ATLAS_API_KEY":"test-only",
        "ATLAS_BASE_URL":"http://example.invalid/v1", "ATLAS_MODEL":"fake"}), \
     patch.object(assistant, "build_agent", build), patch.object(model_catalog, "bind_llm"), \
     patch.object(model_catalog, "require_active", return_value={"model":"fake", "api_key":"test-only", "base_url":"http://example.invalid/v1"}), \
     patch("atlas.LLM", side_effect=video_llm), \
     patch.object(video, "extract", extract_video), \
     patch("desk_companion.feishu_auth.create_markdown_doc", create_note):
    main()
