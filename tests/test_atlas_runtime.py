"""显式验证固定 Atlas 源码与发布补丁组合，不构建完整 Windows 运行时。"""
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
import zipfile


VERIFY = r'''
import json
from pathlib import Path
import httpx
from openai import OpenAI
import atlas
from atlas import LLM

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
    llm = LLM(api_key='test-only', base_url='https://example.invalid/v1', model='fake')
    llm.client.close()
    with OpenAI(api_key='test-only', base_url='https://example.invalid/v1',
                http_client=transport, max_retries=0) as client:
        llm.client = client
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

        events[:] = [chunk(dict(content='半截回答'))]
        try:
            llm.chat([], on_delta=pieces.append)
        except RuntimeError as error:
            assert 'finish_reason' in str(error)
        else:
            raise AssertionError('Packaged Atlas accepted an unfinished stream')
        assert len(requests) == 2
print('Pinned Atlas + patch: sampling, reasoning and incomplete-stream rejection verified')
'''


@unittest.skipUnless(os.environ.get("DESK_TEST_ATLAS_SOURCE"),
                     "Set DESK_TEST_ATLAS_SOURCE to validate the pinned source + patch")
class AtlasRuntimeTests(unittest.TestCase):
    def test_pinned_source_and_patch_preserve_stream_contract(self):
        root = Path(__file__).resolve().parents[1]
        source = Path(os.environ["DESK_TEST_ATLAS_SOURCE"]).resolve()
        settings = runpy.run_path(str(root / "scripts/prepare_runtime.py"))
        archive = subprocess.check_output([
            "git", "-C", str(source), "archive", "--format=zip", settings["ATLAS_REF"], "atlas",
        ])
        with tempfile.TemporaryDirectory(prefix="desk-atlas-runtime-") as folder:
            workspace = Path(folder).resolve()
            self.assertEqual(workspace.parent, Path(tempfile.gettempdir()).resolve())
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                package.extractall(workspace)
            patch = subprocess.run([
                "git", "apply", "--unidiff-zero", str(root / "scripts/atlas-sampling.patch"),
            ], cwd=workspace, capture_output=True, text=True, timeout=30)
            self.assertEqual(patch.returncode, 0, patch.stderr)
            result = subprocess.run([
                sys.executable, "-c", VERIFY,
            ], cwd=workspace, env={**os.environ, "PYTHONPATH": str(workspace), "PYTHONUTF8": "1"},
                capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
