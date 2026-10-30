import tempfile
from pathlib import Path
import unittest

from desk_companion.project_environment_store import EnvironmentStore


class EnvironmentStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=EnvironmentStore(Path(self.tmp.name)/'environments.sqlite')

    def create(self,request='r1',ident='env1'):
        return self.store.begin(ident,'create',request,{'repo':'https://github.com/example/demo'},'task1',
                                initial=dict(id=ident,sandbox_id='',service=None,state='creating'))

    def test_replay_uses_original_environment_and_request_cannot_change_inputs(self):
        environment,operation,fresh=self.create();self.assertTrue(fresh)
        same,receipt,fresh=self.create(ident='unused-candidate')
        self.assertFalse(fresh);self.assertEqual(same['id'],environment['id']);self.assertEqual(receipt,operation)
        with self.assertRaisesRegex(ValueError,'不同项目操作'):
            self.store.begin('env1','create','r1',{'repo':'changed'},initial={'id':'env1'})
        self.assertEqual(len(self.store.list()),1)

    def test_unknown_receipt_blocks_new_work_without_repeating_create(self):
        self.create();self.store.finish('r1','unknown',error='SDK acknowledgement missing')
        with self.assertRaisesRegex(ValueError,'结果未知'):
            self.store.begin('env1','execute','r2',{'command':'npm install'})
        self.assertEqual(self.store.operation('r1')['state'],'unknown')

    def test_cancel_retains_late_runtime_identity_and_cannot_report_success(self):
        self.create();self.store.cancel('env1');self.store.bind_runtime('r1','sandbox-original')
        self.assertEqual(self.store.get('env1')['sandbox_id'],'sandbox-original')
        with self.assertRaisesRegex(RuntimeError,'取消后'):
            self.store.finish('r1','succeeded')
        with self.assertRaises(RuntimeError):self.store.checkpoint('r1','installed')
        self.store.finish('r1','cancelled',changes={'state':'stopped'})
        self.assertEqual(self.store.get('env1')['active_operation'],'')

    def test_runtime_identity_cannot_be_replaced_by_same_name(self):
        self.create();self.store.bind_runtime('r1','original')
        with self.assertRaisesRegex(ValueError,'身份发生变化'):
            self.store.bind_runtime('r1','replacement')
        self.assertEqual(self.store.get('env1')['sandbox_id'],'original')

    def test_failed_operation_keeps_error_and_ready_service_requires_stop(self):
        self.create();self.store.finish('r1','failed',error='dependency install failed',changes={'state':'failed'})
        self.assertEqual(self.store.get('env1')['error'],'dependency install failed')
        self.store.observe('env1',service={'state':'ready','url':'http://127.0.0.1:12345'})
        with self.assertRaisesRegex(ValueError,'先停止'):
            self.store.begin('env1','execute','r2',{'command':'npm install'})
        environment,receipt,fresh=self.store.begin('env1','read','r3',{'path':'README.md'})
        self.assertTrue(fresh)
        self.assertEqual(environment['service']['state'],'ready')

    def test_observation_cannot_overwrite_active_operation_or_late_final_result(self):
        self.create();self.store.observe('env1',state='ready')
        self.assertEqual(self.store.get('env1')['state'],'creating')
        self.store.finish('r1','failed',error='actual failure')
        self.store.finish('r1','succeeded',changes={'state':'ready'})
        self.assertEqual(self.store.operation('r1')['state'],'failed')
        self.assertEqual(self.store.get('env1')['error'],'actual failure')

    def test_service_observation_can_update_during_read_without_overwriting_operation(self):
        self.create();self.store.finish('r1','succeeded')
        self.store.observe('env1',service=dict(id='service-original',state='ready'))
        self.store.begin('env1','read','r2',dict(path='README.md'))
        self.store.observe_service('env1','service-original',dict(id='service-original',state='failed'),'server exited')
        environment=self.store.get('env1')
        self.assertEqual(environment['service']['state'],'failed');self.assertEqual(environment['active_operation'],'r2')
        self.store.observe_service('env1','old-replaced-service',dict(state='ready'),'')
        self.assertEqual(self.store.get('env1')['service']['state'],'failed')

    def test_two_occupied_environments_bound_new_admission_without_partial_record(self):
        self.create(ident='env1');self.create(request='r2',ident='env2')
        with self.assertRaisesRegex(ValueError,'已有两个'):
            self.create(request='r3',ident='env3')
        self.assertEqual(len(self.store.list()),2)


if __name__=='__main__':unittest.main()
