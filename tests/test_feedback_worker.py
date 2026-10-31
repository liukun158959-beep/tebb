import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from desk_companion.collaboration_worker import execute,PublicFailure
from desk_companion.web_sources import source_catalog


class ScriptedModel:
    def __init__(self,drafts,assessments=None):
        self.drafts=iter(drafts);self.assessments=iter(assessments or []);self.calls=[]

    def chat(self,messages,tools=None,on_delta=None):
        self.calls.append(dict(messages=copy.deepcopy(messages),tools=copy.deepcopy(tools)))
        if messages[0]['content'].startswith('反馈审阅协议'):
            rows=json.loads(messages[1]['content'])['materials']
            row=next((r for r in rows if r['level']=='body'),rows[0])
            status=next(self.assessments,'supported')
            value=dict(requirements=[dict(requirement='current requirement',dimension='claim_support'if row['level']=='body'else'task_acceptance',
                status=status,reason='controlled independent assessment',evidence=[dict(source_id=row['id'],quote=row['segments'][0][:500])])])
            return dict(message=dict(content=json.dumps(value)),usage=dict(prompt_tokens=5,completion_tokens=3))
        value=next(self.drafts)
        if isinstance(value,str):value=dict(content=value)
        return dict(message=value,usage=dict(prompt_tokens=10,completion_tokens=4))


def delivery(text):
    return dict(tool_calls=[dict(id=uuid.uuid4().hex,function=dict(name='submit_deliverable',arguments=json.dumps(dict(report=text,complete=True,missing=[]))))])


class FeedbackWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for context in [patch.dict(os.environ,{'DESK_COMPANION_DATA_DIR':self.temp.name}),
                        patch('desk_companion.model_catalog.require_item',return_value=dict(id='fixture',api_key='test-only',base_url='https://example.invalid/v1',model='fake')),
                        patch('desk_companion.collaboration_worker.updates',return_value=[]),
                        patch('desk_companion.collaboration_worker.selected_materials',return_value={})]:
            context.start();self.addCleanup(context.stop)
        self.request=dict(kind='task',role=dict(id='writer',name='writer',role='write',tools=[]),text='只输出JSON。',
            collab_run=uuid.uuid4().hex,collab_path=str(Path(self.temp.name)/'workspace.sqlite3'),conversation='test',
            model_id='fixture',limits=dict(call_timeout=10,task_timeout=30),budget=6,context=[])
        self.request['task_id']=uuid.uuid4().hex
        self.events=[]

    def run_model(self,model):execute(self.request,lambda k,d:self.events.append((k,d)),llm=model)

    def test_structured_citation_and_real_review_have_separate_checks(self):
        source=dict(kind='page',level='body',url='https://example.org/source',text='Launch was in 2020.')
        self.request.update(text='只输出JSON对象，含year和citation来源。',source_evidence=[source])
        ident=source_catalog([source])[0]['id']
        self.run_model(ScriptedModel([json.dumps(dict(year=2020,citation=f'[{ident}]'))]))
        result=json.loads(self.events[-1][1]);self.assertEqual(set(result),{'year','citation'})
        report=next(d for k,d in self.events if k=='feedback_check')
        self.assertEqual(report['completion'],'assessed')
        self.assertTrue(any(c['dimension']=='source_url'and c['basis']=='deterministic'for c in report['checks']))
        self.assertTrue(any(c['dimension']=='claim_support'and c['basis']=='model_assessment'for c in report['checks']))

    def test_parent_format_repair_keeps_only_json_and_counts_review_call(self):
        from desk_companion.agent_evals import CASES
        self.request.update(stage='deliver',objective=next(c['prompt']for c in CASES if c['id']=='delegate_deliver'))
        model=ScriptedModel([delivery('# 报告\n{"total":18,"checked":true}'),delivery('{"total":18,"checked":true}')])
        self.run_model(model)
        self.assertEqual(json.loads(self.events[-1][1]),dict(total=18,checked=True))
        self.assertEqual(len(model.calls),3)
        self.assertEqual(sum(k=='llm_start'for k,d in self.events),3)
        self.assertEqual(model.calls[-1]['tools'],[])

    def test_unsupported_claim_can_only_receive_one_correction(self):
        source=dict(kind='page',level='body',url='https://example.org/source',text='Launch was in 2020.')
        self.request.update(text='报告启动年份并引用来源。',source_evidence=[source])
        model=ScriptedModel(['2019年启动 [来源](https://example.org/source)','2020年启动 [来源](https://example.org/source)'],['contradicted','supported'])
        self.run_model(model)
        self.assertEqual(len(model.calls),4)
        reports=[d for k,d in self.events if k=='feedback_check']
        self.assertEqual([r['ok']for r in reports],[False,True])

    def test_review_uses_existing_call_budget_and_preserves_partial_answer(self):
        self.request.update(stage='deliver',budget=1)
        model=ScriptedModel([delivery('{"done":true}')])
        with self.assertRaises(PublicFailure):self.run_model(model)
        self.assertEqual(len(model.calls),1)
        self.assertTrue(any(k=='token'and v=='{"done":true}'for k,v in self.events))
        self.assertEqual(next(d for k,d in self.events if k=='feedback_check')['completion'],'unverified')

    def test_user_json_data_is_not_mistaken_for_route_control(self):
        self.request.update(kind='route',text='只输出JSON：total=18。')
        self.run_model(ScriptedModel(['{"total":18}']))
        result=json.loads(self.events[-1][1])
        self.assertEqual(result,dict(mode='answer',text='{"total":18}'))

    def test_unknown_create_is_not_replayed_during_correction(self):
        self.request.update(text='准备公开项目。只输出JSON。',role=dict(id='writer',name='writer',role='write',tools=['项目环境']),budget=8)
        def prepare(ident):return dict(tool_calls=[dict(id=ident,function=dict(name='project_prepare',arguments=json.dumps(dict(repository='https://github.com/example/project'))))])
        model=ScriptedModel([prepare('first'),'{"ready":true}',prepare('repeat'),'{"ready":false}'],['contradicted','unverified'])
        unknown=dict(ok=False,operation=dict(id='original-operation',state='unknown'),environment=dict(id='original-environment',state='unknown'),log_id='original-log')
        with patch('desk_companion.project_tools.call',return_value=unknown)as rpc:
            with self.assertRaises(PublicFailure):self.run_model(model)
        self.assertEqual(rpc.call_count,1)
        self.assertTrue(any(k=='repair_tool_blocked'and d['tool']=='project_prepare'for k,d in self.events))
        reports=[d for k,d in self.events if k=='feedback_check']
        self.assertTrue(all(r['unknown_operations'][0]['operation_id']=='original-operation'for r in reports))

    def test_execution_capable_task_cannot_skip_review_by_omitting_tools(self):
        self.request.update(role=dict(id='writer',name='writer',role='write',tools=['项目环境']))
        model=ScriptedModel(['{"ready":true}','{"ready":true}'],['unverified','unverified'])
        with self.assertRaises(PublicFailure):self.run_model(model)
        self.assertEqual(len(model.calls),4)

    def test_update_during_review_invalidates_old_approval_and_format(self):
        self.request.update(stage='deliver',text='计算7和11的总和。',objective='计算7和11的总和。')
        received=[]
        class UpdatingModel(ScriptedModel):
            def chat(inner,messages,tools=None,on_delta=None):
                value=super().chat(messages,tools,on_delta)
                if messages[0]['content'].startswith('反馈审阅协议')and not received:received.append('最终只返回JSON。')
                return value
        model=UpdatingModel([delivery('总和是18。'),delivery('{"total":18}')])
        with patch('desk_companion.collaboration_worker.updates',side_effect=lambda *_:received.copy()):self.run_model(model)
        self.assertTrue(any(k=='feedback_invalidated'for k,d in self.events))
        self.assertEqual(json.loads(self.events[-1][1]),dict(total=18));self.assertEqual(len(model.calls),4)

    def test_failed_repair_generation_retains_prior_candidate_without_success(self):
        self.request.update(stage='deliver')
        class BrokenRepair(ScriptedModel):
            def chat(inner,messages,tools=None,on_delta=None):
                if len(inner.calls)==2:raise RuntimeError('controlled provider failure')
                return super().chat(messages,tools,on_delta)
        model=BrokenRepair([delivery('{"total":19}')],['contradicted'])
        with self.assertRaisesRegex(RuntimeError,'controlled'):self.run_model(model)
        self.assertFalse(any(k=='result'for k,d in self.events))
        self.assertEqual(self.events[-1],('token','{"total":19}'))


if __name__=='__main__':unittest.main()
