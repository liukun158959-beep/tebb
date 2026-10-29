import copy,json,uuid,unittest
from unittest.mock import patch
from tests import test_report_charts as fixture
from desk_companion import report_charts as charts,chart_building as building
from desk_companion.chart_schema import ChartInputError
from desk_companion.collaboration import migrate_companions,seed
from tests import test_execution_flow as execution_fixture
from desk_companion.collaboration_worker import execute


class BuildingTests(unittest.TestCase):
    setUp=fixture.ChartTests.setUp
    def test_preflight_reports_typo_length_and_feedback_together(self):
        spec={**fixture.FLOW,'calification':'说明','edges':[[0,1],[1,2],[2,1]],'edge_labels':['输入'],'flow_style':'steps'}
        with self.assertRaises(ChartInputError) as failure:charts.validate(spec,[])
        issues=failure.exception.details['issues'];paths={r['path'] for r in issues}
        self.assertTrue({'/calification','/edge_labels','/flow_style'}<=paths)
        self.assertIn('calculation',next(r['hint'] for r in issues if r['path']=='/calification'))
        self.assertEqual(spec['edges'],[[0,1],[1,2],[2,1]])

    def test_two_targeted_repairs_persist_and_success_is_reused(self):
        run=uuid.uuid4().hex;events=[];emit=lambda k,d:events.append((k,d))
        with self.assertRaises(ChartInputError) as first:building.build({**fixture.FLOW,'calification':'错字'},[],run,emit)
        draft=first.exception.details['draft_id'];self.assertEqual(first.exception.details['repairs_remaining'],2)
        with self.assertRaises(ChartInputError):building.build({**fixture.FLOW,'draft_id':draft,'edge_labels':['数量不匹配']},[],run,emit)
        good=building.build({**fixture.FLOW,'draft_id':draft},[],run,emit)
        with patch.object(charts,'_render',side_effect=AssertionError('must reuse')):
            again=building.build({**fixture.FLOW,'draft_id':draft},[],run,emit)
        self.assertEqual(good['id'],again['id']);self.assertTrue(again['reused'])
        self.assertEqual(json.loads((charts.folder()/'limits'/(run+'.json')).read_text('utf-8'))['attempts'],1)
        self.assertEqual([d['stage'] for k,d in events],['preflight_failed','preflight_failed','validated','built','reused'])

    def test_exhausted_repairs_and_foreign_draft_never_render(self):
        run=uuid.uuid4().hex;draft=None
        for _ in range(3):
            with self.assertRaises(ChartInputError) as failure:building.build({**fixture.FLOW,'calification':'错',**({'draft_id':draft} if draft else {})},[],run,lambda *_:None)
            draft=failure.exception.details['draft_id']
        self.assertFalse(failure.exception.details['repairable'])
        with self.assertRaisesRegex(building.ChartBuildError,'两轮'):building.build({**fixture.FLOW,'draft_id':draft},[],run,lambda *_:None)
        with self.assertRaisesRegex(building.ChartBuildError,'不属于'):building.build({**fixture.FLOW,'draft_id':draft},[],uuid.uuid4().hex,lambda *_:None)
        self.assertFalse(list(charts.folder().glob('*.png')))

    def test_uncertain_registration_keeps_original_identity_and_blocks_rebuild(self):
        run=uuid.uuid4().hex
        with patch.object(charts,'create',side_effect=charts.ChartChangeError('registration','a'*32,['png','svg'],fixture.FLOW)):
            with self.assertRaises(building.ChartBuildError) as failure:building.build(fixture.FLOW,[],run,lambda *_:None)
        draft=failure.exception.details['draft_id']
        with patch.object(charts,'create',side_effect=AssertionError('must not rebuild')):
            with self.assertRaisesRegex(building.ChartBuildError,'未确认'):building.build({**fixture.FLOW,'draft_id':draft},[],run,lambda *_:None)

    def test_spent_render_budget_is_not_reported_as_repairable_or_uncertain(self):
        run=uuid.uuid4().hex;charts.change_budget(run,lambda value:value.update(attempts=4));events=[]
        with self.assertRaises(building.ChartBuildError) as failure:building.build(fixture.FLOW,[],run,lambda k,d:events.append((k,d)))
        self.assertEqual(failure.exception.details['stage'],'budget');self.assertFalse(failure.exception.details['repairable'])
        self.assertEqual(events[-1][1]['stage'],'exhausted');self.assertFalse(list(charts.folder().glob('*.png')))

    def test_chart_partner_migrates_once_preserving_user_models_and_existing_groups(self):
        state=seed();state['agents']=[a for a in state['agents'] if a['id']!='chart'];state['conversations']=[c for c in state['conversations'] if c['id']!='dm-chart'];state['companion_version']=3
        state['agents'][0]['model']='my-model';state['agents'][1]['memory']='保留偏好';before=copy.deepcopy(state['conversations'])
        migrate_companions(state);migrate_companions(state)
        helpers=[a for a in state['agents'] if a['id']=='chart'];self.assertEqual(len(helpers),1)
        self.assertEqual(helpers[0]['tools'],['构建图表']);self.assertEqual(helpers[0]['model'],'my-model')
        self.assertEqual(state['agents'][1]['memory'],'保留偏好');self.assertEqual(state['conversations'][:-1],before)

    def test_full_partner_roster_preserves_custom_agents_and_reports_skipped_default(self):
        state=seed();state['agents']=[a for a in state['agents'] if a['id']!='chart'];state['agents'] += [dict(id='custom'+str(i),name='自定义'+str(i),tools=[],model='',memory='保留',avatar='owl',personality='') for i in range(5)]
        state['companion_version']=3;before=copy.deepcopy(state['agents']);migrate_companions(state)
        self.assertEqual(state['agents'],before);self.assertIn('已满8位',state['notice'])


