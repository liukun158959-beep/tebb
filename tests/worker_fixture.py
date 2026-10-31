"""仅离线测试启动：真实 worker/Atlas 循环，模型脚本在子进程内注入。"""
from pathlib import Path
import importlib.abc
import json
import os
import sys
import uuid


class NoProviderSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'openai' or fullname.startswith('openai.'):
            raise AssertionError('Offline worker must not import the real provider SDK')


sys.meta_path.insert(0, NoProviderSDK())
from unittest.mock import patch
from types import SimpleNamespace
from atlas.testing import FakeLLM
from desk_companion import assistant, model_catalog, video
from desk_companion.task_worker import main
from desk_companion.paths import data_root

original = assistant.build_agent
fixture_roots=[]
original_stdin=sys.stdin

class RegisteredInput:
    """Standby still warms before reading; standalone fixtures emulate admission."""
    def readline(self):
        from desk_companion.task_budgets import Ledger,default_tier
        request=json.loads(original_stdin.readline())
        ident=request.setdefault('task_id',uuid.uuid4().hex);ledger=Ledger()
        if not ledger.member(ident):
            root=ledger.create('offline-fixture:'+ident,default_tier(request.get('kind',''),request.get('role'),request.get('workflow','')))
            ledger.bind(root,ident,root_member=True,purpose=request.get('kind')or'task');ledger.start(ident)
            fixture_roots.append((ledger,root,ident))
        return json.dumps(request,ensure_ascii=False)+'\n'

sys.stdin=RegisteredInput()

def video_llm(**kwargs):
    import json
    llm = FakeLLM([json.dumps({"notes": "00:00 Agent 决策与执行的测试字幕要点", "verify": []}, ensure_ascii=False), "视频学习笔记：Agent 决策与执行。"])
    return llm

def build(host):
    agent = original(host)
    def query(messages, tools):
        (data_root() / "seen.json").write_text(__import__('json').dumps(messages, ensure_ascii=False), "utf-8")
        if "日程锁测试" in messages[-1].get("content", ""):
            return {"role": "assistant", "content": "", "tool_calls": [{"id": "call_calendar", "type": "function",
                "function": {"name": "create_calendar_event", "arguments": __import__('json').dumps(dict(
                    summary='calendar fixture', start='2026-10-20T09:00:00+08:00', end='2026-10-20T10:00:00+08:00'))}}]}
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
        if os.environ.get('DESK_TEST_UNKNOWN_WRITE')=='1':
            return json.dumps(dict(ok=False,operation=dict(id='fixture-original',state='unconfirmed'),change_state='unconfirmed'))
        return "已完成测试写入"
    agent.tools.register(name="channel_write", description="离线写入测试", parameters={"type":"object","properties":{"attempt":{"type":"string"}}},
                         func=write, retry_max=0)
    agent.llm = FakeLLM(["依据资料的知识回答。"] if os.environ.get('DESK_TEST_KNOWLEDGE') == '1'
                        else [query, "本地流式回归正常。"])
    if os.environ.get('DESK_TEST_UNKNOWN_WRITE')=='1':
        def again(messages,tools):
            return dict(role='assistant',content='',tool_calls=[dict(id='call_2',type='function',function=dict(name='channel_write',arguments='{"attempt":"retry"}'))])
        agent.llm=FakeLLM([query,again,'原操作未确认，未重复执行。'])
    return agent

def create_note(title, body, **kwargs):
    import json
    (data_root() / "archive_created.json").write_text(json.dumps({"title": title, "body": body, **kwargs}, ensure_ascii=False), "utf-8")
    return {"url": "https://example.feishu.cn/wiki/note"}

def extract_video(*args, **kwargs):
    return {"url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "视频归档测试", "author": "离线作者", "chapters": [],
            "segments": [{"start": 0, "text": "Agent 决定下一步"}], "subtitle_status": "available", "subtitle_notice": "已取得测试字幕", "truncated": False}

# Standby preloads the adapter; keep this fixture on FakeLLM while the SDK
# import guard still rejects accidental provider imports in the tool loop.
with patch.dict(sys.modules, {"desk_companion.collaboration_llm": SimpleNamespace(CollaborationLLM=FakeLLM)}), \
     patch.object(assistant, "build_agent", build), patch.object(model_catalog, "bind_llm"), \
     patch.object(model_catalog, "require_active", return_value={"id":"fixture","model":"fake", "api_key":"test-only", "base_url":"http://example.invalid/v1"}), \
     patch("desk_companion.execution_context.ExecutionLLM", side_effect=video_llm), \
     patch.object(video, "extract", extract_video), \
     patch("desk_companion.feishu_auth.create_markdown_doc", create_note):
    try:main()
    finally:
        for ledger,root,ident in fixture_roots:ledger.end(ident);ledger.finish(root)
