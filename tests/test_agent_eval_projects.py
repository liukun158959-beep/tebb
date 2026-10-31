import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from desk_companion.agent_eval_projects import judge,receipts_bound_to_environment,final_test_receipt,active_tool_marker,original_cancel_command,HOLD_COMMAND,execute
from desk_companion.agent_evals import CASES,evaluate


class ProjectVerdictTests(unittest.TestCase):
    def test_setup_failure_waits_for_parent_cleanup_without_calling_unserved_http(self):
        with TemporaryDirectory()as directory:
            folder=None;parent=None
            class Parent:
                def __init__(self,command,**kwargs):
                    nonlocal folder,parent
                    parent=self;folder=Path(command[command.index('--directory')+1]);self.returncode=None;self.waited=False
                    (folder/'setup-error.json').write_text(json.dumps(dict(error='fixture create EOF')),'utf-8')
                def poll(self):return self.returncode
                def wait(self,**kwargs):
                    self.waited=True;self.returncode=1
                    (folder/'cleanup.json').write_text(json.dumps(dict(sdk_stopped=True)),'utf-8')
                    return 1
            class Tree:
                def __init__(self,process):self.process=process
                def close(self):
                    if self.process.poll()is None:self.process.returncode=-9
            with patch('desk_companion.agent_eval_projects.subprocess.Popen',Parent),patch('desk_companion.task_process.ProcessTree',Tree),patch('desk_companion.agent_eval_projects.http.client.HTTPConnection',side_effect=AssertionError('parent has not served HTTP'))as connection:
                with self.assertRaisesRegex(RuntimeError,'fixture create EOF'):
                    execute(dict(id='setup-failure',scenario='cancel'),profile=directory,model_id='fake',timeout=20)
            proof=json.loads((folder/'result.json').read_text('utf-8'))
            self.assertTrue(parent.waited);connection.assert_not_called()
            self.assertEqual(proof['parent_exit_code'],1)
            self.assertNotIn('forced_parent_stop',proof)
            self.assertTrue(proof['cleanup']['sdk_stopped'])
            self.assertFalse(proof['verified'])

    def test_command_echo_is_not_an_active_guest_marker(self):
        self.assertTrue(active_tool_marker(b'guest output\r\ntebb-eval-tool-active\r\n'))
        self.assertFalse(active_tool_marker(b'python -c \'print("tebb-eval-tool-active")\''))
        self.assertFalse(active_tool_marker(b'not-tebb-eval-tool-active'))

    def test_cancel_command_must_be_the_last_execute_of_the_actual_operation_owner(self):
        event=lambda command:dict(kind='tool_start',data=dict(tool='project_execute',query=command))
        tasks=[dict(id='other',events=[event(HOLD_COMMAND)]),dict(id='owner',events=[event('echo marker')])]
        self.assertFalse(original_cancel_command(tasks,'owner'))
        self.assertFalse(original_cancel_command(tasks,'missing'))
        tasks[1]['events'].append(event(HOLD_COMMAND))
        self.assertTrue(original_cancel_command(tasks,'owner'))
        tasks[1]['events'].append(event('echo marker'))
        self.assertFalse(original_cancel_command(tasks,'owner'))

    def test_cleanup_cancellation_cannot_replace_the_goal_needs_input_state(self):
        # Reproduce v8: a route asks for clarification, then teardown cancels it.
        # A fake transport/process isolates this evaluator bookkeeping defect.
        with TemporaryDirectory()as directory:
            folder=None;root=dict(id='root',state='needs_input',answer='需要澄清',error='');published=[]
            def write(name,value):
                (folder/name).write_text(json.dumps(value),'utf-8')
            class Parent:
                def __init__(self,command,**kwargs):
                    nonlocal folder
                    folder=Path(command[command.index('--directory')+1]);self.returncode=None
                    write('ready.json',dict(environment='env'))
                    write('progress.json',dict(runs=[root],tasks=[]))
                    write('verification.json',dict(sdk_stopped=False,identity_matches=True))
                    write('cleanup.json',dict(sdk_stopped=True))
                def poll(self):return self.returncode
                def wait(self,**kwargs):self.returncode=0;return 0
            class Tree:
                def __init__(self,parent):pass
                def close(self):pass
            class Connection:
                def __init__(self,*args,**kwargs):pass
                def request(self,*args,**kwargs):pass
                def getresponse(self):return self
                def read(self):return b''
                def close(self):pass
            class WS:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def send(self,text):
                    message=json.loads(text);method=message['method'];action=message['args'].get('action',{})
                    if method=='load_collaboration':result=dict(state=dict(agents=[dict(id='research')],runs=[]))
                    elif action.get('type')=='send':result=dict(state=dict(runs=[copy.deepcopy(root)]))
                    else:
                        result=dict(ok=True)
                        if action.get('type')=='cancel':
                            root['state']='cancelled';write('progress.json',dict(runs=[root],tasks=[]))
                    self.response=json.dumps(dict(result=dict(ok=True,result=result)))
                def recv(self,**kwargs):return self.response
            with patch('desk_companion.agent_eval_projects.subprocess.Popen',Parent),patch('desk_companion.task_process.ProcessTree',Tree),patch('websockets.sync.client.connect',lambda *a,**k:WS()),patch('desk_companion.agent_eval_projects.http.client.HTTPConnection',Connection):
                result,proof,_,_=execute(dict(id='cancel-test',scenario='cancel'),profile=directory,model_id='fake',timeout=20,progress=published.append)
            self.assertEqual(result['state'],'needs_input')
            self.assertEqual(proof['goal_state_before_cleanup'],'needs_input')
            self.assertEqual(proof['cleanup_root_state'],'cancelled')
            self.assertFalse(proof['verified']);self.assertEqual(proof['parent_exit_code'],0)
            self.assertEqual(published[0]['project_case']['stage'],'parent_setup')
            self.assertTrue(any('start_background_task'in value.get('input','')for value in published))

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
               'clone':['project_prepare','project_read','project_start','project_status','project_log','project_integrity','project_stop'],
               'cancel':[]}[scenario]
        return dict(sdk_stopped=True,identity_matches=True,model_workers_exited=True,
                    model_receipts=[dict(tool=name,ok=True)for name in names],
                    original_tests_unchanged=True,implementation_changed=True,model_test_exit_code=0,verification_test_exit_code=0,
                    model_source_integrity=True,stopped_vm_not_resumed_for_inspection=True,checkout_matches=True,tracked_files_unchanged=True,original_service_command=True,http_ready_before_stop=True,actual_log_bytes=42,
                    preview_unreachable=True,model_stop_same_environment=True,active_guest_marker=True,cancel_requested=True,original_tool_command=True)

    def test_coding_needs_unchanged_original_tests_and_real_test_exit_in_both_phases(self):
        root=dict(state='succeeded');proof=self.proof('coding')
        self.assertTrue(judge('coding',root,proof))
        for key,value in [('original_tests_unchanged',False),('implementation_changed',False),('model_test_exit_code',1),('verification_test_exit_code',None)]:
            self.assertFalse(judge('coding',root,{**proof,key:value}),key)

    def test_oral_success_or_stop_receipt_cannot_replace_original_runtime_and_http_evidence(self):
        proof=self.proof('clone');self.assertTrue(judge('clone',dict(state='succeeded'),proof))
        for key,value in [('model_source_integrity',False),('stopped_vm_not_resumed_for_inspection',False),('sdk_stopped',False),('identity_matches',False),('model_workers_exited',False),('checkout_matches',False),
                          ('tracked_files_unchanged',False),('http_ready_before_stop',False),('actual_log_bytes',0),
                          ('original_service_command',False),
                          ('model_stop_same_environment',False),('preview_unreachable',False)]:
            self.assertFalse(judge('clone',dict(state='succeeded'),{**proof,key:value}),key)
        changed=copy.deepcopy(proof);changed['model_receipts'][2]['ok']=False
        self.assertFalse(judge('clone',dict(state='succeeded'),changed))

    def test_cancel_needs_an_active_guest_and_worker_and_sdk_independently_stopped(self):
        proof=self.proof('cancel');self.assertTrue(judge('cancel',dict(state='cancelled'),proof))
        for key in ('active_guest_marker','cancel_requested','original_tool_command','sdk_stopped','model_workers_exited'):
            self.assertFalse(judge('cancel',dict(state='cancelled'),{**proof,key:False}),key)
        self.assertFalse(judge('cancel',dict(state='succeeded'),proof))
        self.assertFalse(judge('cancel',dict(state='cancelled'),{**proof,'fallback_cleanup_used':True}))

    def test_project_verdict_does_not_apply_json_none_contract_and_keeps_expected_cancel_distinct(self):
        for scenario,state in [('coding','succeeded'),('clone','succeeded'),('cancel','cancelled')]:
            spec=next(value for value in CASES if value.get('scenario')==scenario and value.get('executor')=='project')
            self.assertEqual(evaluate(spec,dict(state=state,answer='实际结果见证据',events=[]),verification={'verified':True})[0],'success')
            self.assertEqual(evaluate(spec,dict(state=state,answer='已完成',events=[]),verification={'verified':False})[0],'false_success')
