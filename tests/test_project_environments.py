import json
import base64
import hashlib
import shlex
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import uuid

from desk_companion.project_environments import ProjectEnvironmentService,relative_path,repository
from desk_companion.docker_sandboxes import SandboxError


class Provider:
    def __init__(self):self.created=[];self.stopped=[];self.gate=None;self.admitted=threading.Event()
    def create(self,name,owner):
        self.created.append((name,owner));self.admitted.set()
        if self.gate:self.gate.wait(3)
        return dict(id='original-uuid',name=name,status='running')
    def stop(self,name,ident):
        self.stopped.append((name,ident));return dict(ok=True,state='stopped',sandbox_id=ident)
    def dependency_network(self,name,ident):return dict(scope='single_sandbox',exclusive_allowlist=False)
    def inventory(self):
        return [dict(name=name,id='original-uuid',agent='sbx-kit-shell',status='stopped' if self.stopped else 'running') for name,owner in self.created]
    def owned(self,name,ident):
        target=next(value for value in self.inventory() if value['name']==name)
        if target['id']!=ident:raise SandboxError('identity_changed','same name replacement')
        return target
    def capture(self,name,ident,argv):
        return json.dumps(dict(owner=self.created[-1][1],commit='',repository_matches=False,project_exists=False))


class ProjectEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.provider=Provider();self.service=ProjectEnvironmentService(self.folder.name,client=self.provider)
        self.scope='conversation:fixture';self.request=uuid.uuid4().hex

    def prepare(self):
        return self.service.prepare(self.scope,self.request,'https://github.com/example/demo')

    def prepared(self):
        with patch.object(self.service,'_exec',side_effect=[dict(stdout='a'*40),dict(stdout='installed'),dict(stdout='{"language":"node"}')]):return self.prepare()

    def test_same_request_returns_original_environment_and_never_repeats_clone_or_install(self):
        first=self.prepared();second=self.prepare()
        self.assertTrue(first['ok']);self.assertTrue(second['replayed'])
        self.assertEqual(first['environment']['id'],second['environment']['id'])
        self.assertEqual(len(self.provider.created),1)

    def test_missing_create_ack_is_retained_without_duplicate_environment(self):
        with patch.object(self.provider,'create',side_effect=SandboxError('outcome_unknown','timeout',unknown=True)) as create:
            first=self.prepare();second=self.prepare()
        self.assertFalse(first['ok']);self.assertEqual(first['operation']['state'],'unknown')
        self.assertEqual(first['environment']['id'],second['environment']['id']);self.assertEqual(create.call_count,1)
        self.assertEqual(self.provider.stopped,[])

    def test_different_call_id_still_observes_original_unknown_create_and_alias_is_immutable(self):
        with patch.object(self.provider,'create',side_effect=SandboxError('outcome_unknown','timeout',unknown=True)) as create:
            first=self.prepare();self.request=uuid.uuid4().hex;second=self.prepare()
            self.assertEqual(create.call_count,1);self.assertEqual(first['environment']['id'],second['environment']['id'])
            self.assertEqual(first['operation']['id'],second['operation']['id'])
            self.assertEqual(self.prepare()['operation']['id'],first['operation']['id'])
            with self.assertRaisesRegex(ValueError,'不同项目操作'):
                self.service.prepare(self.scope,self.request,'https://github.com/example/different')

    def test_stop_preserves_provider_ack_and_is_idempotent(self):
        initial=self.prepared();ident=initial['environment']['id']
        for _ in range(2):
            result=self.service.stop(ident,self.scope)
            self.assertTrue(result['ok']);self.assertEqual(result['state'],'stopped')
        self.assertEqual(self.service.store.get(ident)['state'],'stopped')
        self.assertTrue(all(value[1]=='original-uuid' for value in self.provider.stopped))

    def test_late_create_ack_after_cancel_is_stopped_not_registered_as_success(self):
        self.provider.gate=threading.Event();result=[]
        worker=threading.Thread(target=lambda:result.append(self.prepare()));worker.start()
        try:
            self.assertTrue(self.provider.admitted.wait(2))
            ident=self.service.store.list()[0]['id']
            self.assertFalse(self.service.stop(ident,self.scope)['ok'])
        finally:self.provider.gate.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(result[0]['operation']['state'],'cancelled')
        self.assertEqual(self.provider.stopped,[(self.provider.created[0][0],'original-uuid')])
        self.assertTrue(self.prepare()['replayed']);self.assertEqual(len(self.provider.created),1)

    def test_scope_mismatch_cannot_run_or_stop_original_environment(self):
        ident=self.prepared()['environment']['id']
        with self.assertRaisesRegex(ValueError,'不属于'):
            self.service.execute(ident,'conversation:other',uuid.uuid4().hex,'echo arbitrary')
        with self.assertRaises(ValueError):self.service.stop(ident,'conversation:other')
        self.assertEqual(self.provider.stopped,[])

    def test_read_failure_does_not_stop_previously_ready_service(self):
        ident=self.prepared()['environment']['id']
        self.service.store.observe(ident,service=dict(id='original-service',state='ready',operation='old-operation'))
        with patch.object(self.service,'_exec',side_effect=RuntimeError('file missing')):
            result=self.service.read(ident,self.scope,uuid.uuid4().hex,'missing.txt')
        self.assertFalse(result['ok']);self.assertEqual(self.provider.stopped,[])
        self.assertEqual(self.service.store.get(ident)['service']['id'],'original-service')

    def test_missing_file_preserves_prepared_environment_without_vm_restart(self):
        ident=self.prepared()['environment']['id'];before=self.service.store.get(ident)
        with patch.object(self.service,'_exec',side_effect=RuntimeError('file missing')):
            result=self.service.read(ident,self.scope,uuid.uuid4().hex,'missing.txt')
        self.assertFalse(result['ok']);self.assertEqual(result['operation']['state'],'failed')
        self.assertEqual(self.provider.stopped,[])
        self.assertEqual(self.service.store.get(ident)['state'],before['state'])

    def test_project_directory_can_be_listed_before_reading_real_filenames(self):
        ident=self.prepared()['environment']['id']
        project=Path(self.folder.name)/'repo';project.mkdir();(project/'README.rst').write_text('original documentation')
        for index in range(205):(project/f'file-{index:03d}').touch()
        def local_metadata(name,ident,argv):
            command=shlex.split(argv[-1]);self.assertEqual(command[:3],['python3','-I','-c'])
            return [sys.executable,'-I','-c',command[3]]
        with patch('desk_companion.project_environments.PROJECT',str(project)),patch.object(self.provider,'exec_command',side_effect=local_metadata,create=True):
            result=self.service.read(ident,self.scope,uuid.uuid4().hex,'.')
        self.assertTrue(result['ok'],result.get('error'));self.assertEqual(len(result['entries']),200)
        self.assertEqual(result['entries'][0],dict(name='README.rst',type='file'));self.assertTrue(result['truncated'])
        self.assertNotIn('text',result);self.assertNotIn('base64',result)

    def test_repository_and_paths_reject_credentials_local_roots_and_traversal(self):
        for value in ['file:///home/project','https://token@github.com/a/b','https://github.com/a/b?token=x','https://github.com/a/..']:
            with self.assertRaises(ValueError):repository(value)
        for value in ['/etc/passwd','../secrets','src/../../etc','C:\\host\\file']:
            with self.assertRaises(ValueError):relative_path(value)

    def test_ready_operation_receipt_does_not_end_live_service_log_stream(self):
        initial=self.prepared();ident=initial['environment']['id']
        self.service._append(self.request,'server log')
        self.service.processes[self.request]=SimpleNamespace(poll=lambda:None)
        result=self.service.log(ident,self.scope,self.request)
        self.assertFalse(result['done'])
        self.service.processes.pop(self.request)
        self.assertTrue(self.service.log(ident,self.scope,self.request)['done'])

    def test_exited_foreground_session_cannot_report_ready_while_stop_is_pending(self):
        ident=self.prepared()['environment']['id']
        self.service.store.observe(ident,service=dict(state='ready',operation=self.request,host_port=19991))
        self.service.processes[self.request]=SimpleNamespace(poll=lambda:7)
        with patch.object(self.service,'_http',return_value=dict(ok=True,status=200)):
            observed=self.service.status(ident,self.scope)
        self.assertEqual(observed['observed_runtime']['status'],'running')
        self.assertFalse(observed['service_ready']);self.assertTrue(observed['recovery_required'])
        self.service.processes.pop(self.request)

    def test_file_capture_keeps_64k_payload_separate_from_cli_startup_stderr(self):
        ident=self.prepared()['environment']['id'];data=b'A'*65536
        payload=dict(path='large.txt',base64=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest())
        path=Path(self.folder.name)/'response.json';path.write_text(json.dumps(payload),'utf-8')
        program=[sys.executable,'-u','-c','import sys;sys.stderr.write("SDK sandbox started\\n");sys.stdout.buffer.write(open(sys.argv[1],"rb").read())',str(path)]
        with patch.object(self.provider,'exec_command',return_value=program,create=True):
            result=self.service.read(ident,self.scope,uuid.uuid4().hex,'large.txt')
        self.assertTrue(result['ok'],result.get('error'));self.assertEqual(result['text'],'A'*65536)

    def test_lost_create_receipt_can_verify_original_marker_bind_and_stop_without_recreating(self):
        def lost(name,owner):
            self.provider.created.append((name,owner));raise SandboxError('outcome_unknown','ack lost',unknown=True)
        with patch.object(self.provider,'create',side_effect=lost):first=self.prepare()
        ident=first['environment']['id']
        observed=self.service.reconcile(ident,self.scope)
        self.assertTrue(observed['verification_required']);self.assertEqual(self.service.store.get(ident)['sandbox_id'],'')
        verified=self.service.reconcile(ident,self.scope,verify=True)
        self.assertTrue(verified['identity_verified']);self.assertEqual(verified['environment']['sandbox_id'],'original-uuid')
        self.assertTrue(self.service.stop(ident,self.scope)['ok']);self.assertEqual(len(self.provider.created),1)

    def test_known_same_name_replacement_is_never_probed_or_adopted(self):
        ident=self.prepared()['environment']['id']
        replacement=dict(name=self.provider.created[0][0],id='replacement',agent='sbx-kit-shell',status='running')
        with patch.object(self.provider,'inventory',return_value=[replacement]),patch.object(self.provider,'capture') as capture:
            with self.assertRaises(SandboxError):self.service.reconcile(ident,self.scope,verify=True)
        capture.assert_not_called();self.assertEqual(self.service.store.get(ident)['sandbox_id'],'original-uuid')


if __name__=='__main__':unittest.main()
