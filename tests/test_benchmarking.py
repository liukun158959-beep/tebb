import copy
import json
from pathlib import Path
import tempfile
import unittest

from desk_companion.benchmarking import BenchmarkPlan,run_benchmark


class FakeManager:
    fail_wait=False
    shutdown_complete=True
    output=None
    def __init__(self,**kwargs):self.items={};self.settings={}
    def configure(self,*args):pass
    def submit(self,text,session,channel,**request):
        assert request.get('budget')==1,'benchmark must admit at most one model request'
        self.last_budget=request.get('budget');ident=f'{len(self.items):032x}'
        self.items[ident]=dict(id=ident,state='succeeded',created=1,started=1.1,ended=1.3,answer='hello',error='',events=[
            dict(kind='llm_start',data={},ts=1.12),dict(kind='token',data='hello',ts=1.2)],
            task_budget=dict(consumed=dict(model_calls=1,input_tokens=0,output_tokens=3,input_unknown_calls=1,output_unknown_calls=0)))
        return ident
    def wait(self,ident):
        snapshot=json.loads(self.output.read_text(encoding='utf-8'))
        assert any(row.get('task_id')==ident for row in snapshot['samples'])
        if self.fail_wait:raise RuntimeError('PRIVATE_PROVIDER_VALUE')
        return copy.deepcopy(self.items[ident])
    def get(self,ident):return copy.deepcopy(self.items[ident])
    def shutdown(self):return dict(complete=self.shutdown_complete,pending_task_ids=[])


class BenchmarkingTests(unittest.TestCase):
    def run_case(self,folder,manager):
        output=Path(folder)/'new.json';manager.output=output
        return run_benchmark(BenchmarkPlan(output=output,samples=2,parallel=1),
            dict(id='fixture',model='fixture',api_key='PRIVATE_MODEL_KEY',base_url='http://127.0.0.1:1/v1'),
            dict(mode='direct',proxy='PRIVATE_PROXY_KEY'),manager_factory=manager)

    def test_future_error_preserves_each_sample_and_credentials_are_removed(self):
        class Broken(FakeManager):fail_wait=True
        with tempfile.TemporaryDirectory()as folder:
            report=self.run_case(folder,Broken)
            self.assertEqual(len(report['samples']),2);self.assertTrue(all(row['state']=='recording_failed'for row in report['samples']))
            self.assertFalse(report['passed']);self.assertNotIn('PRIVATE_PROVIDER_VALUE',json.dumps(report))
            self.assertNotIn('PRIVATE_MODEL_KEY',json.dumps(report));self.assertNotIn('PRIVATE_PROXY_KEY',json.dumps(report))
            profile=Path(report['evidence_directory'])/'profile'
            self.assertFalse((profile/'models.json').exists());self.assertFalse((profile/'model-network.json').exists())
            self.assertTrue((Path(report['evidence_directory'])/'samples/0000.json').is_file())

    def test_startup_error_still_saves_all_unstarted_and_cleanup(self):
        def broken(**kwargs):raise OSError('PRIVATE_STARTUP_VALUE')
        with tempfile.TemporaryDirectory()as folder:
            report=self.run_case(folder,broken)
            self.assertEqual(report['run_state'],'failed');self.assertTrue(all(r['state']=='not_started'for r in report['samples']))
            self.assertEqual(report['summary']['completed'],0);self.assertIsNone(report['summary']['duration_ms']['p99'])
            self.assertTrue(report['cleanup']['model_credential_removed']);self.assertNotIn('PRIVATE_STARTUP_VALUE',json.dumps(report))

    def test_unknown_usage_and_incomplete_shutdown_cannot_become_success(self):
        class Unsettled(FakeManager):shutdown_complete=False
        with tempfile.TemporaryDirectory()as folder:
            report=self.run_case(folder,Unsettled)
            self.assertFalse(report['passed']);self.assertFalse(report['checks']['cleanup'])
            self.assertTrue(all(not row['usage_complete']for row in report['samples']))
            self.assertIsNone(report['summary']['first_text_ms']['p99'])
            self.assertTrue(all(row['usage']['input_unknown_calls']==1 for row in report['samples']))

    def test_future_base_exception_cannot_bypass_credential_cleanup(self):
        class Exits(FakeManager):
            def wait(self,ident):raise SystemExit('PRIVATE_FUTURE_EXIT')
        with tempfile.TemporaryDirectory()as folder:
            report=self.run_case(folder,Exits)
            self.assertTrue(report['cleanup']['model_credential_removed']);self.assertFalse(report['passed'])
            self.assertNotIn('PRIVATE_FUTURE_EXIT',json.dumps(report))

    def test_interrupt_during_startup_preserves_unstarted_and_cleans_keys(self):
        def interrupted(**kwargs):raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory()as folder:
            report=self.run_case(folder,interrupted)
            self.assertEqual(report['run_state'],'interrupted');self.assertTrue(report['cleanup']['model_credential_removed'])
            self.assertEqual(report['summary']['completed'],0)

    def test_interrupted_sample_stops_remaining_admission_without_replay(self):
        class Interrupts(FakeManager):
            def wait(self,ident):raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory()as folder:
            report=self.run_case(folder,Interrupts)
            self.assertEqual(report['run_state'],'interrupted');self.assertEqual(report['samples'][0]['state'],'interrupted')
            self.assertEqual(report['samples'][1]['state'],'not_started');self.assertTrue(report['cleanup']['model_credential_removed'])

    def test_old_output_is_never_replaced_or_resumed(self):
        with tempfile.TemporaryDirectory()as folder:
            report=self.run_case(folder,FakeManager);path=Path(folder)/'new.json';before=path.read_bytes()
            with self.assertRaises(FileExistsError):self.run_case(folder,FakeManager)
            self.assertEqual(path.read_bytes(),before)

    def test_frozen_source_change_disqualifies_the_cohort(self):
        class Altered(FakeManager):
            def wait(self,ident):
                report=json.loads(self.output.read_text(encoding='utf-8'))
                path=Path(report['source_snapshot']['directory'])/'desk_companion/benchmarking.py'
                with path.open('ab')as stream:stream.write(b'\n# altered fixture\n')
                return super().wait(ident)
        with tempfile.TemporaryDirectory()as folder:
            report=self.run_case(folder,Altered)
            self.assertFalse(report['source_unchanged']);self.assertFalse(report['checks']['frozen_source'])
            self.assertFalse(report['passed'])


if __name__=='__main__':unittest.main()
