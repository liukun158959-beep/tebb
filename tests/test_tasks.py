"""真实子进程验证：硬超时、取消、并行及会话顺序。不调用线上模型。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from desk_companion.tasks import TaskManager

WORKER = '''import json,sys,time,os
if '--standby' in sys.argv: print(json.dumps(dict(kind='standby_ready')),flush=True)
r=json.loads(sys.stdin.readline())
def emit(k,d):
 print(json.dumps(dict(kind=k,data=d)),flush=True)
if '--standby' in sys.argv: emit('standby_identity',os.getpid())
emit('token','保留片段')
if r['text']=='timeout':
 emit('llm_start',{})
 time.sleep(10)
elif r['text']=='slow': time.sleep(.7)
elif r['text']=='stream':
 emit('llm_start',{})
 for i in range(7):
  time.sleep(.07)
  emit('token','流')
 emit('llm_end',{})
elif r['text']=='stream_stalls':
 emit('llm_start',{})
 emit('token','流')
 time.sleep(10)
elif r['text']=='archive_timeout':
 emit('result','完整笔记')
 emit('tool_start',dict(tool='archive_video_notes',read_only=False))
 time.sleep(10)
elif r['text']=='tool':
 emit('tool_start',dict(tool='create_calendar_event',step='s1'))
 emit('tool_end',dict(tool='create_calendar_event',step='s1',status='success'))
elif r['text']=='command_wait':
 emit('tool_start',dict(tool='workspace_run',phase_timeout=1))
 time.sleep(.5)
 emit('tool_end',dict(tool='workspace_run',status='success'))
elif r['text'] in ('project_wait','invalid_project_wait'):
 emit('tool_start',dict(tool='project_prepare',phase_timeout=300 if r['text']=='project_wait' else 361))
 time.sleep(.5)
 emit('tool_end',dict(tool='project_prepare',status='success'))
elif r['text'] in ('late_model_end','late_model_progress'):
 emit('llm_start',{})
 time.sleep(.5)
 if r['text']=='late_model_progress': emit('llm_progress',True)
 emit('llm_end',{})
elif r['text']=='video_failure':
 emit('failure',dict(type='APIConnectionError',message='片段 3/8 连接中断，已完成2/8',phase='片段3',completed=2,total=8))
 sys.exit(0)
elif r['text'] in ('replace_reply','replace_failure'):
 emit('answer_reset',True)
 time.sleep(.35)
 if r['text']=='replace_failure':
  emit('failure',dict(type='OfflineError',message='新版本未返回'))
  sys.exit(0)
 emit('token','新版')
 time.sleep(.25)
 emit('token','正文')
 emit('result','新版正文')
 sys.exit(0)
elif r['text'] in ('chart_failure','chart_timeout'):
 emit('answer_reset',True)
 emit('tool_start',dict(tool='build_chart'))
 if r['text']=='chart_timeout': time.sleep(10)
 emit('failure',dict(type='ChartBuildError',message='图稿修正仍失败'))
 sys.exit(0)
emit('result',r['text'])
'''


class TaskTests(unittest.TestCase):
    def test_expired_phase_cannot_be_cleared_before_supervisor_observes_it(self):
        start=threading.Thread.start;settled=[]
        def handoff(thread):
            reader=getattr(thread._target,'__name__','')=='read'
            result=start(thread)
            if reader:
                thread.join(4)
                if thread.is_alive():raise TimeoutError('reader barrier did not settle')
                settled.append(True)
            return result
        self.manager.settings.update(call_timeout=.15,task_timeout=5)
        for text,stage,expected in [('project_wait',None,'succeeded'),('invalid_project_wait','tool_start','timed_out'),
                                    ('late_model_end','llm_start','timed_out'),('late_model_progress','llm_start','timed_out')]:
            with self.subTest(text=text),patch.object(threading.Thread,'start',handoff):
                task=self.manager.wait(self.manager.submit(text,'phase-'+text))
                self.assertEqual(task['state'],expected)
                if stage:self.assertEqual(next(e['data']for e in task['events']if e['kind']=='timeout_phase'),dict(stage=stage,scope='call'))
        self.assertEqual(len(settled),4)

    def controlled_clock(self):
        clock = {'offset': 0}
        timer = SimpleNamespace(time=time.time, sleep=time.sleep,
                                monotonic=lambda: time.monotonic() + clock['offset'])
        replacement = patch('desk_companion.tasks.time', timer)
        replacement.start()
        self.addCleanup(replacement.stop)
        return clock

    def test_deadline_uses_observed_completion_not_supervisor_resume_time(self):
        start = threading.Thread.start
        for late_result in (True, False):
            with self.subTest(late_result=late_result):
                clock = {'offset': 0}
                timer = SimpleNamespace(time=time.time, sleep=time.sleep,
                                        monotonic=lambda: time.monotonic() + clock['offset'])
                def handoff(thread):
                    reader = getattr(thread._target, '__name__', '') == 'read'
                    if reader and late_result: clock['offset'] = 10
                    result = start(thread)
                    if reader:
                        thread.join(4)
                        if thread.is_alive(): raise TimeoutError('reader barrier did not settle')
                        if not late_result: clock['offset'] = 10
                    return result
                self.manager.settings.update(call_timeout=5, task_timeout=5)
                with patch('desk_companion.tasks.time', timer), patch.object(threading.Thread, 'start', handoff):
                    task = self.manager.wait(self.manager.submit('instant', 'deadline-' + str(late_result)))
                self.assertEqual(task['state'], 'timed_out' if late_result else 'succeeded')
                if late_result:
                    self.assertIn('任务总时限', task['error'])
                    self.assertNotIn('自动归档', task['error'])
                    self.assertEqual(next(e['data'] for e in task['events'] if e['kind'] == 'timeout_phase'),
                                     dict(stage='outside_call', scope='task'))

    def test_shutdown_reports_pending_reader_without_releasing_session(self):
        clock = self.controlled_clock()
        entered, release = threading.Event(), threading.Event()
        original = self.manager.event
        def event(task, kind, data, **kwargs):
            if kind == 'token' and not entered.is_set():
                with self.manager.db() as connection:
                    connection.execute('SELECT id FROM tasks').fetchall()
                    entered.set()
                    if not release.wait(15): raise TimeoutError('reader barrier not released')
            return original(task, kind, data, **kwargs)
        self.manager.event = event
        self.manager.settings.update(call_timeout=30, task_timeout=30)
        try:
            ident = self.manager.submit('stream', 'pending-reader-database')
            self.assertTrue(entered.wait(5))
            clock['offset'] = 40
            self.assertEqual(self.manager.wait(ident)['state'], 'timed_out')
            started = time.monotonic()
            result = self.manager.shutdown()
            self.assertLess(time.monotonic() - started, 6)
            self.assertFalse(result['complete'])
            self.assertEqual(result['pending_task_ids'], [ident])
            self.assertIn(ident, self.manager.active)
        finally:
            release.set()
            self.assertTrue(self.manager.shutdown()['complete'])

    def test_shutdown_waits_for_reader_database_io_after_terminal_task_state(self):
        clock = self.controlled_clock()
        entered,release,returned=threading.Event(),threading.Event(),threading.Event()
        original=self.manager.event;results=[];closer=None
        def event(task,kind,data,**kwargs):
            if kind=='token'and not entered.is_set():
                with self.manager.db()as connection:
                    connection.execute('SELECT id FROM tasks').fetchall();entered.set()
                    if not release.wait(12):raise TimeoutError('reader barrier not released')
            return original(task,kind,data,**kwargs)
        self.manager.event=event;self.manager.settings.update(call_timeout=30,task_timeout=30)
        try:
            ident=self.manager.submit('stream','reader-database-barrier')
            self.assertTrue(entered.wait(5))
            clock['offset'] = 40
            self.assertEqual(self.manager.wait(ident)['state'],'timed_out')
            self.assertIn(ident,self.manager.active)
            def shutdown():results.append(self.manager.shutdown());returned.set()
            closer=threading.Thread(target=shutdown);closer.start()
            self.assertFalse(returned.wait(.2),'shutdown returned while reader still held SQLite')
            release.set();closer.join(5)
            self.assertFalse(closer.is_alive());self.assertTrue(results[0]['complete'],results)
            self.assertEqual(results[0]['pending_task_ids'],[]);self.assertEqual(self.manager.active,{})
        finally:
            release.set()
            if closer:closer.join(5)

    def test_replacement_keeps_visible_text_until_atomic_first_new_token(self):
        task_id=self.manager.submit('replace_reply','replacement','collaboration')
        deadline=time.monotonic()+5
        while not any(e['kind']=='answer_reset' for e in self.manager.get(task_id)['events']):
            self.assertLess(time.monotonic(),deadline);time.sleep(.01)
        self.assertEqual(self.manager.get(task_id)['answer'],'保留片段')
        observed=[]
        while self.manager.get(task_id)['state'] not in {'succeeded','failed','timed_out'}:
            observed.append(self.manager.get(task_id)['answer']);time.sleep(.005)
        self.assertTrue(observed)
        self.assertTrue(all(value in {'保留片段','新版','新版正文'} for value in observed),set(observed))
        self.assertEqual(self.manager.wait(task_id)['answer'],'新版正文')
        failed=self.manager.wait(self.manager.submit('replace_failure','replacement','collaboration'))
        self.assertEqual(failed['state'],'failed')
        self.assertEqual(failed['answer'],'保留片段')
    def test_chart_failure_or_timeout_keeps_pre_correction_text_without_success(self):
        for text,state in [('chart_failure','failed'),('chart_timeout','timed_out')]:
            task=self.manager.wait(self.manager.submit(text,text,channel='collaboration',workflow='collaboration',requires_charts=True,task_limits=dict(call_timeout=.4,task_timeout=.8)))
            self.assertEqual(task['state'],state)
            self.assertEqual(task['answer'],'保留片段')

    def test_failed_standby_falls_back_to_a_cold_worker(self):
        self.manager.shutdown()
        self.worker.write_text(WORKER.replace("if '--standby' in sys.argv: print(json.dumps(dict(kind='standby_ready')),flush=True)","if '--standby' in sys.argv: sys.exit(1)"),encoding='utf-8')
        self.manager=TaskManager(path=self.manager.path,command=[sys.executable,'-u',str(self.worker)],prewarm=1)
        self.assertFalse(self.manager.standby.wait_ready(.3))
        task=self.manager.wait(self.manager.submit('cold fallback','fallback'))
        self.assertEqual(task['state'],'succeeded')
        self.assertFalse(next(e['data']['prewarmed'] for e in task['events'] if e['kind']=='worker_pool'))

    def test_standby_processes_are_one_shot_cancellable_and_stopped_on_shutdown(self):
        self.manager.shutdown()
        self.manager=TaskManager(path=self.manager.path,command=[sys.executable,'-u',str(self.worker)],prewarm=1)
        self.assertTrue(self.manager.standby.wait_ready(5))
        first=self.manager.wait(self.manager.submit('first','one'))
        self.assertTrue(next(e['data']['prewarmed'] for e in first['events'] if e['kind']=='worker_pool'))
        self.assertTrue(self.manager.standby.wait_ready(5))
        second=self.manager.wait(self.manager.submit('second','two'))
        identity=lambda task:next(e['data'] for e in task['events'] if e['kind']=='standby_identity')
        self.assertNotEqual(identity(first),identity(second))
        self.assertEqual(second['answer'],'second')
        self.assertTrue(self.manager.standby.wait_ready(5))
        task=self.manager.submit('timeout','cancel-warm')
        deadline=time.monotonic()+3
        while not self.manager.get(task)['answer'] and time.monotonic()<deadline:time.sleep(.02)
        self.manager.cancel(task);self.assertEqual(self.manager.wait(task)['state'],'cancelled')
        self.assertTrue(self.manager.standby.wait_ready(5))
        workers=[item[0] for item in self.manager.standby.owned.values() if item]
        result=self.manager.shutdown()
        self.assertTrue(all(p.poll() is not None for p in workers))
        self.assertTrue(result['complete']);self.assertTrue(result['standby']['complete'])
        self.assertEqual({item['pid']for item in result['standby']['workers']},{p.pid for p in workers})
        self.assertTrue(all(item['exited']for item in result['standby']['workers']))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": self.temp.name})
        self.env.start()
        self.worker = Path(self.temp.name) / "worker.py"
        self.worker.write_text(WORKER, "utf-8")
        self.manager = TaskManager(path=Path(self.temp.name) / "tasks.sqlite3", command=[sys.executable, "-u", str(self.worker)])

    def tearDown(self):
        self.manager.shutdown()
        deadline = time.time() + 5
        while self.manager.active and time.time() < deadline:
            time.sleep(.05)
        self.env.stop()
        self.temp.cleanup()

    def test_parallel_and_same_session_fifo(self):
        first = self.manager.submit("slow", "a")
        second = self.manager.submit("second", "a")
        other = self.manager.submit("other", "b")
        self.assertEqual(self.manager.wait(other)["answer"], "other")
        self.assertNotIn(self.manager.get(first)["state"], {"succeeded", "failed"})
        self.assertEqual(self.manager.get(second)["state"], "queued")
        a = self.manager.wait(first)
        b = self.manager.wait(second)
        self.assertGreaterEqual(b["started"], a["ended"])

    def test_stable_submission_key_never_starts_the_same_work_twice(self):
        first = self.manager.submit("slow", "stable-session", "collaboration", submission_key="stable-key")
        second = self.manager.submit("slow", "stable-session", "collaboration", submission_key="stable-key")
        self.assertEqual(first, second)
        with self.assertRaises(ValueError):
            self.manager.submit("different", "stable-session", "collaboration", submission_key="stable-key")
        self.manager.wait(first)
        self.assertEqual(len(self.manager.list()["items"]), 1)

    def test_hard_call_timeout_keeps_partial(self):
        self.manager.settings.update(call_timeout=.2, task_timeout=3)
        started = time.monotonic()
        task = self.manager.wait(self.manager.submit("timeout", "a"))
        self.assertEqual(task["state"], "timed_out")
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(task["answer"], "保留片段")
        self.assertIn('模型接口等待',task['error'])
        phase = next(e['data'] for e in task['events'] if e['kind'] == 'timeout_phase')
        self.assertEqual(phase, dict(stage='llm_start', scope='call'))

    def test_total_timeout_outside_call_is_distinct_from_model_wait_and_persisted(self):
        self.manager.settings.update(call_timeout=.3, task_timeout=.3)
        # Both cold startup and the fixture's non-model wait are outside a call.
        # Do not assume the child can emit its first event within 0.3 seconds.
        task = self.manager.wait(self.manager.submit('slow', 'outside-call'))
        self.assertEqual(task['state'], 'timed_out')
        events = self.manager.get(task['id'])['events']
        pool = next(e['data'] for e in events if e['kind'] == 'worker_pool')
        self.assertGreaterEqual(pool['spawn_duration_ms'], 0)
        self.assertEqual(next(e['data'] for e in events if e['kind'] == 'timeout_phase'),
                         dict(stage='outside_call', scope='task'))
        self.assertFalse(any(e['kind'] == 'llm_start' for e in events))

    def test_coding_command_wait_budget_does_not_use_short_model_idle_timeout(self):
        self.manager.settings.update(call_timeout=.15,task_timeout=3)
        task=self.manager.wait(self.manager.submit('command_wait','command'))
        self.assertEqual(task['state'],'succeeded')
        self.manager.settings['task_timeout']=.25
        limited=self.manager.wait(self.manager.submit('command_wait','total-limit'))
        self.assertEqual(limited['state'],'timed_out')

    def test_project_tool_wait_is_honored_but_bounded_by_total_and_valid_range(self):
        self.manager.settings.update(call_timeout=.15,task_timeout=3)
        task=self.manager.wait(self.manager.submit('project_wait','project'))
        self.assertEqual(task['state'],'succeeded')
        invalid=self.manager.wait(self.manager.submit('invalid_project_wait','invalid-project'))
        self.assertEqual(invalid['state'],'timed_out')
        self.manager.settings['task_timeout']=.25
        limited=self.manager.wait(self.manager.submit('project_wait','total-project'))
        self.assertEqual(limited['state'],'timed_out')

    def test_video_failure_preserves_phase_and_coverage_in_timeline(self):
        task = self.manager.wait(self.manager.submit("video_failure", "a"))
        self.assertEqual(task["state"], "failed")
        self.assertIn("连接中断", task["error"])
        event = next(e for e in task["events"] if e["kind"] == "failure")
        self.assertEqual((event["data"]["completed"], event["data"]["total"]), (2, 8))
        self.assertEqual(event["data"]["phase"], "片段3")

    def test_archive_timeout_preserves_finished_answer(self):
        self.manager.settings.update(call_timeout=.3, task_timeout=3)
        task = self.manager.wait(self.manager.submit("archive_timeout", "a"))
        self.assertEqual(task["state"], "succeeded")
        self.assertEqual(task["answer"], "完整笔记")
        self.assertIn("归档达到时限", task["error"])

    def test_streaming_progress_extends_idle_deadline_but_stalls_still_timeout(self):
        self.manager.settings.update(call_timeout=.2, task_timeout=3)
        task = self.manager.wait(self.manager.submit("stream", "a"))
        self.assertEqual(task["state"], "succeeded")
        stalled = self.manager.wait(self.manager.submit("stream_stalls", "a"))
        self.assertEqual(stalled["state"], "timed_out")
        self.assertIn("流", stalled["answer"])

    def test_streaming_cannot_extend_total_task_deadline(self):
        self.manager.settings.update(call_timeout=.2, task_timeout=.3)
        task = self.manager.wait(self.manager.submit("stream", "a"))
        self.assertEqual(task["state"], "timed_out")

    def test_single_video_link_routes_from_feishu_and_successful_followup_uses_agent(self):
        task_id = self.manager.submit("https://www.bilibili.com/video/BV1ojfDBSEPv", "a", "feishu")
        task = self.manager.wait(task_id)
        with self.manager.db() as db:
            request = json.loads(db.execute("SELECT request FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
        self.assertEqual(request["workflow"], "video_note")
        self.assertEqual(request["limits"]["task_timeout"], 900)
        self.assertEqual(task["source"]["workflow"], "video")
        next_id = self.manager.resume(task_id, "只解释一个词")
        self.manager.wait(next_id)
        with self.manager.db() as db:
            followup = json.loads(db.execute("SELECT request FROM tasks WHERE id=?", (next_id,)).fetchone()[0])
        self.assertNotIn("workflow", followup)
        self.assertFalse(followup["auto_video_archive"])

    def test_archive_config_snapshot_and_successful_followup_opt_out(self):
        from desk_companion.wiki_connection import settings_path
        from desk_companion.video import atomic_write
        atomic_write(settings_path(), {"auto_video_save": True, "video_parent_token": "video"})
        task = self.manager.wait(self.manager.submit("normal", "a"))
        atomic_write(settings_path(), {"auto_video_save": False})
        resumed = self.manager.resume(task["id"], "详细说说一个概念")
        with self.manager.db() as db:
            request = json.loads(db.execute("SELECT request FROM tasks WHERE id=?", (resumed,)).fetchone()[0])
        self.assertTrue(request["video_archive_config"]["auto_video_save"])
        self.assertFalse(request["auto_video_archive"])
        self.manager.wait(resumed)

    def test_cancel_running_and_queued(self):
        first = self.manager.submit("timeout", "a")
        second = self.manager.submit("next", "a")
        deadline = time.time() + 2
        while not self.manager.get(first)["answer"] and time.time() < deadline:
            time.sleep(.02)
        self.manager.cancel(second)
        self.manager.cancel(first)
        self.assertEqual(self.manager.wait(first)["state"], "cancelled")
        self.assertEqual(self.manager.wait(second)["state"], "cancelled")

    def test_restart_never_replays_and_resume_carries_steps(self):
        task = self.manager.wait(self.manager.submit("tool", "a"))
        resumed = self.manager.resume(task["id"], "请继续")
        with self.manager.db() as db:
            payload = json.loads(db.execute("SELECT request FROM tasks WHERE id=?", (resumed,)).fetchone()[0])
        self.assertIn("create_calendar_event", payload["resume_note"])
        self.assertIn("不可盲目重做", payload["resume_note"])
        self.manager.wait(resumed)
        with self.manager.db() as db:
            db.execute("UPDATE tasks SET state='running' WHERE id=?", (task["id"],))
            db.commit()
        self.manager.shutdown()
        self.manager.scheduler.join(1)
        self.manager = TaskManager(path=self.manager.path, command=[sys.executable, "-u", str(self.worker)])
        self.assertEqual(self.manager.get(task["id"])["state"], "interrupted")


if __name__ == "__main__":
    unittest.main()
