import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI
from atlas.llm import ChatOptions,IncompleteResponseError
from desk_companion.execution_context import ExecutionLLM
from desk_companion.task_budgets import Ledger,installed,BudgetExceeded


class BudgetTransport(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=self.tmp.name);env.start();self.addCleanup(env.stop)
        self.ledger=Ledger();self.root=self.ledger.create('goal');self.ledger.bind(self.root,'physical-child')
        self.ledger.bind(self.root,'root',root_member=True)
        self.sent=[];self.finish='stop';self.completion=1;self.fail=False
        def handle(request):
            self.sent.append(json.loads(request.content))
            if self.fail:raise httpx.ReadTimeout('fixture lost response',request=request)
            return httpx.Response(200,json=dict(id='one',object='chat.completion',created=0,model='fixture',
                choices=[dict(index=0,finish_reason=self.finish,message=dict(role='assistant',content='partial'if self.finish=='length'else'answer'))],
                usage=dict(prompt_tokens=10,completion_tokens=self.completion,total_tokens=10+self.completion)))
        http=httpx.Client(transport=httpx.MockTransport(handle));self.addCleanup(http.close)
        sdk=OpenAI(api_key='fixture-only',base_url='https://model.invalid/v1',http_client=http,max_retries=0);self.addCleanup(sdk.close)
        self.llm=ExecutionLLM(model='fixture',client=sdk,context_model_id='fixture')
        for target,value in [('desk_companion.execution_context.model_capacity.for_call',dict(source='unknown')),
                ('desk_companion.execution_context.model_tokenizer.count',dict(tokens=10,source='fixture_bound_counter')),
                ('desk_companion.model_generation.binding',None)]:
            mock=patch(target,return_value=value);mock.start();self.addCleanup(mock.stop)

    def test_no_progress_denies_actual_fourth_sdk_send_and_unknown_capacity_adds_no_parameter(self):
        with installed('physical-child'):
            for _ in range(3):self.llm.chat([dict(role='user',content='same work')])
            with self.assertRaises(BudgetExceeded):self.llm.chat([dict(role='user',content='another request')])
        self.assertEqual(len(self.sent),3)
        self.assertTrue(all('max_completion_tokens'not in body and'max_tokens'not in body for body in self.sent))
        self.assertEqual(self.ledger.view(self.root)['consumed']['model_calls'],3)

    def test_verified_cap_reaches_actual_sdk_and_partial_usage_is_settled_without_completion(self):
        self.finish='length';self.completion=64
        with installed('physical-child'),patch('desk_companion.model_generation.binding',return_value=dict(parameter='max_completion_tokens')):
            with self.assertRaises(IncompleteResponseError)as caught:
                self.llm.chat([dict(role='user',content='long')],options=ChatOptions(extra_body=dict(max_completion_tokens=64)))
        self.assertEqual(caught.exception.partial['message']['content'],'partial')
        self.assertEqual(self.sent[0]['max_completion_tokens'],64)
        view=self.ledger.view(self.root)
        self.assertEqual(view['consumed']['output_tokens'],64)
        self.assertEqual(view['consumed']['output_unknown_calls'],0)
        self.assertEqual(view['output_mode'],'requested_hard')
        self.assertEqual(view['stop_reason']['dimension'],'per_call_output')

    def test_response_loss_does_not_refund_reservation_or_retry_sdk(self):
        self.fail=True
        with installed('physical-child'):
            with self.assertRaises(Exception):self.llm.chat([dict(role='user',content='lost')])
        self.assertEqual(len(self.sent),1)
        view=self.ledger.view(self.root)
        self.assertEqual(view['consumed']['model_calls'],1)
        self.assertEqual(view['consumed']['output_occupied'],4096)
        self.assertEqual(view['consumed']['output_unknown_calls'],1)

    def test_provider_ignoring_verified_cap_is_a_failure_with_preserved_partial(self):
        self.completion=100
        with installed('physical-child'),patch('desk_companion.model_generation.binding',return_value=dict(parameter='max_completion_tokens')):
            with self.assertRaises(BudgetExceeded)as caught:
                self.llm.chat([dict(role='user',content='long')],options=ChatOptions(extra_body=dict(max_completion_tokens=64)))
        self.assertEqual(caught.exception.partial['message']['content'],'answer')
        self.assertEqual(self.ledger.view(self.root)['status'],'blocked')
        with installed('physical-child'):
            with self.assertRaises(BudgetExceeded):self.llm.chat([dict(role='user',content='not sent')])
        self.assertEqual(len(self.sent),1)

    def test_final_reserve_drops_work_tools_and_keeps_messages_owned_by_caller(self):
        with self.ledger.change(self.root)as root:root['no_progress']=3
        messages=[dict(role='user',content='finish')]
        tools=[dict(type='function',function=dict(name='project_start',parameters=dict(type='object')))]
        with installed('root'):self.llm.chat(messages,tools)
        self.assertEqual(len(messages),1)
        self.assertNotIn('tools',self.sent[0])
        self.assertIn('预算进入收尾',self.sent[0]['messages'][-1]['content'])

    def test_tool_denial_is_known_not_started_and_cannot_become_unknown_write(self):
        from desk_companion.feedback import pending_operations
        result=dict(ok=False,error='quota',budget_denied=True,operation=dict(state='not_started'))
        self.assertEqual(pending_operations([dict(tool='project_prepare',call_id='one',read_only=False,status='failed',result=result)]),[])

    def test_generation_cap_respects_known_window_headroom_without_using_output_reserve_as_parameter(self):
        with installed('physical-child'),patch('desk_companion.model_generation.binding',return_value=dict(parameter='max_completion_tokens')), \
                patch('desk_companion.execution_context.model_capacity.for_call',return_value=dict(window=1024,output_reserve=1)):
            self.llm.chat([dict(role='user',content='known ten input tokens')])
        self.assertEqual(self.sent[0]['max_completion_tokens'],1014)
