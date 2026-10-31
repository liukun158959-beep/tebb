import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import psutil
from desk_companion.tasks import TaskManager,TERMINAL
from desk_companion.task_budgets import Ledger


class ReadonlyWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        context=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=str(self.root));context.start();self.addCleanup(context.stop)
        self.manager=TaskManager(path=self.root/'tasks.sqlite3',command=[sys.executable,'-u',str(Path(__file__).with_name('readonly_worker_fixture.py'))])
        self.addCleanup(self.manager.shutdown);self.manager.configure(2,30,60,False)
        self.ledger=Ledger();self.budget=self.ledger.create('readonly-lifecycle','research',60000)

    def submit(self,case):
        marker=self.root/(case+'.ready.json')
        ident=self.manager.submit('只输出JSON，依据所给链接正文，不编造已读。','readonly-'+case,'test',
            workflow='collaboration',kind='task',model_id='offline',collab_path=str(self.root/'workspace.sqlite3'),collab_run='child-'+case,
            parent_run='parent',stage='collect',dependencies=[],execution_runtime='atlas_readonly',conversation='test',budget=8,
            role=dict(id='research',name='research',role='read only',tools=['网页读取']),
            context=[dict(sender='user',text='https://example.org/read')],fixture_case=case,fixture_marker=str(marker),
            _budget_root=self.budget,_budget_root_member=False)
        return ident,marker

    def until(self,predicate,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.03)
        self.fail('bounded readonly lifecycle observation timed out')

    def marker(self,path):
        if not path.exists():return None
        try:return json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:return None

    def gone(self,marker):
        try:return psutil.Process(marker['pid']).create_time()!=marker['created']
        except psutil.NoSuchProcess:return True

    def test_cancel_blocked_model_stops_actual_worker_without_refunding_unknown_usage(self):
        ident,path=self.submit('block_model');marker=self.until(lambda:self.marker(path))
        self.manager.cancel(ident)
        self.until(lambda:self.manager.get(ident)['state']in TERMINAL);self.until(lambda:self.gone(marker))
        self.assertEqual(self.manager.get(ident)['state'],'cancelled')
        budget=self.ledger.view(self.budget)
        self.assertEqual(budget['consumed']['model_calls'],1);self.assertEqual(budget['consumed']['output_unknown_calls'],1)

    def test_shutdown_stops_blocked_read_and_settles_owned_process(self):
        ident,path=self.submit('block_read');marker=self.until(lambda:self.marker(path))
        result=self.manager.shutdown()
        self.assertTrue(result['complete']);self.until(lambda:self.gone(marker))
        self.assertIn(self.manager.get(ident)['state'],TERMINAL)
        self.assertEqual(self.ledger.view(self.budget)['consumed']['tool_calls'],1)

    def test_exhausted_root_deadline_stops_blocked_read_without_new_model_call(self):
        ident,path=self.submit('block_read');marker=self.until(lambda:self.marker(path))
        with self.ledger.change(self.budget)as root:root['limits']['time_ms']=1
        self.until(lambda:self.manager.get(ident)['state']in TERMINAL);self.until(lambda:self.gone(marker))
        self.assertNotEqual(self.manager.get(ident)['state'],'succeeded')
        budget=self.ledger.view(self.budget)
        self.assertEqual(budget['consumed']['model_calls'],1);self.assertEqual(budget['stop_reason']['dimension'],'time_ms')

    def test_failed_sibling_does_not_replace_successful_source_or_answer(self):
        success,_=self.submit('success');failed,_=self.submit('failed_read')
        self.until(lambda:all(self.manager.get(ident)['state']in TERMINAL for ident in(success,failed)))
        good=self.manager.get(success);bad=self.manager.get(failed)
        self.assertEqual(good['state'],'succeeded',good['error']);self.assertEqual(bad['state'],'failed')
        self.assertTrue(json.loads(good['answer'])['ok']);self.assertTrue(any(e['kind']=='evidence'for e in good['events']))
        self.assertFalse(any(e['kind']=='evidence'for e in bad['events']))

    @unittest.skipUnless(os.name=='nt','Windows kill-on-job-close parent-exit boundary')
    def test_abrupt_parent_exit_closes_job_and_ends_actual_atlas_worker(self):
        folder=self.root/'abrupt-parent';folder.mkdir();marker_path=folder/'worker.json'
        parent=subprocess.Popen([sys.executable,'-u',str(Path(__file__).with_name('readonly_parent_fixture.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
            env={**os.environ,'DESK_COMPANION_DATA_DIR':str(folder)},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        marker=None
        try:
            parent.stdin.write((json.dumps(dict(folder=str(folder),marker=str(marker_path)))+'\n').encode());parent.stdin.flush();parent.stdin.close()
            marker=self.until(lambda:self.marker(marker_path))
            self.assertIsNone(parent.poll());self.assertEqual(psutil.Process(marker['pid']).ppid(),parent.pid)
            parent.kill();parent.wait(timeout=5)
            self.until(lambda:self.gone(marker),timeout=5)
        finally:
            if parent.poll()is None:parent.kill();parent.wait(timeout=5)
            if marker and not self.gone(marker):
                process=psutil.Process(marker['pid'])
                if process.create_time()==marker['created']and str(Path(__file__).with_name('readonly_worker_fixture.py'))in process.cmdline():
                    process.kill();process.wait(timeout=5)
            parent.stderr.close()
