import gzip
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI
from desk_companion import agent_debug as debug


class Stream(httpx.SyncByteStream):
    def __init__(self, content):
        self.content = content
    def __iter__(self):
        for i in range(0, len(self.content), 23):
            yield self.content[i:i+23]


class DebugTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": self.temp.name})
        self.env.start(); self.addCleanup(self.env.stop)
        old, default = httpx.Client.send, debug._default
        self.addCleanup(setattr, httpx.Client, "send", old)
        self.addCleanup(setattr, debug, "_default", default)
        debug.install({"task_id": "task-own", "session": "session-own", "channel": "video"})

    def test_actual_sdk_request_and_return_are_complete_without_auth_headers(self):
        text = "长上下文" * 9000 + "最后一行必须保留"
        request_body = []
        def handle(req):
            request_body.append(req.content)
            return httpx.Response(200, json={"id": "chat1", "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20000, "completion_tokens": 30}})
        with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
            client = OpenAI(api_key="PRIVATE_API_KEY", base_url="https://example.invalid/v1", http_client=transport, max_retries=0)
            result = client.chat.completions.create(model="test", messages=[{"role": "system", "content": text}, {"role": "user", "content": "检查完整载荷"}],
                tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}}], temperature=.3)
        row = debug.listing()["items"][0]; detail = debug.detail(row["id"])
        self.assertEqual(detail["request_raw"].encode(), request_body[0])
        self.assertEqual(detail["request"]["messages"][0]["content"], text)
        self.assertEqual(detail["response"]["choices"][0]["message"]["content"], result.choices[0].message.content)
        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["task_id"], "task-own")
        self.assertNotIn("PRIVATE_API_KEY", json.dumps(detail))
        self.assertEqual(detail["request"]["temperature"], .3)

    def test_sdk_sse_decodes_gzip_unicode_tool_deltas_usage_and_done_close(self):
        events = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "可见的返回字段", "content": "正文"}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "tool1", "type": "function", "function": {"name": "lookup", "arguments": '{"id":'}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"中文"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 25, "completion_tokens": 10}}]
        raw = ": keepalive\n\n" + "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events) + "data: [DONE]\n\n"
        def handle(req):
            return httpx.Response(200, headers={"Content-Type": "text/event-stream", "Content-Encoding": "gzip"}, stream=Stream(gzip.compress(raw.encode())))
        with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
            client = OpenAI(api_key="test", base_url="https://example.invalid/v1", http_client=transport, max_retries=0)
            stream = client.chat.completions.create(model="test", messages=[{"role": "user", "content": "test"}], stream=True)
            chunks = list(stream); stream.close()
        d = debug.detail(debug.listing()["items"][0]["id"])
        self.assertTrue(chunks)
        self.assertEqual(d["response_raw"], raw)
        message = d["response"]["choices"][0]["message"]
        self.assertEqual(message["reasoning_content"], "可见的返回字段")
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"], '{"id":"中文"}')
        self.assertEqual(d["response"]["usage"]["prompt_tokens"], 25)
        self.assertEqual(d["call"]["state"], "complete")

    def test_pause_and_non_llm_requests_are_not_recorded(self):
        def handle(req): return httpx.Response(200, json={"ok": True})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            client.post('https://example.invalid/tools', json={"message": "tool content"})
            client.post('https://example.invalid/embeddings', json={"model": "embed", "input": "document"})
            debug.configure(False)
            client.post('https://example.invalid/chat/completions', json={"model": "test", "messages": []})
        self.assertEqual(debug.listing()["items"], [])

    def test_rejected_stream_is_a_failure_and_does_not_look_like_user_cancellation(self):
        def handle(req):return httpx.Response(429,stream=Stream(b'{"error":"rate limited"}'))
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with client.stream('POST','https://example.invalid/v1/chat/completions',json={'model':'test','messages':[]}) as response:
                with self.assertRaises(httpx.HTTPStatusError):response.raise_for_status()
        row=debug.listing()['items'][0]
        self.assertEqual(row['state'],'failed');self.assertEqual(row['status'],429)

    def test_interruption_records_partial_return_and_dead_worker_is_marked(self):
        def handle(req): return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream(b'data: {"choices":[]}\n\n'))
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            request = client.build_request('POST', 'https://example.invalid/chat/completions', json={"model": "test", "messages": []})
            response = client.send(request, stream=True)
            response.close()
        row = debug.listing()["items"][0]
        self.assertEqual(row["state"], "interrupted")
        debug.update(row["id"], state="receiving", pid=99999999)
        self.assertEqual(debug.detail(row["id"])["call"]["state"], "interrupted")

    def test_request_error_is_recorded_without_sensitive_exception_text(self):
        def handle(req): raise httpx.ConnectError('PRIVATE_API_KEY', request=req)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(httpx.ConnectError):
                client.post('https://example.invalid/chat/completions?api_key=SECRET_QUERY', json={"model": "test", "messages": []})
        row = debug.listing()["items"][0]
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["error"], "ConnectError")
        self.assertNotIn('SECRET_QUERY', row['endpoint'])

    def test_context_and_invalid_identifier(self):
        def handle(req): return httpx.Response(200, json={"choices": []})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with debug.context(task_id='other', channel='debug'):
                client.post('https://example.invalid/chat/completions', json={"model": "test", "messages": []})
            client.post('https://example.invalid/chat/completions', json={"model": "test", "messages": []})
        self.assertEqual(len(debug.listing(task_id='other')['items']), 1)
        self.assertEqual(debug.listing()["items"][0]['task_id'], 'task-own')
        with self.assertRaises(ValueError): debug.detail('../outside')

    def test_explanation_sends_complete_selected_payload_with_no_executable_tools(self):
        from atlas import LLM
        long_text = '完整来源数据' * 5000 + '来源最后一行'
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '来源返回'}}]}))) as client:
            client.post('https://example.invalid/chat/completions', json={'model': 'test', 'messages': [{'role': 'user', 'content': long_text}],
                'tools': [{'type': 'function', 'function': {'name': 'execute_command'}}]})
        source_id = debug.listing()['items'][0]['id']
        seen = []
        raw = 'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': '这是只读解读。'}, 'finish_reason': 'stop'}]}) + '\n\ndata: [DONE]\n\n'
        def handle(req):
            seen.append(json.loads(req.content))
            return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=Stream(raw.encode()))
        with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
            def make_llm(**kwargs):
                llm = LLM(**kwargs)
                return llm
            emitted = []
            with patch('desk_companion.model_network.openai_client',side_effect=lambda **kw:OpenAI(api_key='test',base_url='https://example.invalid/v1',http_client=transport,max_retries=0)), patch('desk_companion.execution_context.ExecutionLLM', side_effect=make_llm), patch('desk_companion.model_catalog.require_active', return_value={
                    'model': 'test', 'api_key': 'test', 'base_url': 'https://example.invalid/v1'}):
                debug.execute_explanation({'debug_call_id': source_id, 'text': '解读', 'limits': {'call_timeout': 30},
                    'sampling': {'reasoning_effort': 'low', 'temperature': .5, 'top_p': 1}}, lambda k,d: emitted.append((k,d)))
        payload = seen[0]
        self.assertNotIn('tools', payload)
        source = json.loads(payload['messages'][1]['content'])
        self.assertEqual(source['request']['messages'][0]['content'], long_text)
        self.assertEqual(source['request']['tools'][0]['function']['name'], 'execute_command')
        self.assertIn(('result', '这是只读解读。'), emitted)
        self.assertEqual(len(debug.listing()['items']), 2)


if __name__ == '__main__': unittest.main()
