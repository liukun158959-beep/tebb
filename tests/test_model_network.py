import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

from desk_companion import model_network as network


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.env=patch.dict(os.environ,{'DESK_COMPANION_DATA_DIR':self.folder.name});self.env.start();self.addCleanup(self.env.stop)

    def test_save_modes_and_auth_redaction_and_validation(self):
        self.assertEqual(network.public()['mode'],'system')
        value=network.save(dict(mode='custom',proxy_url='http://localhost:7890',username='test',password='PRIVATE_PROXY_SECRET'))
        self.assertNotIn('PRIVATE_PROXY_SECRET',json.dumps(value));self.assertTrue(value['has_password'])
        network.save(dict(mode='direct',password=''))
        self.assertEqual(network.load()['password'],'PRIVATE_PROXY_SECRET')
        network.save(dict(mode='custom',clear_password=True));self.assertFalse(network.public()['has_password'])
        for url in ('http://user:secret@localhost:7890','https://localhost:bad','file:///x','http://localhost:2/path','http://localhost:2?q=x','http://local host:3'):
            with self.subTest(url=url),self.assertRaises(ValueError):network.save(dict(mode='custom',proxy_url=url))
        self.assertEqual(network.public()['proxy_url'],'http://localhost:7890')
        network.save(dict(mode='custom',password='OLD_PROXY_PASSWORD'))
        network.save(dict(mode='custom',proxy_url='http://localhost:7891'))
        self.assertFalse(network.public()['has_password'])

    def test_direct_bypasses_environment_and_custom_proxy_is_really_used(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                seen.append((self.path,self.headers.get('Proxy-Authorization')))
                self.send_response(200);self.end_headers();self.wfile.write(b'ok')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        env={'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':''}
        with patch.dict(os.environ,env):
            network.save(dict(mode='direct'))
            with network.http_client(2) as client:self.assertEqual(client.get(base+'/direct').text,'ok')
            network.save(dict(mode='custom',proxy_url=base,username='proxy-user',password='private-password'))
            with network.http_client(2) as client:self.assertEqual(client.get('http://example.invalid/custom').text,'ok')
        self.assertEqual(seen[0],('/direct',None))
        self.assertEqual(seen[1][0],'http://example.invalid/custom');self.assertTrue(seen[1][1].startswith('Basic '))
        self.assertNotIn('private-password',json.dumps(network.public()))

    def test_tls_verification_and_ca_environment_remain_enabled_in_direct_mode(self):
        import httpx
        real=httpx.Client;seen=[]
        def create(**kw):seen.append(kw);return real(transport=httpx.MockTransport(lambda _:httpx.Response(200)))
        with patch('httpx.Client',side_effect=create), patch('ssl.create_default_context') as ssl:
            with patch.dict(os.environ,{'SSL_CERT_FILE':'configured-ca','SSL_CERT_DIR':'configured-ca-dir'}):
                with network.http_client(2,dict(mode='direct',proxy_url='',username='',password='')):pass
            ssl.assert_called_once_with(cafile='configured-ca',capath='configured-ca-dir')
        self.assertFalse(seen[0]['trust_env']);self.assertIs(seen[0]['verify'],ssl.return_value)

    def test_sdk_and_collaboration_keep_transport_timeouts(self):
        import httpx
        from desk_companion.collaboration_llm import CollaborationLLM
        for collaboration in (False, True):
            with self.subTest(collaboration=collaboration):
                requests=[]
                def handle(request):
                    requests.append(request)
                    if collaboration:
                        return httpx.Response(200, text='data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n')
                    return httpx.Response(200, json=dict(choices=[dict(index=0,finish_reason='stop',message=dict(role='assistant',content='ok'))]))
                transport=httpx.Client(timeout=httpx.Timeout(37,connect=5),transport=httpx.MockTransport(handle))
                model=dict(api_key='fixture',base_url='https://example.invalid/v1',model='fixture')
                with patch.object(network,'http_client',return_value=transport):
                    if collaboration:
                        with CollaborationLLM(model,dict(temperature=.5,top_p=1,reasoning_effort='low'),37) as llm:
                            llm.chat([])
                    else:
                        with network.openai_client('fixture','https://example.invalid/v1',37) as sdk:
                            sdk.chat.completions.create(model='fixture',messages=[])
                self.assertEqual(requests[0].extensions['timeout']['connect'],5)
                self.assertEqual(requests[0].extensions['timeout']['read'],37)
                self.assertTrue(transport.is_closed)


if __name__=='__main__':unittest.main()
