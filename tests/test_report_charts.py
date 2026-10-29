import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from desk_companion import report_charts as charts,web_sources
from desk_companion.collaboration_worker import execute,PublicFailure
from tests import test_execution_flow as flow

SOURCE=dict(kind='page',url='https://example.org/measure',title='Measured data',level='body',text='A=3, B=5, same protocol.',truncated=False)
SOURCE_ID=web_sources.source_catalog([SOURCE])[0]['id']
BAR=dict(type='bar',title='同口径测量',unit='ms',comparability='相同协议和样本的测量结果',source_ids=[SOURCE_ID],labels=['A','B'],series=[dict(name='耗时',values=[3,5])])
FLOW=dict(type='flow',title='证据到报告',source_ids=[],nodes=['读取原文','核对证据','写作与图表'],edges=[[0,1],[1,2]])


class ChartTests(unittest.TestCase):
    def test_screen_flow_layout_preserves_old_assets_and_uses_shared_positions(self):
        from desk_companion.report_flow import layout
        result=charts.create(FLOW,[],uuid.uuid4().hex)
        path=charts.folder()/(result['id']+'.json');before=path.read_bytes()
        public=charts.public(result['id'])
        self.assertEqual(public['flow_layout'],layout(FLOW))
        self.assertEqual([n['index'] for n in public['flow_layout']['nodes']],[0,1,2])
        self.assertEqual(path.read_bytes(),before)

    def test_flow_keeps_actual_snippet_level_without_upgrading_numeric_evidence(self):
        snippet={**SOURCE,'kind':'search','level':'snippet','truncated':True}
        spec={**FLOW,'source_ids':[SOURCE_ID]}
        result=charts.create(spec,[snippet],uuid.uuid4().hex)
        self.assertEqual(charts.public(result['id'])['sources'][0]['level'],'snippet')
        self.assertIn('搜索摘要，未读取正文',charts.export(result['markdown']))
        self.assertNotIn('正文节选',charts.export(result['markdown']))
        for numeric in (BAR,{**BAR,'type':'line'},dict(type='timeline',title='时间线',source_ids=[SOURCE_ID],items=[dict(label='日期',start='2026-10-29')])):
            with self.subTest(kind=numeric['type']),self.assertRaises(ValueError):charts.validate(numeric,[snippet])
        with self.assertRaises(ValueError):charts.validate({**FLOW,'source_ids':['guessed']},[snippet])

    def test_single_exit_decision_reports_the_actual_repair_without_rendering(self):
        root=uuid.uuid4().hex
        with self.assertRaisesRegex(ValueError,'至少两个出口.*process'):
            charts.create({**FLOW,'node_kinds':['process','decision','process'],'edge_labels':['输入','执行']},[],root)
        self.assertFalse((charts.folder()/'limits'/(root+'.json')).exists())

    def test_five_flow_layouts_keep_branch_conditions_lanes_and_feedback_in_exports(self):
        cases=[{**FLOW,'flow_style':'steps'},
            dict(type='flow',title='条件',source_ids=[],flow_style='decision',nodes=['阅读','证据充分？','形成结论','补读'],edges=[[0,1],[1,2],[1,3],[3,1]],edge_labels=['','充分','不足','重新核对']),
            dict(type='flow',title='并行',source_ids=[],flow_style='parallel',nodes=['目标','检索','核对','汇总'],edges=[[0,1],[0,2],[1,3],[2,3]]),
            {**FLOW,'flow_style':'swimlane','node_lanes':['tebb','研究助手','用户']},
            dict(type='flow',title='循环',source_ids=[],flow_style='cycle',nodes=['假设','验证','分析','调整'],edges=[[0,1],[1,2],[2,3],[3,0]])]
        for spec in cases:
            with self.subTest(style=spec['flow_style']):
                value=charts.create(spec,[],uuid.uuid4().hex);meta,png=charts.load(value['id'])
                self.assertEqual(meta['render_version'],2);self.assertTrue(png.startswith(b'\x89PNG'))
                text=charts.export(value['markdown'])
                for label in spec.get('edge_labels',[]):self.assertIn(label,text)
                for lane in spec.get('node_lanes',[]):self.assertIn(lane,text)
        for change in [dict(flow_style='code'),dict(flow_style='swimlane'),dict(node_kinds=['process']),dict(edge_labels=['多余']),dict(edges=[[0,1],[0,1]]),dict(node_lanes=['tebb'])]:
            with self.subTest(change=change),self.assertRaises(ValueError):charts.validate({**FLOW,**change},[])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=self.temp.name);p.start();self.addCleanup(p.stop)

    def test_four_types_generate_real_images_and_keep_data_sources(self):
        cases=[BAR,{**BAR,'type':'line','series':[dict(name='耗时',values=[3,None])]},FLOW,
            dict(type='timeline',title='时间安排',source_ids=[SOURCE_ID],items=[dict(label='阶段一',start='2026-10-29',end='2026-10-30'),dict(label='发布',start='2026-10-31')])]
        for spec in cases:
            root=uuid.uuid4().hex;result=charts.create(spec,[SOURCE],root);meta,raw=charts.load(result['id'])
            self.assertTrue(raw.startswith(b'\x89PNG'));self.assertIn(b'<svg',(charts.folder()/(result['id']+'.svg')).read_bytes())
            self.assertEqual(meta['spec'],spec);self.assertTrue(charts.public(result['id'])['image'].startswith('data:image/png;base64,'))
            charts.validate_report(result['markdown'],root,True)
            self.assertNotIn('api_key',json.dumps(meta))

    def test_invalid_source_numbers_graphs_and_foreign_assets_never_count_as_delivered(self):
        for change in [dict(source_ids=['guessed']),dict(source_ids=[]),dict(unit=''),dict(comparability=''),dict(series=[dict(name='x',values=[True,4])]),dict(series=[dict(name='x',values=[float('nan'),4])]),dict(labels=['A','A']),dict(code='import os')]:
            with self.subTest(change=change),self.assertRaises(ValueError):charts.validate({**BAR,**change},[SOURCE])
        with self.assertRaises(ValueError):charts.validate(BAR,[{**SOURCE,'level':'snippet'}])
        with self.assertRaises(ValueError):charts.validate({**FLOW,'edges':[[0,1],[1,0]]},[])
        with self.assertRaises(ValueError):charts.public('../../secret')
        result=charts.create(FLOW,[],uuid.uuid4().hex)
        with self.assertRaises(ValueError):charts.validate_report(result['markdown'],uuid.uuid4().hex)
        with self.assertRaises(ValueError):charts.validate_report('只有文字',required=True)
        (charts.folder()/(result['id']+'.png')).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):charts.load(result['id'])

    def test_budget_is_shared_across_workers_and_failed_render_does_not_fabricate_asset(self):
        root=uuid.uuid4().hex
        with patch.object(charts,'_render',side_effect=RuntimeError('renderer unavailable')):
            for _ in range(4):
                with self.assertRaises(RuntimeError):charts.create(FLOW,[],root)
        with self.assertRaisesRegex(ValueError,'最多生成4'):charts.create(FLOW,[],root)
        self.assertFalse(list(charts.folder().glob('*.json')))

    def test_note_markdown_has_registered_png_and_feishu_has_readable_data_fallback(self):
        from desk_companion import notebook
        from tests.test_notebook_service import CITES
        result=charts.create(BAR,[SOURCE],uuid.uuid4().hex)
        with patch('desk_companion.knowledge.public_view',return_value=dict(docs=[])):
            sid=notebook.new_notebook()['session_id'];nid=notebook.save_note(sid,'技术报告',result['markdown'],CITES)['note_id']
            exported=notebook.write_note_markdown(sid,nid);path=Path(exported['path']);text=path.read_text('utf-8')
            self.assertIn('![同口径测量]',text);self.assertIn('|A|3|',text)
            files=notebook._find_note(notebook._find(notebook._read(),sid),nid)['files']
            self.assertEqual(len(files),2);image=Path(files[1]['path']);self.assertTrue(image.is_file())
            notebook.delete_note(sid,nid);self.assertFalse(image.exists());self.assertFalse(path.exists())
            self.assertTrue(charts.load(result['id'])[1])  # original report remains independent
        plain=charts.export(result['markdown']);self.assertNotIn('tebb-chart',plain);self.assertIn('Measured data',plain)

    def test_registration_failure_keeps_data_and_reports_confirmed_file_stage(self):
        from desk_companion import video
        original=video.atomic_write
        def write(path,value):
            if path.parent==charts.folder():raise OSError('metadata registration failed')
            return original(path,value)
        with patch.object(video,'atomic_write',side_effect=write),self.assertRaises(charts.ChartChangeError) as failure:
            charts.create(FLOW,[],uuid.uuid4().hex)
        details=failure.exception.details
        self.assertEqual(details['stage'],'registration');self.assertEqual(details['confirmed_formats'],['png','svg']);self.assertEqual(details['data'],FLOW)
        self.assertTrue((charts.folder()/(details['chart_id']+'.png')).is_file())
        with self.assertRaises(OSError):charts.load(details['chart_id'])


