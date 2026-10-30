"""验证实际发布归档与产品适配器共用契约，不构建完整 Windows 运行时。"""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest


VERIFY = r'''
import json
import unittest
from pathlib import Path
import httpx
from openai import OpenAI
import atlas
from atlas import LLM
from atlas.llm import ChatOptions, IncompleteResponseError
from atlas.testing.llm_contract import StreamContract
from desk_companion.collaboration_llm import CollaborationLLM

assert Path(atlas.__file__).resolve().is_relative_to(Path.cwd())
requests = []
events = []
def handle(request):
    requests.append(json.loads(request.content))
    body = ''.join('data: ' + json.dumps(row, ensure_ascii=False) + '\n\n' for row in events)
    return httpx.Response(200, text=body, headers={'Content-Type': 'text/event-stream'})

def chunk(delta, finish=None):
    return dict(id='test', object='chat.completion.chunk', created=0, model='fake',
                choices=[dict(index=0, delta=delta, finish_reason=finish)])

with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
    with OpenAI(api_key='test-only', base_url='https://example.invalid/v1',
                http_client=transport, max_retries=0) as client:
        llm = LLM(model='fake', client=client)
        llm.sampling = dict(temperature=0.3, top_p=0.8, reasoning_effort='low')
        thoughts, pieces = [], []
        llm.on_reasoning = thoughts.append
        events[:] = [chunk(dict(reasoning_content='思考片段')),
                     chunk(dict(content='完整回答'), 'stop')]
        result = llm.chat([], on_delta=pieces.append)
        assert result['message']['content'] == '完整回答'
        assert pieces == ['完整回答'] and thoughts == ['思考片段']
        assert requests[0]['temperature'] == 0.3 and requests[0]['top_p'] == 0.8
        assert requests[0]['thinking'] == {'type': 'enabled'}
        assert requests[0]['reasoning_effort'] == 'low'
        assert 'reasoning_content' not in result['message']

        for finish in (None, 'length', 'content_filter'):
            events[:] = [chunk(dict(content='半截回答'), finish)]
            try:
                llm.chat([], on_delta=pieces.append)
            except IncompleteResponseError as error:
                assert error.finish_reason == finish
                assert error.partial['message']['content'] == '半截回答'
            else:
                raise AssertionError('Packaged Atlas accepted an unfinished stream')
        assert len(requests) == 4

class PackagedAtlasContract(StreamContract, unittest.TestCase):
    def make_model(self, transport):
        return LLM(model='fixture', client=OpenAI(api_key='fixture', http_client=transport, max_retries=0))
    def call_model(self, model, on_delta=None, should_cancel=None):
        return model.chat([], on_delta=on_delta, options=ChatOptions(stream=True, should_cancel=should_cancel))

class PackagedCollaborationContract(StreamContract, unittest.TestCase):
    def make_model(self, transport):
        return CollaborationLLM(dict(model='fixture', api_key='fixture', base_url='https://example.invalid/v1'),
                                dict(temperature=.5, top_p=1, reasoning_effort='low'), 20, transport)
    def call_model(self, model, on_delta=None, should_cancel=None):
        return model.chat([], on_delta=on_delta, should_cancel=should_cancel)

suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                           for cls in (PackagedAtlasContract, PackagedCollaborationContract))
assert unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()
print('Pinned Atlas: sampling, reasoning and both adapters verified without patches')
'''


@unittest.skipUnless(os.environ.get("DESK_TEST_ATLAS_SOURCE"),
                     "Set DESK_TEST_ATLAS_SOURCE to validate the pinned source and adapters")
class AtlasRuntimeTests(unittest.TestCase):
    def test_pinned_source_and_adapters_preserve_stream_contract(self):
        root = Path(__file__).resolve().parents[1]
        source = Path(os.environ["DESK_TEST_ATLAS_SOURCE"]).resolve()
        settings = runpy.run_path(str(root / "scripts/prepare_runtime.py"))
        with tempfile.TemporaryDirectory(prefix="desk-atlas-runtime-") as folder:
            workspace = Path(folder).resolve()
            self.assertEqual(workspace.parent, Path(tempfile.gettempdir()).resolve())
            commit = settings['export_atlas'](source, workspace)
            self.assertEqual(commit, settings['ATLAS_REF'])
            self.assertIn('liukun158959-beep', (workspace/'atlas/licenses/Atlas-MIT.txt').read_text('utf-8'))
            self.assertIn('Copyright (c) OpenAI', (workspace/'atlas/licenses/HumanEval-MIT.txt').read_text('utf-8'))
            result = subprocess.run([
                sys.executable, "-c", VERIFY,
            ], cwd=workspace, env={**os.environ, "PYTHONPATH": os.pathsep.join((str(workspace), str(root))), "PYTHONUTF8": "1"},
                capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
