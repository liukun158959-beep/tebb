import unittest
from pathlib import Path
import tempfile
from desk_companion.agent_evals import CASES, CancellationNotSettled, aggregate_usage, call_evidence, evaluate, same_json, summarize, wait_task


class EvalVerdictTests(unittest.TestCase):
    def test_actual_http_status_is_preserved_from_call_record(self):
        for status in (200,429,None):
            self.assertEqual(call_evidence(dict(id='call',status=status))['http_status'],status)

    def test_unsettled_cancellation_has_an_explicit_wait_bound(self):
        class Queue:
            cancelled=False
            def get(self,task_id): return dict(state='cancelling' if self.cancelled else 'running')
            def cancel(self,task_id): self.cancelled=True
        queue=Queue()
        ticks=iter((0,1,2,3))
        with self.assertRaises(CancellationNotSettled) as failure:
            wait_task(queue,'task',0,grace=1,clock=lambda:next(ticks),pause=lambda _:None)
        self.assertTrue(queue.cancelled)
        self.assertEqual(failure.exception.task['state'],'cancelling')

    def test_terminal_task_needs_no_cancellation(self):
        class Queue:
            def get(self,task_id): return dict(state='succeeded')
            def cancel(self,task_id): raise AssertionError('already complete')
        self.assertEqual(wait_task(Queue(),'task',0),(dict(state='succeeded'),False))

    def test_named_cases_have_explicit_conditions_and_separate_cancellation_scopes(self):
        self.assertEqual(len(CASES),23)
        self.assertEqual(len({c['id'] for c in CASES}),23)
        self.assertTrue(all('expected_state' in c and 'prerequisite' in c for c in CASES))

    def test_queue_success_with_wrong_content_is_false_success(self):
        spec=CASES[0]
        verdict,evidence=evaluate(spec,dict(state='succeeded',answer='我已经完成了。',events=[]))
        self.assertEqual(verdict,'false_success')
        self.assertEqual(evidence['reason'],'answer_is_not_required_json')

    def test_expected_json_and_required_tool_receipt_are_both_required(self):
        spec=dict(CASES[0],required_tools=['read'])
        task=dict(state='succeeded',answer='{"id":"A-17","quantity":3,"unit_price":12}',events=[])
        self.assertEqual(evaluate(spec,task)[0],'false_success')
        task['events']=[dict(kind='tool_end',data=dict(tool='read',status='failed'))]
        self.assertEqual(evaluate(spec,task)[0],'false_success')
        task['events'][0]['data']['status']='success'
        self.assertEqual(evaluate(spec,task)[0],'success')

    def test_failure_or_cancel_cannot_be_overridden_by_correct_answer(self):
        for state,outcome in [('failed','failed'),('cancelled','cancelled'),('timed_out','timed_out'),('interrupted','failed')]:
            self.assertEqual(evaluate(CASES[1],dict(state=state,answer='[1,2,4]',events=[]))[0],outcome)

    def test_route_clarify_does_not_claim_persisted_needs_input(self):
        outcome,evidence=evaluate(CASES[8],dict(state='succeeded',answer='{"mode":"clarify","text":"请说明日期"}',events=[]))
        self.assertEqual(outcome,'success')
        self.assertEqual(CASES[8]['category'],'route_decision_contract')
        self.assertEqual(evidence['route_decision'],'clarify')
        self.assertIsNone(evidence['user_task_state'])
        self.assertEqual(evaluate(CASES[8],dict(state='succeeded',answer='{"mode":"answer","text":"已创建"}',events=[]))[0],'false_success')

    def test_json_boolean_does_not_pass_numeric_expectation(self):
        self.assertEqual(evaluate(CASES[4],dict(state='succeeded',answer='{"minutes":true}',events=[]))[0],'false_success')
        self.assertFalse(same_json(True,1))
        self.assertTrue(same_json(200.0,200))

    def test_summary_retains_failure_and_unknown_usage(self):
        counts,totals=summarize([dict(outcome='failed',task_id='task1',call_ids=['call1'],usage=None),dict(outcome='incomplete')])
        self.assertEqual(counts['failed'],1)
        self.assertEqual(counts['incomplete'],1)
        self.assertEqual(counts['skip'],0)
        self.assertIsNone(totals['usage'])
        self.assertFalse(totals['usage_complete'])
        self.assertEqual(totals['model_calls'],1)

    def test_empty_or_null_usage_is_unknown(self):
        for raw in (None,{},dict(prompt_tokens=None,completion_tokens=None)):
            self.assertEqual(aggregate_usage([raw]),(None,False))

    def test_partial_usage_preserves_only_known_component(self):
        self.assertEqual(aggregate_usage([dict(prompt_tokens=12)]),(dict(input_tokens=12,output_tokens=None),False))
        self.assertEqual(aggregate_usage([dict(prompt_tokens=12,completion_tokens=3),None]),(dict(input_tokens=12,output_tokens=3),False))

    def test_invalid_usage_cannot_be_counted_as_zero_or_complete(self):
        for invalid in ('12',True,-1,float('nan'),float('inf'),1.5):
            self.assertEqual(aggregate_usage([dict(prompt_tokens=invalid,completion_tokens=None)]),(None,False))

    def test_explicit_zero_is_measured_and_complete(self):
        self.assertEqual(aggregate_usage([dict(prompt_tokens=0,completion_tokens=0)]),(dict(input_tokens=0,output_tokens=0),True))

    def test_summary_does_not_mark_partial_case_usage_complete(self):
        _,totals=summarize([dict(outcome='success',task_id='task',usage=dict(input_tokens=10,output_tokens=None),usage_complete=False)])
        self.assertEqual(totals['usage'],dict(input_tokens=10,output_tokens=None))
        self.assertFalse(totals['usage_complete'])

    def test_empty_clarification_is_false_success(self):
        import json
        for text in ('','   ',None,123,[],{}):
            with self.subTest(text=text):
                self.assertEqual(evaluate(CASES[8],dict(state='succeeded',answer=json.dumps(dict(mode='clarify',text=text)),events=[]))[0],'false_success')

    def test_bootstrap_failure_cleans_worker_credential(self):
        from scripts import eval_agent
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'models.json'
            path.write_text('synthetic-secret','utf-8')
            previous=eval_agent.CREDENTIAL_PATH
            eval_agent.CREDENTIAL_PATH=path
            try:
                with self.assertRaises(RuntimeError):
                    eval_agent.run_with_cleanup(lambda: (_ for _ in ()).throw(RuntimeError('setup failed')))
                self.assertFalse(path.exists())
            finally: eval_agent.CREDENTIAL_PATH=previous
