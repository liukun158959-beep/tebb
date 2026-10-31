import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from desk_companion.bridge import Bridge
from desk_companion.docker_sandboxes import SandboxError
from desk_companion.project_environment_store import EnvironmentStore
from desk_companion.project_environments import ProjectEnvironmentService


class UnknownProvider:
    def __init__(self):self.creates=[];self.stops=[];self.candidates=[];self.owner='different-owner'
    def create(self,name,owner):
        self.creates.append((name,owner))
        raise SandboxError('outcome_unknown','original acknowledgement missing',unknown=True)
    def inventory(self):return self.candidates
    def capture(self,*args):return json.dumps(dict(owner=self.owner))
    def stop(self,*args):self.stops.append(args);raise AssertionError('must not stop by candidate name')


class ProjectCreateArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=str(self.root/'profile'))
        self.env.start();self.addCleanup(self.env.stop)
        self.provider=UnknownProvider()
        self.service=ProjectEnvironmentService(self.root/'projects',client=self.provider)
        self.scope='original-scope';self.repo='https://github.com/example/demo'

    def prepare(self,scope=None,request=None,repo=None):
        return self.service.prepare(scope or self.scope,request or uuid.uuid4().hex,repo or self.repo,owner_task='original-task',owner_run='original-run')

    def archive(self,result,request=None):
        return self.service.archive_unknown_create(result['environment']['id'],result['environment']['scope'],
            result['operation']['id'],request or uuid.uuid4().hex,confirm=True)

    def test_two_unknown_slots_require_explicit_archive_and_old_request_does_not_create(self):
        first=self.prepare();second=self.prepare('second-scope')
        with self.assertRaisesRegex(ValueError,'两个项目'):self.prepare('third-scope')
        self.assertEqual(len(self.provider.creates),2)
        self.assertFalse(self.service.log(first['environment']['id'],self.scope,first['operation']['id'])['done'])
        archived=self.archive(first)
        self.assertFalse(archived['stop_confirmed']);self.assertEqual(archived['creation_outcome'],'unknown')
        self.assertEqual(self.service.store.operation(first['operation']['id'])['state'],'unknown')
        observed=self.service.status(first['environment']['id'],self.scope)
        self.assertEqual(observed['operation']['owner_task'],'original-task')
        self.assertEqual(observed['operation']['state'],'unknown')
        replay=self.prepare(request=first['operation']['id'])
        self.assertTrue(replay['replayed']);self.assertEqual(replay['environment']['id'],first['environment']['id'])
        self.assertEqual(len(self.provider.creates),2)
        third=self.prepare('third-scope');self.assertEqual(len(self.provider.creates),3)
        self.assertNotEqual(third['environment']['id'],second['environment']['id'])
        log=self.service.log(first['environment']['id'],self.scope,first['operation']['id'])
        self.assertTrue(log['done']);self.assertTrue(log['creation_outcome_unknown'])
        self.assertIn('original acknowledgement',base64.b64decode(log['base64']).decode())

    def test_same_repository_new_request_is_fresh_and_late_identity_stays_with_archived_original(self):
        first=self.prepare();self.archive(first);new=self.prepare()
        self.assertEqual(len(self.provider.creates),2);self.assertNotEqual(first['environment']['id'],new['environment']['id'])
        old=self.service.store.bind_runtime(first['operation']['id'],'original-late-uuid')
        self.assertEqual(old['owner_run'],'original-run');self.assertIn('late_runtime_observed_at',old)
        self.service.store.finish(first['operation']['id'],'succeeded',changes=dict(state='available'),result=dict(commit='late'))
        self.assertEqual(self.service.store.operation(first['operation']['id'])['state'],'unknown')
        self.assertEqual(self.service.store.operation(first['operation']['id'])['owner_task'],'original-task')
        self.assertEqual(self.service.store.operation(first['operation']['id'])['late_observations'][0]['state'],'succeeded')
        self.assertEqual(self.service.store.get(old['id'])['active_operation'],'')
        self.assertEqual(self.service.store.get(new['environment']['id'])['active_operation'],new['operation']['id'])
        self.assertFalse(self.service.store.get(new['environment']['id'])['sandbox_id'])
        with self.assertRaises(RuntimeError):self.service.store.checkpoint(first['operation']['id'],'install',state='available')
        with self.assertRaises(ValueError):self.service.store.bind_runtime(first['operation']['id'],'same-name-other-uuid')
        with self.assertRaisesRegex(ValueError,'已归档'):self.service.store.begin(old['id'],'execute',uuid.uuid4().hex,dict(command='run'))
        self.assertFalse(self.provider.stops)

    def test_replay_restart_and_conflicting_request_preserve_original_receipt(self):
        first=self.prepare();request=uuid.uuid4().hex;archived=self.archive(first,request)
        self.service=ProjectEnvironmentService(self.root/'projects',client=self.provider)
        self.assertEqual(self.archive(first,request)['operation'],archived['operation'])
        self.assertEqual(self.archive(first)['operation'],archived['operation'])
        self.assertEqual(self.prepare(request=first['operation']['id'])['operation']['state'],'unknown')
        other=self.prepare('other-scope')
        with self.assertRaisesRegex(ValueError,'不同项目操作'):self.archive(other,request)
        self.assertEqual(self.service.store.get(other['environment']['id'])['active_operation'],other['operation']['id'])
        self.assertEqual(len(self.provider.creates),2)

    def test_confirmation_scope_expected_operation_known_uuid_and_service_fail_closed(self):
        first=self.prepare();ident=first['environment']['id'];operation=first['operation']['id']
        for confirm in (False,1,'yes'):
            with self.assertRaises(ValueError):self.service.archive_unknown_create(ident,self.scope,operation,uuid.uuid4().hex,confirm=confirm)
        with self.assertRaises(ValueError):self.service.archive_unknown_create(ident,'wrong-scope',operation,uuid.uuid4().hex,confirm=True)
        with self.assertRaises(ValueError):self.service.archive_unknown_create(ident,self.scope,uuid.uuid4().hex,uuid.uuid4().hex,confirm=True)
        self.service.store.bind_runtime(operation,'known-uuid')
        with self.assertRaisesRegex(ValueError,'已绑定'):self.archive(first)
        second=self.prepare('second-scope');self.service.store.observe_service(second['environment']['id'],'absent',{},'')
        with self.service.store.transaction() as db:
            env=self.service.store._environment(db,second['environment']['id']);env['service']=dict(state='unknown');self.service.store._save(db,env)
        with self.assertRaisesRegex(ValueError,'存在服务'):self.archive(second)
        self.assertFalse(self.provider.stops)

    def test_cancelled_worker_and_restart_observation_do_not_end_actual_parent_create(self):
        entered=threading.Event();release=threading.Event();original=self.provider.create
        def blocked(*args):entered.set();release.wait(10);return original(*args)
        self.provider.create=blocked
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.prepare)
            try:
                self.assertTrue(entered.wait(5));environment=self.service.store.list()[0]
                self.service.owner_active=lambda task:False
                self.service.store.cancel(environment['id']);self.service.store.recover_pending()
                result=dict(environment=environment,operation=self.service.store.operation(environment['prepare_operation']))
                self.assertTrue(self.service.status(environment['id'],self.scope)['archive_status']['local_active'])
                with self.assertRaisesRegex(ValueError,'仍在父后台'):self.archive(result)
                self.assertFalse(self.service.reconcile(environment['id'],self.scope,verify=True)['ok'])
            finally:release.set()
            completed=future.result(5)
        self.assertEqual(completed['operation']['state'],'unknown');self.assertTrue(self.archive(completed)['ok'])
        self.assertEqual(len(self.provider.creates),1)

    def test_guard_acquisition_failure_before_sdk_does_not_leave_running_admission(self):
        with patch.object(self.service.store,'creation_guard',side_effect=OSError('synthetic lock directory denied')):
            result=self.prepare()
        self.assertFalse(result['ok']);self.assertEqual(result['operation']['state'],'failed')
        self.assertEqual(result['environment']['active_operation'],'');self.assertFalse(self.provider.creates)

    def test_os_guard_from_another_process_blocks_archive_until_actual_exit(self):
        first=self.prepare();operation=first['operation']['id']
        self.assertTrue(self.service.status(first['environment']['id'],self.scope)['archive_status']['eligible'])
        code='import os,sys;os.environ["DESK_COMPANION_DATA_DIR"]=sys.argv[1]+"-different-profile";from desk_companion.project_environment_store import EnvironmentStore;s=EnvironmentStore(sys.argv[1]);\nwith s.creation_guard(sys.argv[2]):\n print("ready",flush=True);sys.stdin.readline()'
        process=subprocess.Popen([sys.executable,'-c',code,str(self.service.store.path),operation],
            cwd=Path(__file__).resolve().parents[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'ready')
            with self.assertRaisesRegex(ValueError,'仍在父后台'):self.archive(first)
        finally:
            _,error=process.communicate(input='release\n',timeout=10)
        self.assertEqual(process.returncode,0,error);self.assertTrue(self.archive(first)['ok'])

    def test_candidate_not_found_and_wrong_owner_are_not_proof_of_no_creation(self):
        first=self.prepare();ident=first['environment']['id']
        self.assertEqual(self.service.reconcile(ident,self.scope)['code'],'candidate_not_found')
        self.assertTrue(self.service.store.get(ident)['active_operation'])
        self.archive(first)
        self.provider.candidates=[dict(name=first['environment']['sandbox_name'],id='foreign-uuid',agent='sbx-kit-shell',status='running')]
        result=self.service.reconcile(ident,self.scope,verify=True)
        self.assertEqual(result['code'],'identity_changed');self.assertFalse(self.service.store.get(ident)['sandbox_id'])
        self.assertEqual(self.service.store.operation(first['operation']['id'])['state'],'unknown');self.assertFalse(self.provider.stops)

    def test_archive_and_late_binding_serialize_without_losing_identity(self):
        first=self.prepare();barrier=threading.Barrier(2)
        def archive():
            barrier.wait()
            try:return self.archive(first)
            except ValueError:return None
        def bind():barrier.wait();return self.service.store.bind_runtime(first['operation']['id'],'late-uuid')
        with ThreadPoolExecutor(max_workers=2) as executor:
            a=executor.submit(archive);b=executor.submit(bind);archived=a.result(5);b.result(5)
        environment=self.service.store.get(first['environment']['id'])
        self.assertEqual(environment['sandbox_id'],'late-uuid')
        self.assertEqual(environment['active_operation'],'' if archived else first['operation']['id'])
        self.assertEqual(self.service.store.operation(first['operation']['id'])['state'],'unknown')

    def test_desktop_bridge_only_exposes_explicit_archive_not_model_dispatch(self):
        from types import SimpleNamespace
        first=self.prepare();bridge=Bridge(SimpleNamespace(project_environments=self.service))
        result=bridge.project_environments_ui('archive_create',dict(environment=first['environment']['id'],
            expected_operation=first['operation']['id'],request_id=uuid.uuid4().hex,confirm=True))
        self.assertTrue(result['ok']);self.assertFalse(result['stop_confirmed'])
        from desk_companion import project_operations
        self.assertNotIn('archive_create',project_operations.OPERATIONS)
