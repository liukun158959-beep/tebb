"""Readiness-to-delivery integration with actual workers and durable roots."""
import copy
import json
import time
import unittest
import uuid
from unittest.mock import patch

from desk_companion.collaboration import transaction
from desk_companion.collaboration_worker import execute, PublicFailure
from tests import test_collaboration as fixture


class ExecutionFlowTests(unittest.TestCase):
    setUp=fixture.CollaborationTests.setUp
    tearDown=fixture.CollaborationTests.tearDown
    state=fixture.CollaborationTests.state

    def send(self,text='test',**extra):
        return self.service.action(dict(type='send',conversation='dm-tebb',text=text,mode='task',request_id=uuid.uuid4().hex,**extra))['state']['runs'][-1]

    def finish(self,run_id,messages):
        self.service.tick();run=self.service.execution_status(run_id)
        manager=self.service._manager(run);request=copy.deepcopy(manager.requests[run['task']])
        request['limits']=dict(call_timeout=10,task_timeout=30)
        events=[];responses=iter(messages);tools=[]
        class LLM:
            def chat(_,history,tools=None,on_delta=None):
                names=[t['function']['name'] for t in tools or []]
                _.names=names
                return dict(message=next(responses),usage={})
        llm=LLM();execute(request,lambda k,d:events.append((k,d)),llm=llm)
        task=manager.items[run['task']]
        task.update(state='succeeded',answer=next(d for k,d in reversed(events) if k=='result'),events=[dict(seq=i+1,kind=k,data=d) for i,(k,d) in enumerate(events)])
        self.service.tick()
        return self.service.execution_status(run_id),events,llm.names

    def control(self,name,**arguments):
        return dict(role='assistant',tool_calls=[dict(id='control',type='function',function=dict(name=name,arguments=json.dumps(arguments)))])

    def test_unclear_goal_pauses_with_zero_business_tools_and_resumes_same_root(self):
        root=self.send()
        run,events,names=self.finish(root['id'],[self.control('request_clarification',question='请说明要测试哪个功能，以及预期结果。')])
        self.assertEqual(run['state'],'needs_input');self.assertEqual(run['calls'],1)
        self.assertFalse(self.work.items);self.assertFalse(any(k=='tool_start' for k,d in events))
        self.assertEqual(set(names),{'start_background_task','request_clarification'})
        for _ in range(3):self.service.tick()
        self.assertEqual(len(self.chat.items),1)
        self.service.action(dict(type='send',conversation='dm-tebb',run=root['id'],text='把 Hello 改成小写并给出结果',request_id='resume-goal-one'))
        run,_,_=self.finish(root['id'],[self.control('start_background_task',instructions='把Hello改成小写',strategy='direct')])
        self.assertEqual(run['kind'],'task');self.assertEqual(run['state'],'pending')
        run,_,_=self.finish(root['id'],[dict(content='hello')])
        self.assertEqual(run['state'],'succeeded');self.assertEqual(run['calls'],3)
        self.assertEqual(run['chain']['calls'],3);self.assertEqual(len(self.state()['runs']),1)
        self.assertEqual(run['artifact'],'hello')
        self.assertEqual([m['sender'] for m in self.state()['messages'][-2:]],['user','tebb'])

    def test_clear_simple_goal_runs_directly_without_plan_or_self_child(self):
        root=self.send('把 Hello 改成小写')
        self.finish(root['id'],[self.control('start_background_task',instructions='仅返回hello',strategy='direct')])
        run,_,_=self.finish(root['id'],[dict(content='hello')])
        self.assertEqual(run['state'],'succeeded');self.assertEqual(len(self.state()['runs']),1)
        self.assertEqual(run['chain']['children'],[])
        self.assertEqual([p['kind'] for p in run['phases']],['route'])

    def test_self_delegation_is_rejected_before_any_child_or_business_work(self):
        root=self.send('分工研究任务')
        self.finish(root['id'],[self.control('start_background_task',instructions='分工',strategy='delegate')])
        self.service.tick();run=self.service.execution_status(root['id'])
        plan=json.dumps(dict(tasks=[dict(agent_id='tebb',title='自己做',instructions='自己做',depends_on=None)]))
        self.work.complete(run['task'],plan);self.service.tick()
        self.assertEqual(len(self.state()['runs']),1)
        self.assertEqual(self.service.execution_status(root['id'])['state'],'failed')

    def test_delegated_failure_cannot_be_hidden_by_successful_planning_or_cancelled_final(self):
        root=self.send('研究明确主题并总结')
        self.finish(root['id'],[self.control('start_background_task',instructions='研究明确主题并总结',strategy='delegate')])
        self.service.tick();run=self.service.execution_status(root['id'])
        plan=json.dumps(dict(tasks=[dict(agent_id='research',title='核对主题',instructions='核对主题一手来源',depends_on=None)]))
        self.work.complete(run['task'],plan);self.service.tick();self.service.tick()
        child=next(r for r in self.state()['runs'] if r.get('parent')==root['id'])
        child=self.service.execution_status(child['id'])
        self.work.complete(child['task'],'部分来源',state='failed');self.service.tick();self.service.tick()
        saved=self.service.execution_status(root['id'])
        self.assertEqual(saved['chain']['state'],'failed');self.assertEqual(saved['chain']['calls'],3)
        self.assertEqual(len(self.state()['runs']),2);self.assertNotIn('artifact',saved)

    def test_waiting_goal_survives_restart_and_can_be_cancelled_without_resubmission(self):
        from desk_companion.collaboration import Collaboration
        root=self.send()
        self.finish(root['id'],[self.control('request_clarification',question='具体目标是什么？')])
        fresh=Collaboration(self.service.path,managers=self.service.managers,start=False)
        try:
            fresh.tick();self.assertEqual(fresh.execution_status(root['id'])['state'],'needs_input')
            fresh.action(dict(type='cancel',id=root['id'],request_id='cancel-awaiting-goal'))
            fresh.tick();self.assertEqual(fresh.execution_status(root['id'])['state'],'cancelled')
            self.assertFalse(self.work.items)
        finally:fresh.shutdown()

    def test_public_projection_is_stable_and_does_not_poll_on_clock_changes(self):
        self.send();before=self.service.snapshot()
        with patch('desk_companion.execution_flow.time.time',return_value=time.time()+100):after=self.service.snapshot(since=before['sync'])
        self.assertEqual(after['patch']['collections'],{})


if __name__=='__main__':unittest.main()