class BuildingWorkerTests(unittest.TestCase):
    setUp=execution_fixture.ExecutionFlowTests.setUp
    tearDown=execution_fixture.ExecutionFlowTests.tearDown
    state=execution_fixture.ExecutionFlowTests.state
    send=execution_fixture.ExecutionFlowTests.send
    finish=execution_fixture.ExecutionFlowTests.finish
    control=execution_fixture.ExecutionFlowTests.control

    def test_direct_partner_real_worker_correction_is_visible_in_same_task(self):
        root=self.service.action(dict(type='send',conversation='dm-chart',text='用流程图解释当前材料',mode='auto',request_id=uuid.uuid4().hex))['state']['runs'][-1]
        self.finish(root['id'],[self.control('start_background_task',instructions='用流程图解释当前材料',requires_charts=True)])
        self.service.tick();run=self.service.execution_status(root['id']);request=copy.deepcopy(self.work.requests[run['task']]);request['limits']=dict(call_timeout=10,task_timeout=60)
        self.assertEqual(request['members'],[]);self.assertEqual(request['role']['tools'],['构建图表'])
        events=[];calls=[];broken={**fixture.FLOW,'calification':'说明','edges':[[0,1],[1,2],[2,1]],'edge_labels':['输入'],'flow_style':'steps'}
        class Model:
            def chat(_,messages,tools,on_delta):
                calls.append(1);self.assertEqual([t['function']['name'] for t in tools],['build_chart'])
                if len(calls)==1:return dict(message=self.control('build_chart',**broken),usage={})
                last=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
                if len(calls)==2:
                    self.assertTrue({'/calification','/edge_labels','/flow_style'}<={r['path'] for r in last['issues']})
                    return dict(message=self.control('build_chart',**{**fixture.FLOW,'edges':broken['edges'],'edge_labels':['输入','执行','反馈'],'flow_style':'cycle','draft_id':last['draft_id']}),usage={})
                return dict(message=dict(content='关系示意，非数值比较。\n'+last['markdown']),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=Model())
        self.work.items[run['task']].update(state='succeeded',answer=next(d for k,d in events if k=='result'),events=[dict(seq=i+1,kind=k,data=d) for i,(k,d) in enumerate(events)])
        self.service.tick();saved=self.service.execution_status(root['id']);self.assertEqual(saved['state'],'succeeded')
        draft=next(iter(saved['chart_builds'].values()));self.assertEqual(draft['stage'],'built')
        self.assertEqual([r['stage'] for r in draft['history']],['preflight_failed','validated','built'])
        self.assertEqual(saved['tool_calls'],2);charts.validate_report(saved['answer'],root['id'],True)

    def test_tebb_can_delegate_chart_partner_and_cancel_original_chain(self):
        root=self.send('请让图表助手把材料画成图')
        self.finish(root['id'],[self.control('start_background_task',instructions='图表助手画图',strategy='delegate',requires_charts=True)])
        plan=dict(tasks=[dict(agent_id='chart',title='组织流程图',instructions='仅根据已有材料画图',depends_on=None)])
        self.finish(root['id'],[self.control('submit_plan',**plan)])
        self.service.tick();self.service.tick();child=next(r for r in self.state()['runs'] if r.get('parent')==root['id'])
        internal=self.service.execution_status(child['id'])
        self.assertEqual(child['agent'],'chart');self.assertEqual(self.work.requests[internal['task']]['members'],[])
        self.service.action(dict(type='cancel',id=root['id'],request_id=uuid.uuid4().hex));self.service.tick()
        self.assertNotIn(self.service.execution_status(child['id'])['state'],{'pending','queued','running','waiting'})
