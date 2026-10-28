"""Real shared workspace, scheduler and product adapters; no model or Feishu writes."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from desk_companion.collaboration import Collaboration, transaction
from desk_companion.work_board import WorkBoardService
from desk_companion.work_execution import WorkExecutionService
from desk_companion.automation_service import AutomationService
from desk_companion import automation as a
from tests.test_collaboration import Queue, MODEL


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=temp.name);env.start();self.addCleanup(env.stop)
        for name in ('require_active','require_item'):
            mock=patch('desk_companion.model_catalog.'+name,return_value=MODEL);mock.start();self.addCleanup(mock.stop)
        self.work,self.chat=Queue(),Queue()
        self.c=Collaboration(Path(temp.name)/'workspace.sqlite3',managers=dict(work=self.work,chat=self.chat),start=False)
        self.addCleanup(self.c.shutdown)
        self.b=WorkBoardService(self.c.path)
        self.executor=WorkExecutionService(self.b,self.c)

    def item(self,**fields):
        result=self.b.change(dict(type='create',request_id=uuid.uuid4().hex,fields=dict(title='处理事项',**fields)))
        self.assertTrue(result['ok'],result);return result['items'][-1]

    def execute(self,item,**extra):
        action=dict(type='execute',request_id=uuid.uuid4().hex,id=item['id'],version=item['version'],**extra)
        return self.executor.execute(action),action

    def test_owner_selection_never_starts_work_and_execution_is_atomic_idempotent(self):
        item=self.item();self.assertEqual(item['agent_id'],'tebb')
        self.assertEqual(self.c.snapshot()['state']['runs'],[])
        result,action=self.execute(item);self.assertTrue(result['ok'],result)
        runid=result['receipt']['run_id'];run=self.c.execution_status(runid)
        self.assertEqual(run['source']['id'],item['id']);self.assertEqual(run['agent'],'tebb')
        self.assertEqual(run['kind'],'route')
        self.assertTrue(self.executor.execute(action)['ok'])
        self.assertEqual(len(self.c.snapshot()['state']['runs']),1)
        self.assertFalse(self.execute(result['items'][0])[0]['ok'])
        self.assertEqual(result['items'][0]['state'],'todo')
        self.assertEqual(result['items'][0]['links'][0]['id'],runid)

    def test_specialist_owner_does_not_plan_and_archived_and_stale_requests_reject(self):
        item=self.item(agent_id='writer')
        legacy=self.b.change(dict(type='save',id=item['id'],version=item['version'],request_id=uuid.uuid4().hex,fields=dict(title='旧客户端编辑')))
        self.assertTrue(legacy['ok'],legacy);item=legacy['items'][0];self.assertEqual(item['agent_id'],'writer')
        result,_=self.execute(item)
        self.assertEqual(self.c.execution_status(result['receipt']['run_id'])['kind'],'route')
        other=self.item(agent_id='research')
        changed=self.b.change(dict(type='archive',id=other['id'],version=other['version'],archived=True,request_id=uuid.uuid4().hex))
        self.assertTrue(changed['ok'],changed);self.assertFalse(self.execute(other)[0]['ok'])
        archived=next(i for i in changed['items'] if i['id']==other['id'])
        self.assertFalse(self.execute(archived)[0]['ok'])
        self.assertFalse(self.b.change(dict(type='create',request_id=uuid.uuid4().hex,fields=dict(title='x',agent_id='gone')))['ok'])

    def test_tebb_delegates_only_to_specialists_and_keeps_final_in_root(self):
        accepted=self.c.submit_execution('tebb','研究并总结明确主题',project_id='',source=dict(kind='test',id='one'),request_id='execution-test-one')
        self.c.tick();root=self.c.execution_status(accepted['run_id'])
        self.chat.complete(root['task'],json.dumps(dict(mode='task',instructions=root['input'],strategy='delegate')));self.c.tick();self.c.tick()
        root=self.c.execution_status(root['id'])
        plan=json.dumps(dict(tasks=[dict(agent_id='research',title='核实来源',instructions='核对一手资料并返回摘要',depends_on=None)]))
        self.work.complete(root['task'],plan);self.c.tick()
        state=self.c.snapshot()['state'];children=[r for r in state['runs'] if r.get('parent')==root['id']]
        self.assertEqual(len(children),1);self.assertEqual(children[0]['agent'],'research')
        saved=self.c.execution_status(root['id']);self.assertEqual(saved['kind'],'task');self.assertEqual(saved['state'],'waiting')
        self.c.action(dict(type='cancel',id=root['id'],conversation=accepted['conversation'],request_id=uuid.uuid4().hex))
        self.assertTrue(all(r['state'] in {'cancelled','cancelling'} for r in self.c.snapshot()['state']['runs']))

    def test_execution_saved_then_refresh_failure_reports_committed(self):
        item=self.item()
        with patch.object(self.b,'load',return_value=dict(ok=False,error='read unavailable')):
            result,action=self.execute(item)
        self.assertEqual(result['change_state'],'committed');self.assertEqual(result['stage'],'refresh')
        recovered=self.b.load(action['request_id']);self.assertEqual(len(recovered['items'][0]['links']),1)

    def automation(self):
        s=AutomationService(execution=lambda:self.c,busy=lambda:True)
        self.addCleanup(lambda:s.stop(1));return s

    def save_job(self,s,**extra):
        payload=dict(name='通用任务',action='tebb_task',enabled=False,cadence='daily',weekdays=[],hour=9,minute=0,doc='',goal='整理项目资料',agent_id='tebb',project_id='')
        payload.update(extra)
        result=s.save_automation_job(payload);self.assertTrue(result['ok'],result);return result['job_id'],payload

    def test_generic_automation_records_real_execution_and_reuses_submission(self):
        s=self.automation();jid,_=self.save_job(s)
        with patch.object(s,'_kick'):
            accepted=s.run_automation_job(jid,'test-auto-one')
        with patch.object(s,'_watch_agent'):
            s._execute(jid)
        job=next(j for j in a.load_store()['jobs'] if j['id']==jid)
        runid=job['execution']['run_id'];self.assertTrue(self.c.execution_status(runid))
        duplicate=s.run_automation_job(jid,'test-auto-one');self.assertTrue(duplicate['ok'],duplicate)
        with patch.object(s,'_watch_agent'):
            s._start_agent(jid,job['execution'])
        self.assertEqual(len(self.c.snapshot()['state']['runs']),1)
        self.assertEqual(accepted['execution']['id'],job['execution']['id'])

    def test_once_validation_persists_goal_and_creation_key_detects_reuse(self):
        s=self.automation();jid,payload=self.save_job(s,cadence='once',date='2099-10-29',operation_id='same-call')
        self.assertEqual(s.save_automation_job(payload)['job_id'],jid)
        self.assertFalse(s.save_automation_job(dict(payload,goal='different'))['ok'])
        self.assertFalse(s.save_automation_job(dict(payload,operation_id='other',name='bad',date='invalid'))['ok'])
        self.assertEqual(next(j for j in a.load_store()['jobs'] if j['id']==jid)['cadence'],'once')

    def test_uncertain_automation_cannot_replay_and_check_reattaches_original_run(self):
        s=self.automation();jid,_=self.save_job(s)
        with patch.object(s,'_kick'):s.run_automation_job(jid)
        s._queued.clear()
        with patch.object(s,'_watch_agent'):s._execute(jid)
        original=copy.deepcopy(next(j for j in a.load_store()['jobs'] if j['id']==jid)['execution'])
        s.stop(0)
        fresh=self.automation()
        self.assertFalse(fresh.run_automation_job(jid)['ok'])
        with patch.object(fresh,'_watch_agent') as watch:
            result=fresh.check_agent_execution(jid)
        self.assertTrue(result['ok'],result);self.assertEqual(watch.call_args.args[1]['run_id'],original['run_id'])
        self.assertEqual(len(self.c.snapshot()['state']['runs']),1)

    def test_waiting_automation_does_not_retry_and_survives_restart_until_same_root_resumes(self):
        s=self.automation();jid,_=self.save_job(s,goal='test')
        self.assertTrue(s.run_automation_job(jid)['ok'])
        def wait_for(predicate):
            until=time.monotonic()+5
            while time.monotonic()<until:
                value=predicate()
                if value:return value
                time.sleep(.03)
            self.fail('automation observation did not settle')
        saved=wait_for(lambda:next(j for j in a.load_store()['jobs'] if j['id']==jid).get('execution',{}).get('run_id'))
        self.c.tick();root=self.c.execution_status(saved)
        self.chat.complete(root['task'],json.dumps(dict(mode='clarify',text='具体目标是什么？')));self.c.tick()
        wait_for(lambda:next(j for j in a.load_store()['jobs'] if j['id']==jid)['last_result']=='needs_input')
        self.assertNotIn('tebb_task',s._occupied());self.assertFalse(s.run_automation_job(jid)['ok'])
        s.stop(1)
        job=next(j for j in a.load_store()['jobs'] if j['id']==jid)
        self.assertEqual(job['execution']['phase'],'needs_input')
        fresh=self.automation();fresh._recover()
        self.c.action(dict(type='send',conversation=root['conversation'],run=root['id'],text='把Hello转为小写',request_id='automation-resume-one'))
        with self.c.lock,transaction(self.c.path) as (state,_):
            run=next(r for r in state['runs'] if r['id']==root['id']);run.update(state='succeeded',answer='hello')
        wait_for(lambda:next(j for j in a.load_store()['jobs'] if j['id']==jid)['last_result']=='ok')
        self.assertEqual(len(self.c.snapshot()['state']['runs']),1)

    def test_once_schedule_enqueues_one_slot_without_consuming_manual_schedule(self):
        s=self.automation();when=(datetime.now(a.TZ)+timedelta(days=2)).replace(hour=9,minute=0,second=0,microsecond=0)
        jid,_=self.save_job(s,cadence='once',date=when.date().isoformat(),enabled=True)
        with a.transaction():
            store=a.load_store();store['last_alive_at']=(when-timedelta(seconds=20)).isoformat();a.save_store(store)
        with patch.object(a,'_now',return_value=when),patch.object(s,'_kick'):
            s.tick();first=copy.deepcopy(next(j for j in a.load_store()['jobs'] if j['id']==jid))
            s._last_tick=0;s.tick();second=next(j for j in a.load_store()['jobs'] if j['id']==jid)
        self.assertEqual(first['last_result'],'queued');self.assertEqual(first['execution']['id'],second['execution']['id'])
        self.assertEqual(len(s._queued),1)

    def test_real_automation_monitor_finishes_only_after_its_saved_run(self):
        s=self.automation();jid,_=self.save_job(s,agent_id='writer')
        result=s.run_automation_job(jid);self.assertTrue(result['ok'],result)
        deadline=time.monotonic()+5;runid=''
        while time.monotonic()<deadline:
            runid=next(j for j in a.load_store()['jobs'] if j['id']==jid).get('execution',{}).get('run_id','')
            if runid:break
            time.sleep(.02)
        self.assertTrue(runid)
        with self.c.lock,transaction(self.c.path) as (state,_):
            run=next(r for r in state['runs'] if r['id']==runid);run.update(state='succeeded',answer='已完成')
        while time.monotonic()<deadline:
            job=next(j for j in a.load_store()['jobs'] if j['id']==jid)
            if job['last_result']=='ok':break
            time.sleep(.02)
        self.assertEqual(job['last_result'],'ok',job);self.assertFalse(s.snapshot()['items'][0]['active'])
        self.assertEqual(job['execution']['result']['run_id'],runid)


if __name__=='__main__':unittest.main()
