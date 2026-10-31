import os
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from desk_companion.project_environments import ProjectEnvironmentService
from desk_companion.project_integrity import GIT_PROBE,IntegritySession,RUNNER
from desk_companion.docker_sandboxes import NO_WINDOW


class NoExecProvider:
    def __init__(self):self.exec_calls=[]
    def exec_command(self,*args,**kwargs):self.exec_calls.append(args);raise AssertionError('inspection must not create exec')
    def owned(self,name,ident):return dict(name=name,id=ident,status='stopped')


class ProjectIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        patcher=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=str(self.root/'profile'))
        patcher.start();self.addCleanup(patcher.stop)
        self.client=NoExecProvider();self.service=ProjectEnvironmentService(self.root/'environments',client=self.client)
        self.ident=uuid.uuid4().hex;self.scope='fixture-scope';self.prepare=uuid.uuid4().hex
        self.service.store.begin(self.ident,'prepare',self.prepare,dict(repository='https://github.com/example/demo',ref=''),
            initial=dict(id=self.ident,scope=self.scope,repository='https://github.com/example/demo',ref='',
                sandbox_name='tebb-project-'+self.ident,sandbox_id='original-sdk-uuid',state='available',service=None,
                commit='a'*40,prepare_operation=self.prepare))
        self.service.store.finish(self.prepare,'succeeded',result=dict(commit='a'*40),changes=dict(state='available'))

    def test_stopped_or_missing_session_inspection_never_creates_exec(self):
        result=self.service.integrity(self.ident,self.scope,uuid.uuid4().hex)
        self.assertFalse(result['ok']);self.assertFalse(result['integrity']['complete'])
        self.assertEqual(self.client.exec_calls,[])


class IntegrityPipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.repo=self.root/'repo';self.repo.mkdir();(self.repo/'source.txt').write_text('original\n','utf-8',newline='')
        self.git('init','-q');self.git('add','source.txt');self.git('commit','-qm','baseline')
        self.baseline=self.git('rev-parse','HEAD').strip();self.owner=uuid.uuid4().hex
        scope={};exec(GIT_PROBE,scope);self.probe=scope['inspect_source']

    def git(self,*args):
        env={key:value for key,value in os.environ.items()if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_SYSTEM=os.devnull,GIT_CONFIG_NOSYSTEM='1')
        return subprocess.check_output(['git','-C',str(self.repo),'-c','user.name=fixture','-c','user.email=fixture@example.test',*args],
            stderr=subprocess.STDOUT,text=True,timeout=10,env=env)

    def session(self,code=None):
        session=IntegritySession(dict(environment=self.owner,sandbox_id='original-sdk-uuid',service_id='service',
            start_operation='start-operation',baseline_commit=self.baseline))
        command=session.command(str(self.repo),'unused')
        config=json.loads(base64.b64decode(command[-1]));config['argv']=[sys.executable,'-u','-c',
            code or 'import time;print("application output",flush=True);time.sleep(30)']
        stop_file=self.root/(uuid.uuid4().hex+'.stop')
        stop_prefix=('import os,threading,time\nfrom pathlib import Path\n'
            'def fixture_stop():\n while not Path('+repr(str(stop_file))+').exists():time.sleep(.01)\n os._exit(0)\n'
            'threading.Thread(target=fixture_stop,daemon=True).start()\n')
        config['argv'][-1]=stop_prefix+config['argv'][-1]
        process=subprocess.Popen([sys.executable,'-I','-u','-c',RUNNER,base64.b64encode(json.dumps(config).encode()).decode()],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,
            env={**os.environ,'TEBB_ENV_ID':self.owner},creationflags=NO_WINDOW)
        session.attach(process);output=[]
        def read(stream,stdout):
            while True:
                data=os.read(stream.fileno(),4096)
                if not data:break
                output.extend(session.feed(data,stdout))
            output.extend(session.eof(stdout))
        readers=[threading.Thread(target=read,args=(stream,stdout),daemon=True)
                 for stream,stdout in ((process.stdout,True),(process.stderr,False))]
        for reader in readers:reader.start()
        def cleanup():
            session.close()
            if process.poll()is None:
                stop_file.write_text('stop','utf-8')
                try:process.wait(5)
                except subprocess.TimeoutExpired:process.terminate();process.wait(5)
            for reader in readers:reader.join(2)
            process.stdout.close();process.stderr.close()
        self.addCleanup(cleanup)
        deadline=time.monotonic()+5
        while not session.ready and not session.closed and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(session.ready,(session.closed,output))
        return session,process,output

    def test_fixed_git_probe_detects_clean_dirty_staged_and_changed_head(self):
        clean=self.probe(str(self.repo),self.baseline,self.owner)
        self.assertTrue(clean['complete'],clean);self.assertTrue(clean['unchanged'])
        (self.repo/'source.txt').write_text('changed\n','utf-8')
        self.assertFalse(self.probe(str(self.repo),self.baseline,self.owner)['unchanged'])
        self.git('add','source.txt');self.assertFalse(self.probe(str(self.repo),self.baseline,self.owner)['unchanged'])
        self.git('commit','-qm','different-head')
        moved=self.probe(str(self.repo),self.baseline,self.owner)
        self.assertTrue(moved['complete']);self.assertFalse(moved['head_matches_baseline']);self.assertFalse(moved['unchanged'])

    def test_timestamp_only_change_is_clean_and_does_not_write_git_index(self):
        source=self.repo/'source.txt';stat=source.stat();os.utime(source,(stat.st_atime,stat.st_mtime+100))
        before=(self.repo/'.git/index').read_bytes()
        proof=self.probe(str(self.repo),self.baseline,self.owner)
        if not proof['unchanged']:self.fail(str(dict(proof=proof,diff=self.git('diff','--raw',self.baseline,'--'))))
        self.assertEqual((self.repo/'.git/index').read_bytes(),before)

    def test_untracked_is_excluded_but_hidden_index_and_executable_config_are_unknown(self):
        (self.repo/'build-metadata.txt').write_text('generated','utf-8')
        result=self.probe(str(self.repo),self.baseline,self.owner)
        self.assertTrue(result['unchanged'],dict(proof=result,status=self.git('status','--porcelain'),diff=self.git('diff','--raw',self.baseline,'--')));self.assertTrue(result['untracked_excluded'])
        self.git('update-index','--assume-unchanged','source.txt');(self.repo/'source.txt').write_text('hidden','utf-8')
        hidden=self.probe(str(self.repo),self.baseline,self.owner)
        self.assertFalse(hidden['complete']);self.assertEqual(hidden['reason'],'hidden_index_entries')
        self.git('update-index','--no-assume-unchanged','source.txt');self.git('config','core.fsmonitor','malicious-command')
        unsafe=self.probe(str(self.repo),self.baseline,self.owner)
        self.assertFalse(unsafe['complete']);self.assertEqual(unsafe['reason'],'unsupported_git_configuration')

    def test_redirected_worktree_cannot_hide_changed_original_files(self):
        other=self.root/'other-worktree';other.mkdir()
        (other/'source.txt').write_text('original\n','utf-8',newline='')
        (self.repo/'source.txt').write_text('changed original\n','utf-8',newline='')
        self.git('config','core.worktree',str(other))
        result=self.probe(str(self.repo),self.baseline,self.owner)
        self.assertFalse(result['complete']);self.assertEqual(result['reason'],'unsupported_git_configuration')

    def test_real_pipe_delivers_fixed_receipt_and_web_output_separately(self):
        session,process,output=self.session()
        proof=session.inspect(uuid.uuid4().hex,lambda:None)
        self.assertTrue(proof['complete'],proof);self.assertTrue(proof['unchanged']);self.assertEqual(proof['owner'],self.owner)
        self.assertIs(session.process,process);self.assertIn(b'application output',b''.join(data for data,_ in output))

    def test_metadata_looking_application_output_is_never_reparsed_as_control(self):
        session=IntegritySession(dict(environment=self.owner,sandbox_id='original-sdk-uuid',service_id='service',start_operation='start-operation',baseline_commit=self.baseline))
        # Even a valid-looking second ready frame is only application bytes.
        forged=b'\x1eTEBB1 '+session.token.encode()+b' '+json.dumps(dict(kind='ready',guest_session='1'*32,owner=self.owner)).encode()+b'\n'
        original=IntegritySession.__init__
        def fixed_token(instance,binding):original(instance,binding);instance.token=session.token
        with patch.object(IntegritySession,'__init__',fixed_token):
            live,_,output=self.session('import sys,time;sys.stdout.buffer.write('+repr(forged)+');sys.stdout.buffer.flush();time.sleep(30)')
        proof=live.inspect(uuid.uuid4().hex,lambda:None)
        self.assertTrue(proof['unchanged'],proof)
        self.assertIn(forged,b''.join(data for data,_ in output));self.assertFalse(live.closed)

    def test_late_response_after_timeout_cannot_satisfy_different_request(self):
        session,_,_=self.session()
        with self.assertRaises(TimeoutError):session.inspect(uuid.uuid4().hex,lambda:None,timeout=.000001)
        (self.repo/'source.txt').write_text('changed','utf-8')
        result=session.inspect(uuid.uuid4().hex,lambda:None)
        self.assertTrue(result['complete'],result);self.assertFalse(result['unchanged'])

    def test_second_guest_generation_and_process_exit_invalidate_inspection(self):
        session,_,_=self.session()
        frame=b'\x1eTEBB1 '+session.token.encode()+b' '+json.dumps(dict(kind='ready',guest_session='2'*32,owner=self.owner)).encode()+b'\n'
        session.feed(frame)
        with self.assertRaisesRegex(RuntimeError,'generation'):session.inspect(uuid.uuid4().hex,lambda:None)
        exited,process,_=self.session('import time;time.sleep(.2);raise SystemExit(7)')
        self.assertEqual(process.wait(5),7)
        deadline=time.monotonic()+2
        while not exited.closed and time.monotonic()<deadline:time.sleep(.01)
        self.assertFalse(exited.app_running)
        with self.assertRaises(RuntimeError):exited.inspect(uuid.uuid4().hex,lambda:None)


