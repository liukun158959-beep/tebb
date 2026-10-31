import copy
import hashlib
import unittest
from desk_companion.agent_eval_projects import judge,receipts_bound_to_environment,final_test_receipt
from desk_companion.agent_evals import CASES,evaluate


class ProjectVerdictTests(unittest.TestCase):
    def test_model_must_test_after_last_write_and_last_test_cannot_be_a_failure(self):
        command='original-test';digest=hashlib.sha256(command.encode()).hexdigest()
        test=lambda code:dict(tool='project_execute',command_sha256=digest,ok=code==0,exit_code=code)
        write=dict(tool='project_write',ok=True)
        self.assertEqual(final_test_receipt([test(1),write,test(0)],command)['exit_code'],0)
        self.assertEqual(final_test_receipt([test(0),test(1)],command)['exit_code'],1)
        self.assertEqual(final_test_receipt([test(0),write],command),{})
        self.assertEqual(final_test_receipt([test(0)],'another-command'),{})

    def test_cannot_combine_success_receipts_from_different_environments(self):
        receipts=[dict(tool='project_start',ok=True,environment={'id':'original'}),
                  dict(tool='project_stop',ok=True,environment={'id':'original'})]
        self.assertTrue(receipts_bound_to_environment(receipts,'original'))
        mixed=copy.deepcopy(receipts);mixed[0]['environment']['id']='another'
        self.assertFalse(receipts_bound_to_environment(mixed,'original'))
        self.assertFalse(receipts_bound_to_environment([],'original'))
        receipts.append(dict(tool='project_read',ok=False,environment={'id':'invalid'}))
        self.assertTrue(receipts_bound_to_environment(receipts,'original'))

    def proof(self,scenario):
        names={'coding':['project_read','project_write','project_execute'],
               'clone':['project_prepare','project_read','project_start','project_status','project_log','project_stop'],
               'cancel':[]}[scenario]
        return dict(sdk_stopped=True,identity_matches=True,model_workers_exited=True,
                    model_receipts=[dict(tool=name,ok=True)for name in names],
                    original_tests_unchanged=True,implementation_changed=True,model_test_exit_code=0,verification_test_exit_code=0,
                    checkout_matches=True,tracked_files_unchanged=True,original_service_command=True,http_ready_before_stop=True,actual_log_bytes=42,
                    preview_unreachable=True,model_stop_same_environment=True,active_guest_marker=True,cancel_requested=True)

    def test_coding_needs_unchanged_original_tests_and_real_test_exit_in_both_phases(self):
        root=dict(state='succeeded');proof=self.proof('coding')
        self.assertTrue(judge('coding',root,proof))
        for key,value in [('original_tests_unchanged',False),('implementation_changed',False),('model_test_exit_code',1),('verification_test_exit_code',None)]:
            self.assertFalse(judge('coding',root,{**proof,key:value}),key)

    def test_oral_success_or_stop_receipt_cannot_replace_original_runtime_and_http_evidence(self):
        proof=self.proof('clone');self.assertTrue(judge('clone',dict(state='succeeded'),proof))
        for key,value in [('sdk_stopped',False),('identity_matches',False),('model_workers_exited',False),('checkout_matches',False),
                          ('tracked_files_unchanged',False),('http_ready_before_stop',False),('actual_log_bytes',0),
                          ('original_service_command',False),
                          ('model_stop_same_environment',False),('preview_unreachable',False)]:
            self.assertFalse(judge('clone',dict(state='succeeded'),{**proof,key:value}),key)
        changed=copy.deepcopy(proof);changed['model_receipts'][2]['ok']=False
        self.assertFalse(judge('clone',dict(state='succeeded'),changed))

    def test_cancel_needs_an_active_guest_and_worker_and_sdk_independently_stopped(self):
        proof=self.proof('cancel');self.assertTrue(judge('cancel',dict(state='cancelled'),proof))
        for key in ('active_guest_marker','cancel_requested','sdk_stopped','model_workers_exited'):
            self.assertFalse(judge('cancel',dict(state='cancelled'),{**proof,key:False}),key)
        self.assertFalse(judge('cancel',dict(state='succeeded'),proof))
        self.assertFalse(judge('cancel',dict(state='cancelled'),{**proof,'fallback_cleanup_used':True}))

    def test_project_verdict_does_not_apply_json_none_contract_and_keeps_expected_cancel_distinct(self):
        for scenario,state in [('coding','succeeded'),('clone','succeeded'),('cancel','cancelled')]:
            spec=next(value for value in CASES if value.get('scenario')==scenario and value.get('executor')=='project')
            self.assertEqual(evaluate(spec,dict(state=state,answer='实际结果见证据',events=[]),verification={'verified':True})[0],'success')
            self.assertEqual(evaluate(spec,dict(state=state,answer='已完成',events=[]),verification={'verified':False})[0],'false_success')
