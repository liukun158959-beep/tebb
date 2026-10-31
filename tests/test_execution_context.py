"""Admission uses full requests, preserves authority/receipts and never edits history."""
import copy
import json
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI
from atlas.llm import ChatOptions
from desk_companion.execution_context import ContextAdmissionError,ExecutionLLM,prepare_request,replace_owned_message


def counter(request,capacity):
    # Controlled unit metric, deliberately not described as a real model tokenizer.
    return dict(tokens=len(json.dumps(request,ensure_ascii=False)),source='fixture_counter')


def request(body=None):
    messages=[dict(role='system',content='Permission: read only; never repeat unknown writes.'),dict(role='user',content='Current task: preserve original receipt op-unknown.')]
    for index,value in enumerate([body or dict(ok=True,source_id='source-original',text='x'*10000),{'recent':1},{'recent':2}]):
        messages.extend([dict(role='assistant',content=None,tool_calls=[dict(id=str(index),type='function',function=dict(name='read_local_source',arguments='{}'))]),
                         dict(role='tool',tool_call_id=str(index),content=json.dumps(value))])
    return dict(model='fixture',messages=messages,tools=[dict(type='function',function=dict(name='read_local_source',parameters=dict(type='object')))])


class ExecutionContextTests(unittest.TestCase):
    def test_partner_history_preserves_old_and_long_receipts_and_honors_manual_coverage(self):
        from desk_companion.collaboration import Collaboration
        marker='original-op-unknown-do-not-repeat'
        conversation=dict(id='one',summary='explicit summary',summary_ids=['covered'])
        rows=[dict(id=str(i),conversation='one',sender='user',text=marker if i==0 else str(i))for i in range(24)]
        rows.extend([dict(id='long',conversation='one',sender='user',text='x'*8000+marker),
                     dict(id='covered',conversation='one',sender='user',text='covered text'),
                     dict(id='other',conversation='other',sender='user',text='private other conversation')])
        history=Collaboration._history(dict(messages=rows),conversation)
        self.assertEqual(len(history),26);self.assertEqual(history[1]['text'],marker)
        self.assertTrue(history[-1]['text'].endswith(marker))
        self.assertNotIn('covered text',str(history));self.assertNotIn('private other',str(history))

    def test_ordinary_history_has_no_fixed_128k_auto_summary_or_write(self):
        from desk_companion.context_pack import prepare_injection,context_view
        rows=[dict(role='user',text='original unknown receipt'),dict(role='pet',text='kept')]
        with patch('desk_companion.context_pack.session_for_model',return_value=rows),patch('desk_companion.context_pack._read_session',return_value=None),patch('desk_companion.model_capacity.current',return_value=dict(window=None,source='unknown')):
            complete=unittest.mock.Mock();save=unittest.mock.Mock()
            result=prepare_injection('synthetic','system','A'*512001,exclude_user=None,manual=False,complete=complete,save=save)
            view=context_view('synthetic','system')
        self.assertEqual([r['text']for r in result],[r['text']for r in rows]);complete.assert_not_called();save.assert_not_called()
        self.assertIsNone(view['budget']);self.assertIsNone(view['over'])
        self.assertEqual(view['estimate_source'],'character_heuristic_history_only')

    def test_unknown_window_or_tokenizer_keeps_full_material_and_explicit_unknown(self):
        original=request();before=copy.deepcopy(original)
        for capacity,count in [(dict(window=None),counter),(dict(window=2048,output_reserve=100),lambda *a:dict(tokens=None,source='unavailable'))]:
            prepared,report=prepare_request(original,capacity,counter=count)
            self.assertEqual(prepared,before);self.assertFalse(report['compacted'])
            self.assertIn('unknown',report['decision'])
        self.assertEqual(original,before)

    def test_full_tools_count_and_known_output_reserve_are_part_of_admission(self):
        value=dict(model='fixture',messages=[dict(role='user',content='small')],tools=[dict(description='large schema'*400)])
        with self.assertRaises(ContextAdmissionError)as caught:
            prepare_request(value,dict(window=2048,output_reserve=512),counter=counter)
        self.assertEqual(caught.exception.report['tools'],1)
        self.assertEqual(caught.exception.report['decision'],'protected_material_overflow')
        self.assertEqual(caught.exception.report['output_reserve'],512)

    def test_old_success_body_is_excerpted_with_actual_recount_but_calls_and_ids_remain(self):
        original=request();saved=copy.deepcopy(original)
        prepared,report=prepare_request(original,dict(window=2048,output_reserve=128),counter=counter)
        self.assertTrue(report['compacted']);self.assertLessEqual(counter(prepared,{})['tokens'],1920)
        self.assertEqual(report['input_tokens'],counter(prepared,{})['tokens'])
        self.assertEqual(original,saved)
        self.assertEqual(prepared['messages'][1:3],original['messages'][:2])
        before=[m.get('tool_call_id')for m in original['messages']if m['role']=='tool']
        after=[m.get('tool_call_id')for m in prepared['messages']if m['role']=='tool']
        self.assertEqual(before,after)
        value=json.loads(prepared['messages'][4]['content'])
        self.assertEqual(value['source_id'],'source-original');self.assertTrue(value['context_excerpt'])
        self.assertEqual(prepared['messages'][-4:],original['messages'][-4:])

    def test_failure_unknown_write_and_recent_evidence_cannot_be_silently_snipped(self):
        for fields in [dict(ok=False,error='denied'),dict(operation=dict(id='op',state='unknown')),
                       dict(operation=dict(id='op',kind='write',state='succeeded')),
                       dict(result=dict(ok=False,error='nested failure'))]:
            value=dict(ok=True,source_id='source-original',text='x'*10000);value.update(fields)
            original=request(value);saved=copy.deepcopy(original)
            with self.assertRaises(ContextAdmissionError):prepare_request(original,dict(window=2048),counter=counter)
            self.assertEqual(original,saved)
        recent=request(dict(ok=True,source_id='short',text='short'))
        recent['messages'][-1]['content']=json.dumps(dict(ok=True,source_id='recent',text='x'*10000))
        with self.assertRaises(ContextAdmissionError):prepare_request(recent,dict(window=2048),counter=counter)

    def test_count_disappearing_mid_projection_never_sends_unverified_copy(self):
        calls=iter([counter(request(),{}),dict(tokens=None,source='unavailable')])
        with self.assertRaises(ContextAdmissionError)as caught:
            prepare_request(request(),dict(window=2048),counter=lambda *a:next(calls))
        self.assertEqual(caught.exception.report['decision'],'tokenizer_changed')

    def test_mutating_or_unknown_tool_does_not_gain_readonly_status_from_its_body(self):
        for name in ['project_execute','send_message','unknown_plugin']:
            original=request();original['messages'][2]['tool_calls'][0]['function']['name']=name
            with self.assertRaises(ContextAdmissionError):
                prepare_request(original,dict(window=2048),counter=counter)

    def test_reused_call_id_does_not_reclassify_an_old_mutating_receipt(self):
        original=request()
        original['messages'][2]['tool_calls'][0]['function']['name']='project_execute'
        original['messages'][6]['tool_calls'][0]['id']='0'
        original['messages'][7]['tool_call_id']='0'
        with self.assertRaises(ContextAdmissionError):prepare_request(original,dict(window=2048),counter=counter)

    def test_all_results_of_the_current_parallel_tool_call_group_are_protected(self):
        original=request();messages=original['messages']
        assistant=copy.deepcopy(messages[2]);assistant['tool_calls']=[messages[i]['tool_calls'][0]for i in(2,4,6)]
        original['messages']=messages[:2]+[assistant]+[messages[i]for i in(3,5,7)]
        with self.assertRaises(ContextAdmissionError):prepare_request(original,dict(window=2048),counter=counter)

    def test_text_byte_protection_is_not_a_fake_token_window_or_a_media_limit(self):
        value=dict(messages=[dict(role='user',content='x'*2_000_001)])
        with self.assertRaises(ContextAdmissionError)as caught:prepare_request(value,{},counter=counter)
        self.assertEqual(caught.exception.report['decision'],'text_limit')
        media=dict(messages=[dict(role='user',content=[dict(type='text',text='describe'),dict(type='image_url',image_url={'url':'data:image/png;base64,'+'x'*2_000_001})])])
        prepared,report=prepare_request(media,{},counter=lambda *a:dict(tokens=None,source='unavailable'))
        self.assertEqual(prepared,media);self.assertLess(report['text_bytes'],100)

    def test_owned_catalog_replacement_does_not_delete_equal_user_objects(self):
        user=dict(role='user',content='same');owned=copy.deepcopy(user);messages=[user,owned]
        current=replace_owned_message(messages,owned,'updated')
        self.assertIs(messages[0],user);self.assertIs(messages[1],current);self.assertEqual(len(messages),2)

    def test_real_atlas_sdk_boundary_receives_exact_prepared_messages_once(self):
        calls=[]
        def handle(req):
            calls.append(json.loads(req.content))
            return httpx.Response(200,json=dict(id='response',object='chat.completion',created=0,model='fixture',
                choices=[dict(index=0,finish_reason='stop',message=dict(role='assistant',content='OK'))],
                usage=dict(prompt_tokens=17,completion_tokens=1,total_tokens=18)))
        http=httpx.Client(transport=httpx.MockTransport(handle));self.addCleanup(http.close)
        sdk=OpenAI(api_key='fixture-only',base_url='https://model.invalid/v1',http_client=http,max_retries=0);self.addCleanup(sdk.close)
        observed=[];llm=ExecutionLLM(model='fixture',client=sdk,context_model_id='fixture',on_context=observed.append)
        original=request();capacity=dict(window=2048,output_reserve=128,source='fixture_config')
        with patch('desk_companion.execution_context.model_capacity.for_call',return_value=capacity),patch('desk_companion.execution_context.model_tokenizer.count',side_effect=counter):
            result=llm.chat(original['messages'],original['tools'],options=ChatOptions())
        self.assertEqual(result['message']['content'],'OK');self.assertEqual(len(calls),1)
        expected,report=prepare_request(original,capacity,counter=counter)
        self.assertEqual(calls[0]['messages'],expected['messages']);self.assertEqual(calls[0]['tools'],original['tools'])
        self.assertEqual(observed[0]['material_sha256'],report['material_sha256'])
        usage=llm.last_context['provider_usage']
        self.assertEqual(usage.get('input_tokens',usage.get('prompt_tokens')),17)

    def test_observer_failure_does_not_retry_or_change_admission(self):
        llm=ExecutionLLM.__new__(ExecutionLLM);llm.last_context={'decision':'unchanged'}
        llm.on_context=lambda report:(_ for _ in()).throw(RuntimeError('observer failed'))
        llm._context_notice();self.assertEqual(llm.last_context['decision'],'unchanged')
