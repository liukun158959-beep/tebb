"""Routing must produce real execution requests, never fabricated receipts."""
import copy
import json
import unittest
from desk_companion.collaboration_worker import execute, PublicFailure
from tests import test_execution_flow as flow


class HandoffGuardTests(unittest.TestCase):
    setUp=flow.ExecutionFlowTests.setUp
    tearDown=flow.ExecutionFlowTests.tearDown
    state=flow.ExecutionFlowTests.state
    finish=flow.ExecutionFlowTests.finish
    control=flow.ExecutionFlowTests.control

    def test_real_auto_path_repairs_fabricated_handoff_before_marking_success(self):
        root=self.service.action(dict(type='send',conversation='dm-tebb',text='帮我搜索下最近比较火的AI技术资讯',mode='auto',request_id='auto-search-guard'))['state']['runs'][-1]
        self.assertTrue(root['require_execution'])
        from desk_companion.collaboration import transaction
        # Also retain coverage for existing auto/False records from before the fix.
        with transaction(self.service.path) as (state,_):state['runs'][-1]['require_execution']=False
        run,events,_=self.finish(root['id'],[dict(content='已把这轮目标提交给后台协作流程。'),self.control('start_background_task',instructions=root['title'],strategy='direct')])
        self.assertEqual(run['kind'],'task');self.assertEqual(run['state'],'pending');self.assertEqual(run['calls'],2)
        self.assertEqual(len(self.state()['runs']),1);self.assertFalse(any(k=='tool_start' for k,d in events))
        self.assertIn(('answer_reset',True),events)

    def test_literal_search_commits_to_execution_and_does_not_capture_explanations(self):
        from desk_companion.execution_flow import explicit_search
        for text in ('帮我搜索下最近的AI技术资讯','请联网搜索Python','search latest AI news'):
            self.assertTrue(explicit_search(text))
        for text in ('解释搜索算法','如何做联网搜索','写一段搜索算法示例'):
            self.assertFalse(explicit_search(text))
        root=self.service.action(dict(type='send',conversation='dm-tebb',text='帮我搜索最近AI技术资讯',mode='auto',request_id='literal-search-guard'))['state']['runs'][-1]
        self.assertTrue(root['require_execution'])
        self.service.tick();raw=self.service.execution_status(root['id']);request=copy.deepcopy(self.chat.requests[raw['task']]);request['limits']=dict(call_timeout=10,task_timeout=30)
        class Model:
            def chat(_,messages,tools,on_delta):return dict(message=dict(content='最近AI很热门。'),usage={})
        with self.assertRaises(PublicFailure):execute(request,lambda *_:None,llm=Model())
        self.assertFalse(self.work.items)

    def test_repeated_fabricated_handoff_has_bounded_failure_and_no_work(self):
        from desk_companion.collaboration import parse_route
        with self.assertRaises(ValueError):parse_route(json.dumps(dict(mode='answer',text='I have submitted the background task.')))
        root=self.service.action(dict(type='send',conversation='dm-tebb',text='搜索资讯',mode='auto',request_id='auto-no-handoff'))['state']['runs'][-1]
        self.service.tick();raw=self.service.execution_status(root['id']);request=copy.deepcopy(self.chat.requests[raw['task']]);request['limits']=dict(call_timeout=10,task_timeout=30)
        events=[]
        class Model:
            def chat(_,messages,tools,on_delta):return dict(message=dict(content='已将任务提交给后台。'),usage={})
        with self.assertRaisesRegex(PublicFailure,'口头声称提交'):
            execute(request,lambda k,d:events.append((k,d)),llm=Model())
        self.assertEqual(sum(k=='llm_start' for k,d in events),2);self.assertFalse(self.work.items)
        self.assertFalse(any(k=='result' for k,d in events))


if __name__=='__main__':unittest.main()
