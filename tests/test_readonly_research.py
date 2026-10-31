from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desk_companion.readonly_research import ReadonlyLoop,eligible_child
from desk_companion.research_tools import ToolOutcome
from desk_companion.task_budgets import Ledger,current,installed


SCHEMA=dict(type='function',read_only=True,function=dict(name='web_fetch',description='Read known source',
    parameters=dict(type='object',properties=dict(url=dict(type='string')),required=['url'],additionalProperties=False)))


def tool(ident):return dict(message=dict(tool_calls=[dict(id=ident,function=dict(name='web_fetch',arguments=json.dumps(dict(url='https://example.org/read'))))]))


class ReadonlyLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        context=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=self.tmp.name);context.start();self.addCleanup(context.stop)

    @unittest.skipUnless(os.name=='nt','first readonly runtime pilot requires Windows parent-exit protection')
    def test_only_independent_collect_children_with_supported_permissions_qualify(self):
        request=dict(kind='task',parent_run='parent',stage='collect',dependencies=[],role=dict(id='research',tools=['联网搜索','网页读取']))
        self.assertTrue(eligible_child(request))
        with patch('desk_companion.readonly_research.os.name','posix'):self.assertFalse(eligible_child(request))
        for changes in (dict(parent_run=''),dict(stage='deliver'),dict(dependencies=['earlier']),dict(kind='plan'),dict(role=dict(id='research',tools=['网页读取','终端']))):
            self.assertFalse(eligible_child({**request,**changes}))

    def test_actual_tool_ids_budget_context_and_repeated_reads_are_not_cached(self):
        ledger=Ledger();root=ledger.create('readonly','research');ledger.bind(root,'actual-child')
        values=iter([tool('first'),tool('second'),dict(message=dict(content='actual candidate'))]);seen=[]
        def chat(messages,tools=None,on_delta=None):
            call,_=ledger.reserve_call('actual-child',10)
            result=next(values);ledger.settle('actual-child',call,dict(prompt_tokens=10,completion_tokens=2));return result
        def invoke(name,args,ident):
            self.assertEqual(current().task,'actual-child');current().tool(True,name);seen.append(ident)
            return ToolOutcome(True,'actual body')
        loop=ReadonlyLoop(model_name='safe-model-name',schemas=[SCHEMA],messages=[dict(role='system',content='Read only')],
            prepare=lambda:[SCHEMA],chat=chat,invoke=invoke)
        output=io.StringIO()
        with installed('actual-child'),redirect_stdout(output):result=loop.run(6)
        self.assertEqual(result.status,'success');self.assertEqual(seen,['first','second'])
        self.assertEqual(ledger.view(root)['consumed']['tool_calls'],2);self.assertEqual(output.getvalue(),'')

    def test_configuration_dict_and_unknown_or_mutating_tools_are_rejected(self):
        arguments=dict(schemas=[SCHEMA],messages=[],prepare=lambda:[SCHEMA],chat=lambda *a,**k:None,invoke=lambda *a:None)
        with self.assertRaises(ValueError):ReadonlyLoop(model_name=dict(model='fake',api_key='credential-must-not-be-recorded'),**arguments)
        for name in ('workspace_write','delegate_to_partners','execute_skill','project_execute'):
            schema={**SCHEMA,'function':{**SCHEMA['function'],'name':name}}
            with self.assertRaises(ValueError):ReadonlyLoop(model_name='fake',**{**arguments,'schemas':[schema]})
        with self.assertRaises(ValueError):ReadonlyLoop(model_name='fake',**{**arguments,'schemas':[{**SCHEMA,'read_only':False}]})

    def test_failed_read_has_one_actual_attempt_without_atlas_retry(self):
        values=iter([tool('failed'),dict(message=dict(content='source unavailable'))]);seen=[]
        def invoke(*args):seen.append(args);raise TimeoutError('bounded read failure')
        loop=ReadonlyLoop(model_name='fake',schemas=[SCHEMA],messages=[],prepare=lambda:[SCHEMA],
            chat=lambda *a,**k:next(values),invoke=invoke)
        result=loop.run(5)
        self.assertEqual(len(seen),1);self.assertEqual(result.status,'success')  # Loop status is not product acceptance.
        self.assertTrue(any(row.get('role')=='tool'and 'bounded read failure'in row['content']for row in loop.messages))

    def test_round_limit_termination_is_not_a_final_answer(self):
        seen=[]
        loop=ReadonlyLoop(model_name='fake',schemas=[SCHEMA],messages=[],prepare=lambda:[SCHEMA],
            chat=lambda *a,**k:tool(str(len(seen))),invoke=lambda *a:(seen.append(a)or ToolOutcome(True,'body')))
        result=loop.run(3)
        self.assertEqual(result.status,'terminated');self.assertEqual(len(seen),2)

    def test_new_user_material_and_current_source_catalog_reach_next_request(self):
        calls=[];values=iter([tool('read'),dict(message=dict(content='updated candidate'))])
        def prepare():
            if calls:loop.messages.append(dict(role='user',content='new material and actual source S1'))
            return [SCHEMA]
        def chat(messages,tools=None,on_delta=None):calls.append(json.loads(json.dumps(messages)));return next(values)
        loop=ReadonlyLoop(model_name='fake',schemas=[SCHEMA],messages=[dict(role='system',content='root instruction')],
            prepare=prepare,chat=chat,invoke=lambda *a:ToolOutcome(True,'actual body'))
        self.assertEqual(loop.run(5).status,'success')
        self.assertTrue(any(row.get('content')=='new material and actual source S1'for row in calls[-1]))