class ChartWorkerTests(unittest.TestCase):
    setUp=flow.ExecutionFlowTests.setUp
    tearDown=flow.ExecutionFlowTests.tearDown
    state=flow.ExecutionFlowTests.state
    send=flow.ExecutionFlowTests.send
    finish=flow.ExecutionFlowTests.finish
    control=flow.ExecutionFlowTests.control

    def test_invalid_chart_parameters_do_not_exhaust_shared_render_budget(self):
        root=self.send('把刚才的讲解改为流程图')
        self.finish(root['id'],[self.control('start_background_task',instructions='做流程图',requires_charts=True)])
        self.service.tick();run=self.service.execution_status(root['id']);request=copy.deepcopy(self.work.requests[run['task']])
        request['limits']=dict(call_timeout=10,task_timeout=60)
        events=[];calls=[]
        class Model:
            def chat(_,messages,tools,on_delta):
                calls.append(1)
                if len(calls)==1:
                    invalid=[self.control('build_chart',**{**FLOW,'title':'待修正'+str(i),'source_ids':['guessed']})['tool_calls'][0] for i in range(4)]
                    return dict(message=dict(role='assistant',tool_calls=[{**row,'id':str(i)} for i,row in enumerate(invalid)]),usage={})
                if len(calls)==2:
                    self.assertTrue(all(not json.loads(m['content'])['ok'] for m in messages if m['role']=='tool'))
                    return dict(message=self.control('build_chart',**FLOW),usage={})
                value=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
                self.assertTrue(value['ok'],value.get('error'))
                return dict(message=dict(content='完整流程说明。\n'+value['markdown']),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=Model())
        charts.validate_report(next(d for k,d in events if k=='result'),root['id'],True)
        budget=json.loads((charts.folder()/'limits'/(root['id']+'.json')).read_text('utf-8'))
        self.assertEqual(budget['attempts'],1)
        self.assertEqual(len(calls),3)

    def test_real_worker_tool_receipt_is_rendered_into_original_root_and_required_flag_survives(self):
        root=self.send('做一张流程图')
        self.finish(root['id'],[self.control('start_background_task',instructions='做流程图',requires_charts=True)])
        self.service.tick();run=self.service.execution_status(root['id']);request=copy.deepcopy(self.work.requests[run['task']]);request['limits']=dict(call_timeout=10,task_timeout=60)
        events=[]
        class Model:
            def chat(_,messages,tools,on_delta):
                self.assertIn('build_chart',[t['function']['name'] for t in tools])
                if not any(m['role']=='tool' for m in messages):return dict(message=self.control('build_chart',**FLOW),usage={})
                result=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
                return dict(message=dict(content='流程说明。\n'+result['markdown']),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=Model())
        answer=next(d for k,d in events if k=='result');self.assertIn('tebb-chart',answer)
        charts.validate_report(answer,root['id'],True)
        self.assertTrue(request['requires_charts']);self.assertTrue(any(k=='tool_end' and d.get('status')=='success' for k,d in events))
        class NoChart:
            def chat(_,messages,tools,on_delta):return dict(message=dict(content='完成，但没有图。'),usage={})
        with self.assertRaisesRegex(PublicFailure,'图表交付未确认'):execute(request,lambda *_:None,llm=NoChart())

    def test_missing_chart_gets_one_bounded_correction_then_real_receipt(self):
        root=self.send('报告必须有图表')
        self.finish(root['id'],[self.control('start_background_task',instructions='报告含图',requires_charts=True)])
        self.service.tick();run=self.service.execution_status(root['id']);request=copy.deepcopy(self.work.requests[run['task']]);request['limits']=dict(call_timeout=10,task_timeout=60)
        events=[];calls=[]
        class Model:
            def chat(_,messages,tools,on_delta):
                calls.append(1)
                if len(calls)==1:return dict(message=dict(content='已有文字稿，但漏图。'),usage={})
                if len(calls)==2:return dict(message=self.control('build_chart',**FLOW),usage={})
                value=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
                return dict(message=dict(content='修订的完整说明。\n'+value['markdown']),usage={})
        execute(request,lambda k,d:events.append((k,d)),llm=Model())
        self.assertEqual(len(calls),3);self.assertIn(('answer_reset',True),events)
        charts.validate_report(next(d for k,d in events if k=='result'),root['id'],True)
