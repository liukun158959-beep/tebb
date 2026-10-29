import json
import socket
import time
import unittest
from unittest.mock import patch

import httpx

from desk_companion import public_dns as dns, web_sources as sources
from tests.test_web_sources import Response, REAL_OPEN


class PublicDNSTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.responses = []
        def transport(request):
            self.requests.append(request)
            return self.responses.pop(0)
        self.addCleanup(patch.stopall)
        patch.object(dns.model_network, 'http_client', side_effect=lambda *a, **k: httpx.Client(
            transport=httpx.MockTransport(transport))).start()
        patch('desk_companion.logutil.log').start()
        self.local = patch.object(sources.socket, 'getaddrinfo', return_value=[(
            socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.18.1.27', 443))]).start()
        self.open = patch.object(sources, '_open', return_value=Response('<main>'+'Real article body. '*20+'</main>')).start()

    def response(self, addresses=('93.184.216.34',), **changes):
        value = dict(Status=0, TC=False, Question=[dict(name='example.org', type=1)],
                     Answer=[dict(name='example.org', type=1, data=a) for a in addresses])
        value.update(changes)
        return httpx.Response(200, json=value)

    def test_fake_ip_is_replaced_not_whitelisted_and_page_secrets_never_reach_dns(self):
        self.responses = [self.response()]
        page = sources.fetch_page('https://example.org/article?token=PAGE_SECRET#part')
        self.assertEqual(page['level'], 'body')
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url).split('?')[0], dns.ENDPOINT)
        self.assertEqual(dict(request.url.params), dict(name='example.org', type='1'))
        self.assertNotIn('PAGE_SECRET', str(request.url))
        self.assertNotIn('authorization', request.headers)
        self.assertEqual(self.open.call_args.args[0]._tebb_addresses, ['93.184.216.34'])
        self.assertEqual(self.local.call_count, 1)  # no second DNS lookup before connecting

    def test_private_mixed_local_and_fake_ip_literals_never_use_resolver(self):
        for host, addresses in [('example.org', ['127.0.0.1']), ('example.org', ['198.18.1.1', '10.0.0.1']),
                                ('198.18.1.27', ['198.18.1.27']), ('example.lan', ['198.18.1.27']),
                                ('example.org', ['::ffff:127.0.0.1']), ('2130706433', ['198.18.1.27'])]:
            with self.subTest(host=host):
                self.local.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, 443)) for a in addresses]
                with self.assertRaises(ValueError):
                    sources.fetch_page('https://'+host+'/')
        self.assertFalse(self.requests)
        self.open.assert_not_called()

    def test_rejects_bad_dns_answers_without_connecting_to_page(self):
        cases = [self.response(['127.0.0.1']), self.response(['93.184.216.34', '10.0.0.1']),
                 self.response(['198.18.1.99']), self.response(['::1']), self.response(TC=True),
                 self.response(Status=3), self.response(Question=[dict(name='other.org', type=1)]),
                 self.response(Answer='bad'), httpx.Response(302, headers={'location':'http://127.0.0.1/'})]
        for response in cases:
            with self.subTest(response=response.content[:100]):
                self.responses = [response]
                with self.assertRaises(ValueError): sources.fetch_page('https://example.org/')
        self.open.assert_not_called()

    def test_cname_chain_matches_question_and_unrelated_records_are_not_connected(self):
        self.responses = [self.response(Answer=[dict(name='example.org', type=5, data='cdn.example.net.'),
            dict(name='cdn.example.net', type=1, data='93.184.216.34'),
            dict(name='unrelated.org', type=1, data='8.8.8.8')])]
        self.assertEqual(dns.resolve('example.org', time.monotonic()+5), ['93.184.216.34'])
        self.responses = [self.response(Answer=[dict(name='unrelated.org', type=1, data='8.8.8.8')]),
                          httpx.Response(200, json=dict(Status=0, Question=[dict(name='example.org',type=28)], Answer=[]))]
        with self.assertRaises(ValueError): dns.resolve('example.org', time.monotonic()+5)

    def test_ipv6_only_dns_and_shared_deadline(self):
        self.responses = [self.response([]), httpx.Response(200, json=dict(Status=0,
            Question=[dict(name='example.org.',type=28)], Answer=[dict(name='example.org',type=28,data='2606:4700:4700::1111')]))]
        self.assertEqual(dns.resolve('example.org',time.monotonic()+5), ['2606:4700:4700::1111'])
        count = len(self.requests)
        with self.assertRaises(TimeoutError): dns.resolve('example.org',time.monotonic()-1)
        self.assertEqual(len(self.requests), count)

    def test_redirect_destination_still_checked_after_successful_fake_ip_resolution(self):
        import io
        import urllib.error
        from email.message import Message
        self.responses = [self.response()]
        headers = Message(); headers['Location']='http://127.0.0.1/private'
        self.open.side_effect=urllib.error.HTTPError('https://example.org/',302,'Found',headers,io.BytesIO())
        self.local.side_effect=lambda host,*a,**k: [(socket.AF_INET,socket.SOCK_STREAM,6,'',(
            '127.0.0.1' if host=='127.0.0.1' else '198.18.1.27',443))]
        with self.assertRaises(ValueError): sources.fetch_page('https://example.org/')
        self.assertEqual(self.open.call_count,1)
        self.assertEqual(len(self.requests),1)

    def test_transport_failures_fail_closed_with_safe_diagnostic(self):
        with patch.object(dns.model_network,'http_client',side_effect=httpx.ConnectError('credential SECRET')):
            with self.assertRaisesRegex(ValueError,'HTTPS 公网解析失败') as error:
                sources.fetch_page('https://example.org/')
        self.assertNotIn('SECRET',str(error.exception))
        self.open.assert_not_called()

    def test_real_connection_pins_resolved_public_ip_and_checks_original_tls_name(self):
        from types import SimpleNamespace
        self.responses = [self.response()]
        with patch.object(sources.socket,'create_connection') as tcp, patch.object(sources.ssl,'create_default_context') as tls, \
             patch.object(sources.urllib.request,'getproxies',return_value={}), \
             patch.object(sources.http.client.HTTPConnection,'request',lambda conn,*a,**k:conn.connect()), \
             patch.object(sources.http.client.HTTPConnection,'getresponse',return_value=SimpleNamespace(status=200,close=lambda:None)):
            with REAL_OPEN(sources.urllib.request.Request('https://example.org/doc'),5): pass
            self.assertEqual(tcp.call_args.args[0],('93.184.216.34',443))
            self.assertEqual(tls.return_value.wrap_socket.call_args.kwargs['server_hostname'],'example.org')


if __name__=='__main__': unittest.main()
