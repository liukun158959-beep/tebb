import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import httpx

from desk_companion import agent_debug as debug,context_inspector as inspector,model_capacity


class ContextInspectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=self.temp.name);self.env.start();self.addCleanup(self.env.stop)
        self.model=dict(id='m1',model='configured-model',base_url='https://api.example.org/v1',api_key='PRIVATE_KEY')
        p=patch('desk_companion.model_catalog.require_item',return_value=self.model);p.start();self.addCleanup(p.stop)
        self.collab=Mock();self.collab.snapshot.return_value=dict(state=dict(conversations=[dict(id='one',members=['tebb']),dict(id='two',members=['tebb'])],
            agents=[dict(id='tebb',name='tebb',memory='我的伙伴偏好')],runs=[dict(id='root1',conversation='one',agent='tebb',model_id='m1'),
            dict(id='child1',conversation='one',agent='research',parent='root1',model_id='m1'),dict(id='root2',conversation='two',agent='tebb',model_id='m1')]))

    def call(self,run='root1',usage=100,phase='route',tail=''):
        payload=dict(model='configured-model',messages=[dict(role='user',content='你好'+tail)],tools=[dict(type='function',function=dict(name='safe'))],temperature=.7)
        request=httpx.Request('POST','https://api.example.org/v1/chat/completions?credential=QUERY_SECRET',json=payload,
                             headers=dict(Authorization='Bearer PRIVATE_KEY'))
        with debug.context(session='collab-'+run,model_id='m1',collab_run=run,phase=phase):ident=debug.begin(request,payload)
        response={'usage':{'prompt_tokens':usage}} if usage is not None else {}
        (debug.root()/(ident+'.response.txt')).write_text(json.dumps(response),'utf-8')
        debug.update(ident,state='succeeded');return ident

    def test_conversation_selection_includes_children_and_never_reads_other_call(self):
        root=self.call(tail='末尾保留'*4000);child=self.call('child1');foreign=self.call('root2')
        result=inspector.inspect(self.collab,'one')
        self.assertEqual(result['selected'],root)
        self.assertEqual({r['id'] for r in result['items']},{root,child})
        self.assertIn('末尾保留'*4000,result['detail']['request_raw'])
        self.assertNotIn('PRIVATE_KEY',json.dumps(result));self.assertNotIn('QUERY_SECRET',json.dumps(result))
        self.assertEqual(next(r for r in result['items'] if r['id']==root)['phase'],'route')
        self.assertEqual(inspector.inspect(self.collab,'one',child)['selected'],child)
        with self.assertRaises(ValueError):inspector.inspect(self.collab,'one',foreign)
        with self.assertRaises(ValueError):inspector.inspect(self.collab,'missing')

    def test_unknown_window_and_missing_tokenizer_never_guess_tokens(self):
        ident=self.call(usage=None)
        result=inspector.inspect(self.collab,'one',ident,draft='新的问题')
        self.assertIsNone(result['budget']['window']);self.assertIsNone(result['budget']['remaining'])
        self.assertEqual(result['budget']['input_source'],'unavailable')
        self.assertIsNone(result['budget']['input_tokens'])
        self.assertIsNone(result['budget']['draft_reference']['tokens'])

    def test_capacity_snapshot_remains_bound_to_model_endpoint_and_call(self):
        model_capacity.save('m1',10000,2000)
        ident=self.call(usage=3000)
        model_capacity.save('m1',20000,1000)
        result=inspector.inspect(self.collab,'one',ident);budget=result['budget']
        self.assertEqual(result['configuration']['window'],20000)
        self.assertEqual((budget['window'],budget['remaining'],budget['input_source']),(10000,5000,'provider'))
        self.assertEqual(budget['capacity_source'],'captured_config')
        self.assertIsNone(model_capacity.for_call('m1','different-model','https://api.example.org/v1/chat/completions')['window'])
        self.assertIsNone(model_capacity.for_call('m1','configured-model','https://different.org/v1/chat/completions')['window'])

    def test_late_configuration_and_legacy_metadata_remain_honest(self):
        ident=self.call(usage=9500)
        (debug.root()/(ident+'.meta.json')).unlink()
        model_capacity.save('m1',10000,1000)
        result=inspector.inspect(self.collab,'one',ident)
        self.assertEqual(result['items'][0]['phase'],'历史阶段未标注')
        self.assertEqual(result['budget']['over_by'],500);self.assertEqual(result['budget']['remaining'],0)
        self.assertEqual(result['budget']['capacity_source'],'user_config')
        for window,reserve in [(True,0),(1000,0),(10000,10000),(None,1),(10000,1.5)]:
            with self.subTest(window=window,reserve=reserve),self.assertRaises(ValueError):model_capacity.save('m1',window,reserve)

    def test_no_recording_no_fake_request_and_full_body_can_contain_user_secret(self):
        debug.configure(False)
        result=inspector.inspect(self.collab,'one');self.assertFalse(result['recording']);self.assertIsNone(result['detail'])
        ident=self.call(tail='用户在正文自写SECRET')
        self.assertIn('用户在正文自写SECRET',inspector.inspect(self.collab,'one',ident)['detail']['request_raw'])
