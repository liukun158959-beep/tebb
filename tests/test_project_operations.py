from pathlib import Path
import tempfile
import threading
import time
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desk_companion.collaboration import initialize_workspace,transaction
from desk_companion.project_operations import execute
from desk_companion.project_tools import specs


class Projects:
    def __init__(self):self.calls=[]
    def prepare(self,**kwargs):self.calls.append(kwargs);return dict(ok=True,received=kwargs)


class ProjectAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'workspace.sqlite3';initialize_workspace(self.path)
        self.run=dict(id='run',kind='task',state='running',agent='research',agent_config=dict(tools=['项目环境']),
                      task='physical-task',conversation='real-conversation',workspace=dict(project_id='real-project'))
        with transaction(self.path) as (state,_):state['runs'].append(self.run)
        self.task=dict(id='physical-task',state='running',channel='collaboration',session='collab-run')
        manager=SimpleNamespace(get=lambda ident:self.task)
        self.projects=Projects()
        self.host=SimpleNamespace(collaboration=SimpleNamespace(lock=threading.RLock(),path=self.path,managers=dict(work=manager)),
                                  project_environments=self.projects)

    def call(self,args=None,task='physical-task'):
        return execute(self.host,'run',task,'prepare',args or dict(repository='https://github.com/example/demo',_call_id='actual-call'))

    def change_run(self,**changes):
        with transaction(self.path) as (state,_):next(value for value in state['runs'] if value['id']=='run').update(changes)

    def test_authorized_custom_partner_uses_parent_scope_and_stable_request(self):
        first=self.call();second=self.call()
        self.assertTrue(first['ok']);self.assertEqual(first['received'],second['received'])
        self.assertEqual(first['received']['scope'],'project:real-project')
        self.assertEqual(first['received']['owner_task'],'physical-task')
        self.assertEqual(first['received']['owner_run'],'run')

    def test_tebb_identity_does_not_bypass_new_capability(self):
        self.change_run(agent='tebb',agent_config=dict(tools=[]))
        self.assertFalse(self.call()['ok']);self.assertEqual(self.projects.calls,[])

    def test_stale_phase_or_cancelled_physical_task_cannot_create(self):
        self.assertFalse(self.call(task='old-physical-task')['ok'])
        self.task['state']='cancelled'
        self.assertFalse(self.call()['ok']);self.assertEqual(self.projects.calls,[])

    def test_physical_task_from_another_conversation_root_cannot_create(self):
        self.task['session']='collab-other-root'
        self.assertFalse(self.call()['ok']);self.assertEqual(self.projects.calls,[])

    def test_actual_task_manager_public_identity_works_without_exposing_private_request(self):
        from desk_companion.tasks import TaskManager
        manager=TaskManager(path=Path(self.folder.name)/'tasks.sqlite3',
                            command=[sys.executable,'-u','-c','import sys,time;sys.stdin.readline();time.sleep(30)'])
        self.addCleanup(manager.shutdown);manager.configure(1,10,10,False)
        ident=manager.submit('bounded authentication fixture','collab-run','collaboration',collab_run='run')
        self.host.collaboration.managers['work']=manager;self.change_run(task=ident)
        deadline=time.monotonic()+3
        while manager.get(ident)['state']=='queued' and time.monotonic()<deadline:time.sleep(.02)
        self.assertNotIn('request',manager.get(ident));self.assertTrue(self.call(task=ident)['ok'])
        manager.cancel(ident);self.assertFalse(self.call(task=ident)['ok'])

    def test_route_and_ended_run_cannot_create(self):
        for changes in [dict(kind='route'),dict(kind='task',state='succeeded')]:
            self.change_run(**changes);self.assertFalse(self.call()['ok'])
        self.assertEqual(self.projects.calls,[])

    def test_model_cannot_override_scope_owner_or_request_identity(self):
        for name in ('scope','owner_task','owner_run','request_id'):
            args=dict(repository='https://github.com/example/demo',_call_id='actual-call',**{name:'forged'})
            self.assertFalse(self.call(args)['ok'])
        self.assertEqual(self.projects.calls,[])

    def test_worker_adapter_binds_real_physical_task_and_normal_rpc(self):
        offered=specs(dict(collab_run='run',task_id='physical-task'))
        self.assertIn('project_reconcile',{value['function']['name'] for value in offered})
        prepare=next(value for value in offered if value['function']['name']=='project_prepare')
        self.assertFalse(prepare['read_only'])
        with patch('desk_companion.project_tools.call',return_value=dict(ok=True)) as rpc:
            prepare['implementation'](dict(repository='https://github.com/example/demo',_call_id='actual-call'))
        self.assertEqual(rpc.call_args.args[0],'project_operation')
        self.assertEqual(rpc.call_args.args[1]['task_id'],'physical-task')


if __name__=='__main__':unittest.main()
