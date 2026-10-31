import concurrent.futures
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI
from desk_companion import agent_debug
from desk_companion.usage_store import UsageStore,normalized_usage,estimate
from desk_companion.usage_observer import ModelAttempt,invoke_tool


class UsageRecords(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        env=patch.dict(os.environ,{'DESK_COMPANION_DATA_DIR':str(self.path)})
        env.start();self.addCleanup(env.stop)
        old,default=httpx.Client.send,agent_debug._default
        self.addCleanup(setattr,httpx.Client,'send',old)
        self.addCleanup(setattr,agent_debug,'_default',default)
        agent_debug.install();agent_debug.configure(False)
        self.store=UsageStore()

    def test_zero_missing_cache_subset_and_price_unknown_are_distinct(self):
        zero=normalized_usage({'prompt_tokens':0,'completion_tokens':0,'prompt_tokens_details':{'cached_tokens':0}})
        missing=normalized_usage(None)
        self.assertEqual(zero['total_tokens'],0);self.assertIsNone(missing['total_tokens'])
        prices={'input_cny_per_mtok':0,'output_cny_per_mtok':0}
        self.assertEqual(estimate(zero,prices),0);self.assertIsNone(estimate(missing,prices));self.assertIsNone(estimate(zero,None))
        cached=normalized_usage({'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':80}})
        self.assertEqual(cached['total_tokens'],120);self.assertEqual(cached['cached_tokens'],80)
        invalid=normalized_usage({'prompt_tokens':10,'completion_tokens':0,'prompt_tokens_details':{'cached_tokens':11}})
        self.assertIsNone(invalid['cached_tokens']);self.assertEqual(invalid['usage_quality'],'invalid_details')

    def test_same_identity_is_idempotent_and_rebinding_or_payload_is_rejected(self):
        self.assertTrue(self.store.begin('a','model',{'root':'r','task':'t'},now=1))
        self.assertFalse(self.store.begin('a','model',{'root':'r','task':'t'},now=2))
        self.assertEqual(len(self.store.rows(0,3)),1)
        self.assertEqual(self.store.rows(0,3)[0]['started'],1)
        with self.assertRaises(ValueError):self.store.begin('a','model',{'root':'other','task':'t'})
        with self.assertRaises(ValueError):self.store.begin('secret','model',{'prompt':'PRIVATE_PROMPT'})
        with self.assertRaises(ValueError):self.store.update('a',task='other')

    def test_actual_sdk_metadata_survives_debug_pause_without_payloads(self):
        (self.path/'user_state.json').write_text(json.dumps({'model_prices':{'demo':{'input_cny_per_mtok':2,'output_cny_per_mtok':4}}}),'utf-8')
        def handle(request):
            return httpx.Response(200,json={'id':'c','choices':[{'index':0,'message':{'role':'assistant','content':'PRIVATE_RESULT'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':80}}})
        with httpx.Client(transport=httpx.MockTransport(handle))as transport:
            with OpenAI(api_key='PRIVATE_KEY',base_url='https://model.invalid/v1',http_client=transport,max_retries=0)as client:
                with ModelAttempt('demo','model-id','https://model.invalid/v1',ident='reservation-id')as observed:
                    result=client.chat.completions.create(model='demo',messages=[{'role':'user','content':'PRIVATE_PROMPT'}])
                    observed.returned(result.usage.model_dump())
        rows=self.store.rows();self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual(row['id'],'reservation-id');self.assertEqual(row['state'],'returned')
        self.assertEqual(row['usage']['cached_tokens'],80);self.assertEqual(row['cost_cny'],0.00028)
        self.assertEqual(len(row['network_attempts']),1);self.assertEqual(row['network_attempts'][0]['status'],200)
        self.assertEqual(list((self.path/'memory/agent_debug').glob('*.request.json')),[])
        metadata=json.dumps(rows)
        for secret in ['PRIVATE_PROMPT','PRIVATE_RESULT','PRIVATE_KEY','https://model.invalid']:
            self.assertNotIn(secret,metadata)

    def test_failed_and_interrupted_calls_keep_unknown_usage(self):
        with self.assertRaises(TimeoutError):
            with ModelAttempt('demo',ident='failed'):raise TimeoutError('PRIVATE_ERROR')
        row=self.store.rows()[0]
        self.assertEqual(row['state'],'failed');self.assertIsNone(row['usage']['input_tokens'])
        self.assertNotIn('PRIVATE_ERROR',json.dumps(row))
        with self.assertRaises(KeyboardInterrupt):
            with ModelAttempt('demo',ident='cancelled'):raise KeyboardInterrupt()
        self.assertEqual(next(r for r in self.store.rows()if r['id']=='cancelled')['state'],'cancelled')

    def test_incomplete_response_retains_public_partial_usage(self):
        class PartialResponse(RuntimeError):
            partial={'usage':{'prompt_tokens':10,'completion_tokens':5}}
        with self.assertRaises(PartialResponse):
            with ModelAttempt('demo',ident='partial'):raise PartialResponse('PRIVATE_PARTIAL')
        row=self.store.rows()[0]
        self.assertEqual(row['state'],'failed');self.assertEqual(row['usage']['total_tokens'],15)
        self.assertNotIn('PRIVATE_PARTIAL',json.dumps(row))

    def test_sdk_retry_is_one_adapter_call_and_two_network_attempts(self):
        attempts=[]
        def handle(request):
            attempts.append(True)
            if len(attempts)==1:return httpx.Response(429,headers={'retry-after-ms':'1'},json={'error':{'message':'retry fixture','type':'rate_limit_error'}})
            return httpx.Response(200,json={'id':'r','choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}})
        with httpx.Client(transport=httpx.MockTransport(handle))as transport:
            with OpenAI(api_key='fixture',base_url='https://model.invalid/v1',http_client=transport,max_retries=1)as client:
                with ModelAttempt('demo',ident='retry')as observed:
                    response=client.chat.completions.create(model='demo',messages=[{'role':'user','content':'fixture'}])
                    observed.returned(response.usage.model_dump())
        rows=self.store.rows();self.assertEqual(len(rows),1)
        self.assertEqual([n['status']for n in rows[0]['network_attempts']],[429,200])

    def test_research_guard_does_not_create_an_execution_observation(self):
        from desk_companion.research_tools import ResearchTools
        search=unittest.mock.Mock(side_effect=AssertionError('must not execute'))
        adapter=ResearchTools(sources=[],discovered=set(),emit=lambda *args:None,search_limit=0,fetch_limit=0,search=search)
        self.assertFalse(adapter.invoke('web_search',{'query':'fixture'}).ok)
        search.assert_not_called();self.assertFalse(self.store.path.exists())

    def test_concurrent_tool_parents_are_context_bound_not_global_latest(self):
        barrier=threading.Barrier(2)
        def metadata():
            return dict(root='task:goal',task=threading.current_thread().name,project_id='p',channel='desktop',source='task')
        def run(ident):
            with ModelAttempt('demo',ident=ident)as observed:observed.returned({'prompt_tokens':1,'completion_tokens':1})
            barrier.wait(timeout=10)
            invoke_tool('lookup',lambda:{'ok':True})
        with patch('desk_companion.usage_observer._metadata',side_effect=metadata):
            with concurrent.futures.ThreadPoolExecutor(2)as pool:list(pool.map(run,['m1','m2']))
        rows=self.store.rows();models={r['task']:r['id']for r in rows if r['kind']=='model'}
        tools=[r for r in rows if r['kind']=='tool'];self.assertEqual(len(tools),2)
        for tool in tools:self.assertEqual(tool['parent_call'],models[tool['task']])

    def test_persisted_task_roots_deduplicate_resume_and_ignore_orphan_budget(self):
        from desk_companion.task_budgets import Ledger
        from desk_companion.tasks import usage_metadata
        ledger=Ledger(self.path/'memory/task_budgets.sqlite3')
        root=ledger.create('task:first');ledger.bind(root,'first',root_member=True);ledger.bind(root,'resume',root_member=True);ledger.bind(root,'child')
        ledger.create('task:orphan')
        path=self.path/'tasks.sqlite3'
        with closing(sqlite3.connect(path))as db,db:
            db.executescript('CREATE TABLE tasks(id,created,started,ended,state,channel,source,text);CREATE TABLE events(seq INTEGER,task,kind,data);')
            for ident,created,state in [('first',1,'failed'),('resume',2,'succeeded'),('child',3,'failed')]:
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',(ident,created,0,0,state,'desktop',json.dumps({'_usage_project_id':'p'}),'PRIVATE_TASK'))
            db.execute('INSERT INTO events VALUES(1,?,?,?)',('resume','feedback_check',json.dumps({'ok':True,'completion':'checked','execution':'completed','message':'PRIVATE_CHECK'})))
            db.execute('INSERT INTO events VALUES(2,?,?,?)',('child','feedback_check',json.dumps({'ok':{'text':'PRIVATE_BOOL'},'completion':{'text':'PRIVATE_COMPLETION'}})))
        value=usage_metadata(path,ledger)
        self.assertEqual(len(value['tasks']),1);self.assertEqual(len(value['attempts']),3)
        self.assertEqual(value['tasks'][0]['state'],'succeeded');self.assertEqual(value['tasks'][0]['created'],1)
        self.assertEqual(value['tasks'][0]['completion'],'checked')
        self.assertNotIn('PRIVATE_',json.dumps(value))

    def test_plan_success_is_not_whole_chain_success_and_no_channel_is_invented(self):
        from desk_companion.collaboration import usage_metadata
        path=self.path/'workspace.sqlite3'
        runs=[dict(id='root',parent='',kind='plan',state='succeeded',created=1,workspace={'project_id':'p'},answer='PRIVATE_ANSWER'),
            dict(id='child',parent='root',kind='task',state='running',created=2)]
        with closing(sqlite3.connect(path))as db,db:
            db.execute('CREATE TABLE workspace(id,data)');db.execute('INSERT INTO workspace VALUES(1,?)',(json.dumps({'runs':runs,'messages':['PRIVATE_MESSAGE']}),))
        value=usage_metadata(path)
        self.assertEqual(len(value),1);self.assertEqual(value[0]['state'],'running');self.assertIsNone(value[0]['channel'])
        self.assertNotIn('PRIVATE_',json.dumps(value))

    def query_fixture(self):
        from desk_companion.usage_center import UsageCenter
        meta=dict(root='task:goal',task='worker',project_id='p',channel='desktop',source='task',model='demo',model_id='entry',model_key='entry:route',route='route')
        for ident in ['a','b']:self.store.begin(ident,'model',meta,now=10)
        usage=normalized_usage({'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':80}})
        self.store.update('a',state='returned',usage=usage,cost_cny=0)
        self.store.update('b',state='failed',usage=normalized_usage(None),cost_cny=None)
        self.store.begin('tool','tool',{'root':'task:goal','task':'worker','project_id':'p','channel':'desktop','source':'task','tool':'lookup','parent_call':'a'},now=11)
        self.store.update('tool',state='returned')
        source=dict(tasks=[dict(id='task:goal',kind='task',created=1,state='failed',raw_state='failed',project_id='p',channel='desktop',source='task')],
            attempts=[dict(id='worker',root='task:goal',state='timed_out',created=2,project_id='p',channel='desktop')])
        service=UsageCenter(self.store,sources=lambda:source,clock=lambda:100,admissions=lambda owners:[dict(id='a',root='task:goal'),dict(id='unobserved',root='task:goal')])
        return service

    def test_connection_test_is_diagnostic_without_creating_a_business_task(self):
        service=self.query_fixture()
        self.store.begin('connection','model',dict(root='',task='',source='model_test',channel='diagnostic',model='demo',model_id='entry',model_key='entry:route',route='route'),now=12)
        self.store.update('connection',state='returned',usage=normalized_usage({'prompt_tokens':1,'completion_tokens':1}),cost_cny=None)
        value=service.query({'period':'all','kind':'diagnostic'})
        self.assertTrue(value['ok']);self.assertEqual(value['totals']['tasks'],1)
        self.assertEqual(value['totals']['model_calls'],3);self.assertEqual(value['totals']['diagnostic_model_calls'],1)
        self.assertEqual([row['id']for row in value['items']],['connection'])

    def test_query_reconciles_known_unknown_cache_and_nonexecuted_admission(self):
        value=self.query_fixture().query({'period':'all'})
        self.assertTrue(value['ok']);total=value['totals']
        self.assertEqual((total['tasks'],total['physical_attempts'],total['model_calls'],total['tool_calls']),(1,1,2,1))
        self.assertEqual(total['physical_states'],{'timed_out':1});self.assertEqual(total['input_tokens_known'],100)
        self.assertEqual(total['total_tokens_known'],120);self.assertEqual(total['cached_tokens_known'],80)
        self.assertEqual(total['input_tokens_unknown_calls'],1)
        self.assertEqual(total['cost_cny_known'],0);self.assertEqual(total['cost_unknown_calls'],1)
        self.assertEqual(value['coverage']['unobserved_model_admissions'],1)
        self.assertEqual(sum(r['model_calls']for r in value['by_model']),total['model_calls'])

    def test_same_time_cursor_has_no_gap_and_filter_change_is_rejected(self):
        service=self.query_fixture();filters={'period':'all','kind':'model'}
        first=service.query(filters,limit=1);second=service.query(filters,first['next_cursor'],1)
        self.assertEqual([first['items'][0]['id'],second['items'][0]['id']],['b','a'])
        self.assertEqual(second['next_cursor'],'')
        changed=service.query({**filters,'project':'unknown'},first['next_cursor'],1)
        self.assertFalse(changed['ok'])
        tasks=service.query({'period':'all','kind':'task'})
        self.assertEqual([r['id']for r in tasks['items']],['task:goal'])

    def test_legacy_rows_stay_separate_and_corruption_does_not_echo_body(self):
        service=self.query_fixture();path=self.path/'memory/usage.jsonl';path.parent.mkdir(exist_ok=True)
        old=dict(ts='2026-10-31T00:00:00+08:00',model='demo',input_tokens=0,output_tokens=None,cost_cny=None,run_id='same',text='PRIVATE_LEGACY')
        path.write_text(json.dumps(old)+'\n'+json.dumps(old)+'\n','utf-8')
        value=service.query({'period':'all'})
        self.assertEqual(value['totals']['model_calls'],2);self.assertEqual(value['legacy']['records'],2)
        self.assertEqual(value['legacy']['output_unknown_records'],2);self.assertFalse(value['legacy']['merged'])
        self.assertNotIn('PRIVATE_LEGACY',json.dumps(value))
        path.write_text('PRIVATE_BROKEN\n','utf-8');value=service.query({'period':'all'})
        self.assertTrue(value['ok']);self.assertFalse(value['legacy']['ok']);self.assertNotIn('PRIVATE_BROKEN',json.dumps(value))

    def test_empty_read_has_no_database_side_effect_and_bad_filters_fail(self):
        from desk_companion.usage_sources import task_metadata
        from desk_companion.usage_center import UsageCenter
        self.assertEqual(task_metadata(),dict(tasks=[],attempts=[]))
        service=UsageCenter();self.assertTrue(service.query()['ok'])
        self.assertFalse(self.store.path.exists());self.assertFalse((self.path/'memory/tasks.sqlite3').exists())
        for filters in [[],False,{'period':[]},{'period':'forever'},{'unexpected':'PRIVATE_PARAM'}]:
            value=service.query(filters);self.assertFalse(value['ok']);self.assertNotIn('PRIVATE_PARAM',json.dumps(value))

    def test_terminal_worker_start_only_record_is_unknown_not_still_running(self):
        service=self.query_fixture()
        self.store.begin('unfinished','model',dict(root='task:goal',task='worker',project_id='p',channel='desktop',source='task',model='demo',model_id='entry',model_key='entry:route',route='route'),now=12)
        value=service.query({'period':'all','kind':'model'})
        row=next(r for r in value['items']if r['id']=='unfinished')
        self.assertEqual(row['state'],'unknown');self.assertEqual(row['observed_state'],'running')
