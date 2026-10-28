"""Provider fault injection uses real HTTPX parsing, no external network."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import httpx
from desk_companion import search_service as service, web_search

GOOD_TEXT = 'Title: Python TaskGroup\nURL: https://docs.python.org/3/library/asyncio-task.html\nPublished: N/A\nHighlights:\nStructured concurrency'
RSS = '<rss><channel><item><title>Official</title><link>https://example.org/doc</link><description>source</description></item></channel></rss>'


def exa(request, *, text=GOOD_TEXT, sse=True, result=None):
    body = json.loads(request.content)
    data = dict(jsonrpc='2.0', id=body['id'], result=result or {'content': [{'type': 'text', 'text': text}]})
    return httpx.Response(200, text='event: message\ndata: ' + json.dumps(data) + '\n\n' if sse else json.dumps(data),
                          headers={'content-type': 'text/event-stream' if sse else 'application/json'})


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.env = patch.dict(os.environ, {'DESK_COMPANION_DATA_DIR': self.folder.name}); self.env.start(); self.addCleanup(self.env.stop)
        self.requests = []

    def transport(self, handler):
        def call(request):
            self.requests.append(request)
            return handler(request)
        mock = patch.object(service.model_network, 'http_client', side_effect=lambda *a, **k: httpx.Client(transport=httpx.MockTransport(call)))
        mock.start(); self.addCleanup(mock.stop)

    def test_public_sse_and_json_without_model_configuration_or_credentials(self):
        for sse in (True, False):
            self.transport(lambda request: exa(request, sse=sse))
            with patch('desk_companion.envconf.require_llm_env', side_effect=AssertionError('must not read model')):
                result = service.search('Python TaskGroup')
            self.assertEqual(result['provider'], 'exa'); self.assertEqual(result['results'][0]['published_date'], '')
            request = self.requests[-1]; self.assertNotIn('authorization', request.headers)
            args = json.loads(request.content)['params']['arguments']
            self.assertIn('objective', args); self.assertEqual(args['query'], 'Python TaskGroup')

    def test_unsupported_and_rate_limit_are_not_retried_and_share_cooldown(self):
        for status in (404, 405, 501, 429):
            with self.subTest(status=status):
                service._cooldown('exa', 1)
                with patch.object(service.time, 'time', return_value=10**12):
                    self.requests.clear()
                    self.transport(lambda r: httpx.Response(status, headers={'retry-after': '60'}, text='<html>PRIVATE</html>') if r.url.host == 'mcp.exa.ai' else httpx.Response(200, text=RSS))
                    first = service.search('query'); second = service.search('another')
                self.assertEqual(first['provider'], 'bing-rss')
                self.assertEqual(sum(r.url.host == 'mcp.exa.ai' for r in self.requests), 1)
                self.assertEqual(second['attempts'][0]['category'], 'cooldown')
                self.assertNotIn('PRIVATE', str(first)); self.assertNotIn('<html>', web_search.format_search(first))

    def test_transient_retry_then_success_has_attempt_diagnostics(self):
        count = 0
        def handler(r):
            nonlocal count
            count += 1
            if count == 1: raise httpx.ConnectError('TLS EOF PRIVATE_TOKEN', request=r)
            return exa(r)
        self.transport(handler); result = service.search('query')
        self.assertEqual([a['ok'] for a in result['attempts']], [False, True])
        self.assertEqual(result['attempts'][0]['category'], 'connection_error'); self.assertNotIn('PRIVATE_TOKEN', str(result))

    def test_auth_and_certificate_failures_are_not_hidden(self):
        for status in (401, 403, 'certificate'):
            self.requests.clear()
            def handler(r):
                if status == 'certificate': raise httpx.ConnectError('[SSL: CERTIFICATE_VERIFY_FAILED] PRIVATE', request=r)
                return httpx.Response(status)
            self.transport(handler)
            with self.assertRaises(service.SearchFailure) as caught: service.search('query')
            self.assertEqual(len(self.requests), 1); self.assertNotIn('PRIVATE', str(caught.exception))

    def test_bad_protocol_empty_and_invalid_urls_reach_fallback(self):
        responses = [lambda r: httpx.Response(200, text='not-json'), lambda r: exa(r, text='No results'),
                     lambda r: exa(r, result={'isError': True}), lambda r: httpx.Response(200, json={'id': 'wrong', 'result': {}}),
                     lambda r: exa(r, text='Title: bad\nURL: javascript:alert(1)')]
        for response in responses:
            with self.subTest(response=response):
                self.transport(lambda r: response(r) if r.url.host == 'mcp.exa.ai' else httpx.Response(200, text=RSS))
                result = service.search('query')
                self.assertEqual(result['provider'], 'bing-rss'); self.assertEqual(len(result['attempts']), 2)

    def test_mcp_business_rate_limit_under_http_success_is_cooled_and_falls_back(self):
        self.transport(lambda r: exa(r,result={'_meta':{'ai.exa/rateLimited':True},'content':[{'type':'text','text':'limit'}]}) if r.url.host=='mcp.exa.ai' else httpx.Response(200,text=RSS))
        result=service.search('query');second=service.search('query')
        self.assertEqual(result['attempts'][0]['category'],'rate_limit')
        self.assertEqual(second['attempts'][0]['category'],'cooldown')
        self.assertEqual(sum(r.url.host=='mcp.exa.ai' for r in self.requests),1)

    def test_unreadable_snippet_is_not_given_to_model_as_evidence(self):
        rows=service._normalize([dict(title='Python',link='https://python.org',snippet='\ufffd\ufffd\x00\x01bad')])
        self.assertEqual(rows[0]['snippet'],'')

    def test_private_provider_is_explicit_and_only_private_request_receives_key(self):
        model = dict(base_url='https://model.example/v1', api_key='MODEL_SECRET')
        self.assertEqual(service.settings()['provider'], 'exa'); service.save_settings({'provider': 'model-api'})
        self.transport(lambda r: httpx.Response(404) if r.url.host == 'model.example' else httpx.Response(200, text=RSS))
        with patch('desk_companion.model_catalog.require_active', return_value=model): result = service.search('query')
        self.assertEqual(self.requests[0].headers['authorization'], 'Bearer MODEL_SECRET')
        self.assertTrue(all('authorization' not in r.headers for r in self.requests[1:])); self.assertNotIn('MODEL_SECRET', str(result))

    def test_retry_budget_shared_with_fallback_and_cancel_stops_all_requests(self):
        self.transport(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout('read', request=r)))
        with self.assertRaises(service.SearchFailure) as caught: service.search('query', budget_s=1)
        self.assertEqual(len(self.requests), 2); self.assertEqual(len(caught.exception.attempts), 2)
        self.requests.clear()
        with self.assertRaises(service.SearchFailure) as caught: service.search('query', cancelled=lambda: True)
        self.assertEqual(caught.exception.category, 'cancelled'); self.assertFalse(self.requests)

    def test_total_deadline_checked_during_body_and_before_fallback(self):
        self.transport(lambda r: exa(r)); ticks = iter([0, 0, 0, 0, 31, 31, 31, 31, 31, 31])
        with patch.object(service.time, 'monotonic', side_effect=lambda: next(ticks, 31)):
            with self.assertRaises(service.SearchFailure): service.search('query')
        self.assertEqual(len(self.requests), 1)

    def test_size_limit_failed_fallback_reports_safe_diagnostics(self):
        self.transport(lambda r: httpx.Response(200, text='x' * (service.MAX_BODY + 1)) if r.url.host == 'mcp.exa.ai' else httpx.Response(503, text='PRIVATE'))
        result = service.probe('query')
        self.assertFalse(result['ok']); self.assertEqual(len(result['attempts']), 2)
        self.assertEqual(result['attempts'][0]['category'], 'response_size'); self.assertNotIn('PRIVATE', str(result))

    def test_query_validation_prevents_requests(self):
        self.transport(lambda r: exa(r))
        for value in ('', ' ' * 10, 'x' * 201, None, {}):
            with self.assertRaises(ValueError): service.search(value)
        self.assertFalse(self.requests)

    def test_atlas_tool_failure_recorded_without_credentials(self):
        from atlas.testing import FakeLLM
        from desk_companion import assistant
        from types import SimpleNamespace
        self.transport(lambda r: httpx.Response(503, text='PRIVATE_TOKEN'))
        cfg = dict(ATLAS_API_KEY='test-secret', ATLAS_BASE_URL='https://example.invalid/v1', ATLAS_MODEL='fake')
        host = SimpleNamespace(state=SimpleNamespace(persona='助手', max_steps=8), ui=lambda fn: fn(), on_llm_delta=lambda _: None, on_stream_status=lambda _: None)
        with patch.object(assistant, 'require_llm_env', return_value=cfg): agent = assistant.build_agent(host)
        agent.llm = FakeLLM([{'role': 'assistant', 'content': '', 'tool_calls': [{'id': 's1', 'type': 'function', 'function': {'name': 'web_search', 'arguments': '{"query":"苏州天气"}'}}]}, '未取得来源。'])
        agent.run('苏州天气', run_id='search-failure')
        tool = [e for e in agent.journal.get_events('search-failure') if e.event_type == 'tool_after_call'][-1]
        self.assertEqual(tool.payload['status'], 'failed'); self.assertNotIn('PRIVATE_TOKEN', str(tool.payload))


if __name__ == '__main__': unittest.main()
