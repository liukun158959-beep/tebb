import concurrent.futures
import os
import tempfile
import unittest
from unittest.mock import patch

from atlas.llm import IncompleteResponseError
from desk_companion import model_generation as generation


class GenerationBindings(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=temp.name);env.start();self.addCleanup(env.stop)
        item=patch('desk_companion.model_catalog.require_item',side_effect=lambda ident:dict(id=ident,model=ident,base_url='https://fixture.invalid/v1',api_key='fixture-only'))
        item.start();self.addCleanup(item.stop)

    def transport(self,wrong=False):
        from types import SimpleNamespace
        class Model:
            def __init__(self,*args,**kwargs):self.client=SimpleNamespace(base_url='https://fixture.invalid/v1')
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def chat(self,messages,tools=None,options=None):
                self_test.assertEqual(tools,[])
                cap=next(iter(options.extra_body.values()))
                raise IncompleteResponseError('length',dict(message=dict(content='partial'),usage=dict(prompt_tokens=10,completion_tokens=cap-1 if wrong else cap),duration_ms=1))
        self_test=self
        return Model

    def test_parallel_verified_models_do_not_lose_bindings_and_route_changes_invalidate(self):
        with patch('desk_companion.execution_context.ExecutionLLM',self.transport()),patch('desk_companion.model_catalog.bind_llm'):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:
                list(pool.map(lambda ident:generation.verify(dict(model_id=ident,parameter='max_completion_tokens'),lambda *_:None),['one','two']))
        for ident in('one','two'):
            self.assertTrue(generation.public(ident)['verified'])
            self.assertIsNone(generation.binding(ident,ident,'https://changed.invalid/v1/chat/completions'))
        self.assertNotIn('fixture-only',generation.path().read_bytes().decode('latin1'))

    def test_wrong_or_missing_truncation_usage_never_creates_a_verified_binding(self):
        with patch('desk_companion.execution_context.ExecutionLLM',self.transport(wrong=True)),patch('desk_companion.model_catalog.bind_llm'):
            with self.assertRaises(ValueError):generation.verify(dict(model_id='bad',parameter='max_tokens'),lambda *_:None)
        self.assertFalse(generation.public('bad')['verified'])
