"""Reject plausible answers with missing lifecycle or artifact evidence."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from desk_companion.agent_evals import CASES,evaluate,refresh_call_evidence,summarize
from desk_companion.agent_eval_tools import prepare,verify,FIXTURE_URL
from desk_companion.agent_eval_lifecycle import judge


def spec(ident):return next(c for c in CASES if c['id']==ident)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.profile=Path(folder.name)
        env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=folder.name);env.start();self.addCleanup(env.stop)
        from desk_companion.collaboration import initialize_workspace
        self.workspace=self.profile/'workspace.sqlite3';initialize_workspace(self.workspace)
        self.run_id=uuid.uuid4().hex

    def test_correct_calendar_answer_cannot_hide_a_mutation(self):
        case=spec('calendar_read_only');context=prepare(case,self.workspace,self.run_id,fixture=True)
        task=dict(state='succeeded',answer=json.dumps(case['expected']),events=[dict(kind='tool_end',data=dict(tool='read_local_calendar',status='success'))])
        self.assertEqual(evaluate(case,task,verification=verify(case,task,context))[0],'success')
        calendar=context['calendar']
        result=calendar.change(dict(type='create',request_id=uuid.uuid4().hex,summary='unexpected',
            start='2040-01-02T15:00:00+08:00',end='2040-01-02T16:00:00+08:00',item_id=''))
        self.assertTrue(result['ok'])
        self.assertEqual(evaluate(case,task,verification=verify(case,task,context))[0],'false_success')

    def test_correct_source_answer_cannot_cite_another_roots_snapshot(self):
        from desk_companion.web_snapshots import PageReader
        from scripts.eval_fixture_worker import fetch_page
        case=spec('research_public_source');context=prepare(case,self.workspace,self.run_id,fixture=True)
        with patch('desk_companion.web_snapshots.fetch_page',fetch_page):
            source=PageReader(uuid.uuid4().hex).read(FIXTURE_URL)
        task=dict(state='succeeded',answer=json.dumps({**case['expected'],'citation':'[来源]('+FIXTURE_URL+')'}),
            events=[dict(kind='evidence',data=source),dict(kind='tool_end',data=dict(tool='web_fetch',status='success'))])
        self.assertEqual(evaluate(case,task,verification=verify(case,task,context))[0],'false_success')

    def test_missing_citation_fails_even_after_real_scoped_snapshot_is_read(self):
        from desk_companion.web_snapshots import PageReader
        from scripts.eval_fixture_worker import fetch_page
        case=spec('research_public_source');context=prepare(case,self.workspace,self.run_id,fixture=True)
        with patch('desk_companion.web_snapshots.fetch_page',fetch_page):source=PageReader(self.run_id).read(FIXTURE_URL)
        task=dict(state='succeeded',answer=json.dumps(case['expected']),events=[dict(kind='evidence',data=source)])
        self.assertFalse(verify(case,task,context)['verified'])

    def test_claimed_chart_success_requires_artifact_verification(self):
        case=spec('chart_from_evidence')
        task=dict(state='succeeded',answer='已经画好流程图',events=[dict(kind='tool_end',data=dict(tool=t,status='success')) for t in case['required_tools']])
        self.assertEqual(evaluate(case,task)[0],'false_success')

    def test_cancelled_state_does_not_prove_worker_has_exited(self):
        root=dict(id='root',state='cancelled')
        self.assertFalse(judge('cancel',root,[],dict(cancel_observed_active=True,tasks_terminal=True,workers_exited_before_shutdown=False)))
        self.assertEqual(evaluate(spec('cancel_active_model'),root)[0],'false_success')

    def test_dependency_failure_is_not_a_claim_of_success(self):
        root=dict(state='cancelled',events=[])
        self.assertEqual(evaluate(spec('delegate_deliver'),root,verification=dict(verified=False,chain_state='failed'))[0],'failed')
        self.assertEqual(evaluate(spec('delegate_deliver'),root,verification=dict(verified=False,chain_state='cancelled'))[0],'cancelled')

    def test_lifecycle_proof_cannot_override_actual_failed_state(self):
        root=dict(state='failed',events=[])
        self.assertEqual(evaluate(spec('clarify_resume_root'),root,verification=dict(verified=True))[0],'failed')

    def test_matching_answer_requires_same_resumed_root_and_actual_child(self):
        root=dict(id='root',state='succeeded',answer='{"total":18,"checked":true}',stage='deliver')
        self.assertFalse(judge('clarify',root,[],dict(paused_root='other',resumed_root='root')))
        self.assertFalse(judge('delegate',root,[],dict(accepted_delivery=True)))
        self.assertFalse(judge('delegate',root,[dict(parent='root',agent='writer',state='failed')],dict(accepted_delivery=True)))

    def test_summary_counts_all_phases_separately_from_cases(self):
        _,total=summarize([dict(outcome='success',task_id='route',task_ids=['route','work'],call_ids=[]),
                           dict(outcome='success',task_id='next',task_ids=['next'],call_ids=[])])
        self.assertEqual(total['tasks'],3);self.assertEqual(total['cases_started'],2)

    def test_child_queue_success_cannot_substitute_for_correct_delegated_work(self):
        root=dict(id='root',state='succeeded',answer='{"total":18,"checked":true}',stage='deliver')
        child=dict(parent='root',agent='writer',state='succeeded',answer='{"total":999,"checked":true}')
        self.assertFalse(judge('delegate',root,[child],dict(accepted_delivery=True)))
        child['answer']=root['answer']
        self.assertTrue(judge('delegate',root,[child],dict(accepted_delivery=True)))

    def test_exception_path_keeps_actual_call_and_partial_unknown_usage(self):
        row=dict(task_id='task',outcome='timed_out')
        call=dict(id='call',pid=123,created=5,state='sending')
        listing=lambda **kw:dict(items=[call])
        detail=lambda ident:dict(response=dict(usage=None))
        self.assertTrue(refresh_call_evidence(row,listing,detail))
        self.assertEqual(row['call_ids'],['call']);self.assertEqual(row['calls'][0]['pid'],123)
        self.assertIsNone(row['usage']);self.assertFalse(row['usage_complete'])
        self.assertEqual(summarize([row])[1]['model_calls'],1)
        def unavailable(**kw):raise OSError('trace unavailable')
        self.assertFalse(refresh_call_evidence(row,unavailable,detail))
        self.assertEqual(row['call_ids'],['call']);self.assertFalse(row['call_evidence_complete'])

    def test_setup_failure_removes_network_credentials_and_restores_parent_rpc(self):
        from scripts import eval_agent
        model=self.profile/'models.json';network=self.profile/'model-network.json'
        model.write_text('synthetic-model-secret','utf-8');network.write_text('synthetic-proxy-secret','utf-8')
        previous=eval_agent.CREDENTIAL_PATH,eval_agent.NETWORK_PATH
        eval_agent.CREDENTIAL_PATH,eval_agent.NETWORK_PATH=model,network
        def fail():
            os.environ.pop('DESK_TERMINAL_TOKEN',None)
            raise RuntimeError('setup failed')
        try:
            with patch.dict(os.environ,DESK_TERMINAL_TOKEN='synthetic-parent-token'):
                with self.assertRaises(RuntimeError):eval_agent.run_with_cleanup(fail)
                self.assertEqual(os.environ['DESK_TERMINAL_TOKEN'],'synthetic-parent-token')
            self.assertFalse(model.exists());self.assertFalse(network.exists())
        finally:eval_agent.CREDENTIAL_PATH,eval_agent.NETWORK_PATH=previous
