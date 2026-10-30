import io
import json
from contextlib import redirect_stderr
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import ci_checks as checks
from scripts import draft_release
import zipfile


class CIChecksTests(unittest.TestCase):
    def test_version_and_pinned_ref_are_consistent(self):
        checks.check_version()
        self.assertRegex(checks.atlas_ref(),r'^[a-f0-9]{40}$')

    def test_runner_records_failure_and_skip_without_retry(self):
        class Cases(unittest.TestCase):
            def test_ok(self):pass
            @unittest.skip('explicit platform condition')
            def test_skip(self):pass
            def test_failure(self):self.fail('fixture failure')
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,DESK_TEST_ATLAS_SOURCE='fixture'),patch.object(checks.unittest.defaultTestLoader,'discover',return_value=unittest.defaultTestLoader.loadTestsFromTestCase(Cases)):
            with redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit):checks.run_tests(Path(directory))
            self.assertIn('test_failure',errors.getvalue())
            self.assertIn('fixture failure',errors.getvalue())
            report=json.loads((Path(directory)/'backend.json').read_text('utf-8'))
            self.assertEqual(report['tests'],3);self.assertEqual(report['attempts'],1)
            self.assertEqual(len(report['failures']),1);self.assertEqual(len(report['skipped']),1)
            self.assertFalse(report['successful']);self.assertEqual(len(report['samples']),3)

    def test_gate_rejects_latest_failed_attempt_even_after_success(self):
        runs=dict(workflow_runs=[dict(id=1,created_at='2026-10-29',run_attempt=1,status='completed',conclusion='success'),
                                 dict(id=2,created_at='2026-10-30',run_attempt=1,status='completed',conclusion='failure',html_url='https://example.invalid/run')])
        with patch.dict(os.environ,GITHUB_REPOSITORY='owner/repo'),patch.object(checks.subprocess,'run'),patch.object(checks.subprocess,'check_output',return_value=json.dumps(runs)):
            with self.assertRaisesRegex(RuntimeError,'最新 CI'):checks.check_gate('a'*40)

    def test_gate_rejects_skipped_backend_and_invalid_sha(self):
        with self.assertRaises(ValueError):checks.check_gate('main; unsafe')
        runs=dict(workflow_runs=[dict(id=1,created_at='2026-10-30',run_attempt=1,status='completed',conclusion='success')])
        with patch.dict(os.environ,GITHUB_REPOSITORY='owner/repo'),patch.object(checks.subprocess,'run'),patch.object(checks.subprocess,'check_output',side_effect=[json.dumps(runs),json.dumps(dict(jobs=[dict(name='Frontend',conclusion='success'),dict(name='Rust',conclusion='success'),dict(name='Python / Atlas',conclusion='skipped')]))]):
            with self.assertRaisesRegex(RuntimeError,'必要 job'):checks.check_gate('a'*40)

    def test_draft_never_overwrites_existing_version_or_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ui=root/'desk_companion/ui';ui.mkdir(parents=True)
            (ui/'release.json').write_text(json.dumps(dict(version='9.0.0',tag='v9.0.0',title='fixture',image='poster.png')))
            notes=root/'docs/releases';notes.mkdir(parents=True);(notes/'v9.0.0.md').write_text('fixture')
            archive=root/'tebb-9.0.0-windows-x64.zip';sha='a'*40
            with zipfile.ZipFile(archive,'w') as package:package.writestr('tebb-9.0.0/build-info.json',json.dumps(dict(version='9.0.0',desk_commit=sha)))
            import hashlib
            (root/'SHA256SUMS.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
            with patch.object(draft_release,'ROOT',root),patch.object(draft_release.sys,'argv',['draft_release.py',str(root)]),patch.dict(os.environ,CANDIDATE_SHA=sha,GITHUB_REPOSITORY='owner/repo'),patch.object(draft_release.subprocess,'run') as run:
                with patch.object(draft_release.subprocess,'check_output',side_effect=[json.dumps([dict(tag_name='v9.0.0')]),'[]']):
                    with self.assertRaisesRegex(RuntimeError,'不覆盖'):draft_release.main()
                run.assert_not_called()
                with patch.object(draft_release.subprocess,'check_output',side_effect=['[]','[]']):draft_release.main()
                command=run.call_args.args[0];self.assertIn('--draft',command);self.assertNotIn('--latest',command)
