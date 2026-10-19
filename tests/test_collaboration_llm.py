import json
import unittest

import httpx
from openai import RateLimitError

from desk_companion.collaboration_llm import CollaborationLLM

MODEL = dict(model='fixture', api_key='fixture-key', base_url='https://example.invalid/v1/')
SAMPLING = dict(reasoning_effort='low', temperature=.5, top_p=1)


class StreamingTests(unittest.TestCase):
    def test_glm_tool_reasoning_is_returned_only_to_model_and_tool_stream_reports_progress(self):
        seen=[]
        chunks=[dict(choices=[dict(delta=dict(reasoning_content='opaque-model-reasoning'))]),
                dict(choices=[dict(delta=dict(tool_calls=[dict(index=0,id='tool',function=dict(name='workspace_read',arguments='{"path":"a.py"}'))]),finish_reason='tool_calls')])]
        def handle(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')
        client=httpx.Client(transport=httpx.MockTransport(handle));self.addCleanup(client.close)
        llm=CollaborationLLM({**MODEL,'model':'glm-5.3-flash'},{**SAMPLING,'reasoning_effort':'max'},20,client,purpose='route')
        progress=[];visible=[];llm.on_progress=lambda:progress.append(True)
        first=llm.chat([],tools=[dict(type='function')],on_delta=visible.append)
        self.assertEqual(first['message']['reasoning_content'],'opaque-model-reasoning')
        llm.chat([first['message']])
        self.assertEqual(seen[1]['messages'][0]['reasoning_content'],'opaque-model-reasoning')
        self.assertEqual(seen[0]['thinking'],dict(type='enabled',clear_thinking=False))
        self.assertEqual(seen[0]['reasoning_effort'],'low');self.assertTrue(seen[0]['tool_stream'])
        self.assertTrue(progress);self.assertEqual(visible,[])
    def test_owned_transport_is_reused_only_within_one_worker_and_closed(self):
        from unittest.mock import patch
        client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')))
        with patch('desk_companion.model_network.http_client',return_value=client) as make:
            with CollaborationLLM(MODEL,SAMPLING,20) as llm:
                llm.chat([]);llm.chat([]);make.assert_called_once()
            self.assertTrue(client.is_closed)

    def llm(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return CollaborationLLM(MODEL, SAMPLING, 20, client)

    def test_stream_separates_reasoning_preserves_usage_and_tool_fragments(self):
        seen = []
        chunks = [dict(choices=[dict(index=0, delta=dict(reasoning_content='private'))]),
                  dict(choices=[dict(index=0, delta=dict(content='你好', tool_calls=[dict(index=0,id='call-1',function=dict(name='web_',arguments='{"query":'))]))]),
                  dict(choices=[dict(index=0, delta=dict(tool_calls=[dict(index=0,function=dict(name='search',arguments='"AI"}'))]),finish_reason='tool_calls')]),
                  dict(choices=[], usage=dict(prompt_tokens=12,completion_tokens=9,total_tokens=21))]
        def handle(request):
            body=json.loads(request.content);seen.append(body)
            self.assertEqual(request.url.path,'/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'],'Bearer fixture-key')
            return httpx.Response(200,text=': keepalive\n\n'+''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')
        llm=self.llm(handle);text=[];reasoning=[];llm.on_reasoning=reasoning.append
        fragments=[];llm.on_tool_delta=lambda *args:fragments.append(args)
        result=llm.chat([dict(role='user',content='test')],tools=[dict(type='function')],on_delta=text.append)
        self.assertEqual(text,['你好']);self.assertEqual(reasoning,['private'])
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual(result['usage']['total_tokens'],21)
        self.assertEqual(result['message']['tool_calls'][0]['function'],dict(name='web_search',arguments='{"query":"AI"}'))
        self.assertEqual(fragments,[(0,'web_','{"query":'),(0,'web_search','{"query":"AI"}')])
        self.assertEqual(seen[0]['reasoning_effort'],'low')
        self.assertTrue(seen[0]['stream_options']['include_usage'])

    def test_incomplete_stream_retains_delivered_tokens_and_never_retries(self):
        calls=[];text=[]
        def handle(request):
            calls.append(request)
            return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        with self.assertRaisesRegex(RuntimeError,'finish_reason'):
            self.llm(handle).chat([],on_delta=text.append)
        self.assertEqual(text,['partial']);self.assertEqual(len(calls),1)

    def test_http_error_is_not_replayed(self):
        calls=[]
        def handle(request):
            calls.append(request);return httpx.Response(429,text='rate limit')
        with self.assertRaises(RateLimitError):self.llm(handle).chat([])
        self.assertEqual(len(calls),1)


if __name__ == '__main__':unittest.main()