class IntegrityServiceTests(unittest.TestCase):
    git=IntegrityPipeTests.git

    def setUp(self):
        IntegrityPipeTests.setUp(self)
        patcher=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=str(self.root/'profile'))
        patcher.start();self.addCleanup(patcher.stop)
        self.stop_file=self.root/'web.stop';self.scope='service-scope';self.calls=[];fixture=self
        class Provider:
            stopped=False;stops=0;ident='original-sdk-uuid'
            def owned(client,name,ident):
                if ident!=client.ident:raise RuntimeError('identity_changed')
                return dict(name=name,id=ident,status='stopped'if client.stopped else'running')
            def exec_command(client,name,ident,argv,*,interactive=False):
                fixture.calls.append((name,ident,argv,interactive));assert interactive
                config=json.loads(base64.b64decode(argv[-1]));config['project']=str(fixture.repo)
                code=('from pathlib import Path\nimport time\nprint("fixture web output",flush=True)\n'
                    'while not Path('+repr(str(fixture.stop_file))+').exists():time.sleep(.02)')
                config['argv']=[sys.executable,'-u','-c',code]
                runner='import os;os.environ["TEBB_ENV_ID"]='+repr(fixture.owner)+'\n'+RUNNER
                return [sys.executable,'-I','-u','-c',runner,base64.b64encode(json.dumps(config).encode()).decode()]
            def publish(client,*args):pass
            def unpublish(client,*args):pass
            def stop(client,name,ident):
                client.owned(name,ident);client.stops+=1;client.stopped=True;fixture.stop_file.write_text('stop','utf-8')
                return dict(ok=True,state='stopped',sandbox_id=ident)
        self.provider=Provider();self.service=ProjectEnvironmentService(self.root/'services',client=self.provider)
        request=uuid.uuid4().hex
        self.service.store.begin(self.owner,'prepare',request,{},initial=dict(id=self.owner,scope=self.scope,
            repository='https://github.com/example/demo',ref='',sandbox_name='tebb-project-'+self.owner,
            sandbox_id=self.provider.ident,state='available',service=None,commit=self.baseline,prepare_operation=request))
        self.service.store.finish(request,'succeeded',result=dict(commit=self.baseline),changes=dict(state='available'))
        http=patch.object(self.service,'_http',return_value=dict(ok=True,status=200,bytes=1));http.start();self.addCleanup(http.stop)
        self.addCleanup(self.cleanup_service)

    def cleanup_service(self):
        self.provider.ident='original-sdk-uuid';self.service.stop(self.owner,self.scope);self.service.closed=True

    def start(self):
        result=self.service.start(self.owner,self.scope,uuid.uuid4().hex,'original web command',5000)
        self.assertTrue(result['ok'],result);return result

    def test_full_service_check_reuses_one_exec_and_stopped_query_never_reconnects(self):
        from desk_companion.project_integrity import valid_receipt
        self.start();self.assertEqual(len(self.calls),1)
        request=uuid.uuid4().hex;result=self.service.integrity(self.owner,self.scope,request)
        self.assertTrue(result['ok'],result);self.assertTrue(valid_receipt(result['integrity'],environment=self.owner,baseline=self.baseline))
        self.assertTrue(result['integrity']['unchanged']);self.assertFalse(result['integrity']['new_exec'])
        self.assertEqual(len(self.calls),1)
        self.assertTrue(self.service.stop(self.owner,self.scope)['ok'])
        stopped=self.service.integrity(self.owner,self.scope,uuid.uuid4().hex)
        self.assertFalse(stopped['ok']);self.assertFalse(stopped['integrity']['complete']);self.assertEqual(len(self.calls),1)
        old=self.service.integrity(self.owner,self.scope,request)
        self.assertTrue(old['replayed']);self.assertEqual(old['integrity'],result['integrity']);self.assertEqual(len(self.calls),1)

    def test_metadata_failure_preserves_ready_web_and_full_log_does_not_hide_receipt(self):
        initial=self.start();operation=initial['service']['operation']
        with patch('desk_companion.project_environments.LOG_LIMIT',64):
            self.service._append(operation,b'A'*128)
            proof=self.service.integrity(self.owner,self.scope,uuid.uuid4().hex)
            self.assertTrue(proof['ok'],proof);self.assertTrue(self.service.log(self.owner,self.scope,operation)['truncated'])
        self.git('config','core.fsmonitor','must-not-execute')
        failed=self.service.integrity(self.owner,self.scope,uuid.uuid4().hex)
        self.assertFalse(failed['ok']);self.assertEqual(failed['integrity']['reason'],'unsupported_git_configuration')
        self.assertTrue(self.service.status(self.owner,self.scope)['service_ready']);self.assertEqual(self.provider.stops,0)
        self.service.controls[operation].invalidate('synthetic_metadata_channel_failure')
        self.assertFalse(self.service.integrity(self.owner,self.scope,uuid.uuid4().hex)['ok'])
        self.assertTrue(self.service.status(self.owner,self.scope)['service_ready']);self.assertEqual(self.provider.stops,0)

    def test_wrong_identity_cancelled_owner_and_replaced_service_cannot_inspect(self):
        initial=self.start();control=self.service.controls[initial['service']['operation']]
        with patch.object(control,'inspect',side_effect=AssertionError('must not send inspection')):
            self.provider.ident='same-name-other-uuid'
            result=self.service.integrity(self.owner,self.scope,uuid.uuid4().hex)
            self.assertFalse(result['ok']);self.assertEqual(len(self.calls),1)
            self.provider.ident='original-sdk-uuid';self.service.owner_active=lambda task:False
            cancelled=self.service.integrity(self.owner,self.scope,uuid.uuid4().hex,owner_task='cancelled-owner')
            self.assertFalse(cancelled['ok']);self.assertEqual(cancelled['operation']['state'],'cancelled')
            replacement={**initial['service'],'id':'different-service'}
            self.service.store.observe(self.owner,service=replacement)
            self.assertFalse(self.service.integrity(self.owner,self.scope,uuid.uuid4().hex)['ok'])
        self.assertEqual(self.provider.stops,0)

    def test_cancellation_after_reply_cannot_register_success_or_stop_web(self):
        initial=self.start();control=self.service.controls[initial['service']['operation']];original=control.inspect
        def cancel_after_reply(*args,**kwargs):
            proof=original(*args,**kwargs);self.service.owner_active=lambda task:False;return proof
        with patch.object(control,'inspect',side_effect=cancel_after_reply):
            result=self.service.integrity(self.owner,self.scope,uuid.uuid4().hex,owner_task='worker')
        self.assertFalse(result['ok']);self.assertFalse(result['integrity']['complete'])
        self.assertIsNone(result['integrity']['unchanged']);self.assertEqual(result['operation']['state'],'cancelled')
        self.assertEqual(self.provider.stops,0);self.assertTrue(self.service.status(self.owner,self.scope)['service_ready'])

    def test_product_stop_waits_for_original_inspection_and_then_invalidates_pipe(self):
        from concurrent.futures import ThreadPoolExecutor
        initial=self.start();control=self.service.controls[initial['service']['operation']];original=control.inspect
        entered=threading.Event();release=threading.Event()
        def blocked(*args,**kwargs):entered.set();release.wait(5);return original(*args,**kwargs)
        with patch.object(control,'inspect',side_effect=blocked),ThreadPoolExecutor(max_workers=2)as executor:
            inspection=executor.submit(self.service.integrity,self.owner,self.scope,uuid.uuid4().hex)
            try:
                self.assertTrue(entered.wait(3));stopping=executor.submit(self.service.stop,self.owner,self.scope)
                time.sleep(.05);self.assertEqual(self.provider.stops,0)
            finally:release.set()
            self.assertTrue(inspection.result(10)['ok']);self.assertTrue(stopping.result(10)['ok'])
        self.assertTrue(control.closed);self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
