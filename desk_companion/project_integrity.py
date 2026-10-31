"""Fixed metadata channel in an already-authorized foreground SDK session.

It never opens an SDK connection. Web output is data, never a control receipt.
"""
import base64
import json
import math
import os
import re
import threading
import time
import uuid


def valid_receipt(value,*,environment=None,baseline=None):
    """Validate only the fixed producer's observation scope, not an entire goal."""
    if not isinstance(value,dict) or value.get('complete')is not True:return False
    ident=value.get('environment')
    if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident) or value.get('owner')!=ident:return False
    if environment is not None and ident!=environment:return False
    base=value.get('baseline_commit');head=value.get('current_commit')
    if not all(isinstance(i,str)and re.fullmatch('[a-f0-9]{40,64}',i)for i in (base,head)):return False
    if baseline is not None and base!=baseline:return False
    if type(value.get('tracked_changes'))is not bool or type(value.get('unchanged'))is not bool:return False
    if value['unchanged']!=(head==base and not value['tracked_changes']):return False
    if (value.get('scope')!='tracked_files_at_observation' or value.get('untracked_excluded')is not True
        or value.get('new_exec')is not False or value.get('transport')!='existing_service_pipe'):return False
    if not all(isinstance(value.get(key),str)and value[key]for key in ('sandbox_id','service_id','start_operation','request_id')):return False
    if not isinstance(value.get('guest_session'),str)or not re.fullmatch('[a-f0-9]{32}',value['guest_session']):return False
    if not all(type(value.get(key))in (int,float)and math.isfinite(value[key])and value[key]>0
               for key in ('observed_at','observation_finished_at','received_at')):return False
    return value['observation_finished_at']>=value['observed_at']


def observation_current_for_actions(receipts,selected,environment):
    """A later potentially mutating product action invalidates the old checkpoint."""
    position=next((index for index,row in enumerate(receipts)if row is selected),None)
    if position is None:return False
    mutating={'project_prepare','project_write','project_execute','project_start','project_reconcile'}
    for row in receipts[position+1:]:
        if row.get('tool')not in mutating:continue
        raw=row.get('result');result=raw if isinstance(raw,dict)else{}
        saved=row.get('environment')or result.get('environment')or{}
        arguments=row.get('arguments');arguments=arguments if isinstance(arguments,dict)else{}
        ident=arguments.get('environment')or (saved.get('id')if isinstance(saved,dict)else saved)
        if ident==environment:return False
    return True


GIT_PROBE = r'''
def inspect_source(project,baseline,owner):
    import os,re,subprocess,tempfile,time
    started=time.time();deadline=time.monotonic()+5
    result=dict(complete=False,unchanged=None,scope='tracked_files_at_observation',untracked_excluded=True,
                baseline_commit=baseline,owner=owner,observed_at=started)
    try:
        if not re.fullmatch('[a-f0-9]{40,64}',baseline):raise ValueError('missing_original_baseline')
        env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_SYSTEM=os.devnull,GIT_CONFIG_NOSYSTEM='1',
                   GIT_NO_REPLACE_OBJECTS='1',GIT_OPTIONAL_LOCKS='0',GIT_TERMINAL_PROMPT='0')
        def git(*args,allowed=(0,)):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('source_check_timeout')
            with tempfile.TemporaryFile() as out:
                process=subprocess.run(['git','--no-optional-locks','-C',project,'-c','core.fsmonitor=false',
                    '-c','core.hooksPath='+os.devnull,'-c','diff.external=','-c','diff.autoRefreshIndex=false','-c','core.quotePath=true',*args],
                    stdout=out,stderr=subprocess.DEVNULL,env=env,timeout=remaining)
                out.seek(0);body=out.read(65537)
            if len(body)>65536:raise ValueError('source_check_output_limit')
            if process.returncode not in allowed:raise ValueError('git_check_failed')
            return process.returncode,body
        _,unsafe=git('config','--local','--no-includes','--get-regexp',
            r'^(filter\.|diff\.|include\.|includeif\.|core\.(fsmonitor|hookspath|ignorestat|sparsecheckout|worktree)|index\.sparse)',allowed=(0,1))
        if unsafe:raise ValueError('unsupported_git_configuration')
        _,stages=git('ls-files','--stage')
        if any(line.startswith(b'160000 ') for line in stages.splitlines()):raise ValueError('submodule_scope_unsupported')
        _,flags=git('ls-files','-v')
        if any(line and (line[:1].islower() or line.startswith(b'S '))for line in flags.splitlines()):
            raise ValueError('hidden_index_entries')
        _,head=git('rev-parse','HEAD');head=head.strip().decode('ascii')
        if not re.fullmatch('[a-f0-9]{40,64}',head):raise ValueError('invalid_git_head')
        # Name-only/raw/exit status may include stat-only entries when index
        # refresh is disabled. Numstat compares content; summary retains modes.
        _,changes=git('diff','--no-ext-diff','--no-textconv','--no-renames','--ignore-submodules=none','--exit-code','--numstat','--summary',baseline,'--',allowed=(0,1))
        _,changes_again=git('diff','--no-ext-diff','--no-textconv','--no-renames','--ignore-submodules=none','--exit-code','--numstat','--summary',baseline,'--',allowed=(0,1))
        _,after=git('rev-parse','HEAD');_,after_flags=git('ls-files','-v')
        if after.strip()!=head.encode() or changes!=changes_again or flags!=after_flags:raise ValueError('source_changed_during_check')
        result.update(complete=True,current_commit=head,tracked_changes=bool(changes),
                      unchanged=head==baseline and not changes,head_matches_baseline=head==baseline)
    except Exception as exc:
        result['reason']=str(exc) if isinstance(exc,(ValueError,TimeoutError)) else type(exc).__name__
    result['observation_finished_at']=time.time()
    return result
'''


RUNNER = r'''
import base64,json,os,subprocess,sys,threading,time,uuid
config=json.loads(base64.b64decode(sys.argv[1]));token=config['token'];guest=uuid.uuid4().hex
write_lock=threading.Lock()
def emit(kind,**value):
    data=b'\x1eTEBB1 '+token.encode()+b' '+json.dumps(dict(kind=kind,guest_session=guest,**value),separators=(',',':')).encode()+b'\n'
    with write_lock:sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
owner=os.environ.get('TEBB_ENV_ID','')
if owner!=config['environment']:
    emit('error',reason='identity_changed');raise SystemExit(125)
''' + GIT_PROBE + r'''
app=subprocess.Popen(config['argv'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
def forward(stream,name):
    while True:
        data=os.read(stream.fileno(),3072)
        if not data:break
        emit('output',stream=name,data=base64.b64encode(data).decode())
readers=[threading.Thread(target=forward,args=(stream,name),daemon=True) for stream,name in ((app.stdout,'stdout'),(app.stderr,'stderr'))]
emit('ready',owner=owner)
for reader in readers:reader.start()
def control():
    os.set_blocking(sys.stdin.fileno(),False);buffer=bytearray()
    while app.poll()is None:
        try:
            if b'\n'not in buffer:
                try:data=os.read(sys.stdin.fileno(),512)
                except BlockingIOError:time.sleep(.02);continue
                if not data:break
                buffer.extend(data)
            if b'\n'not in buffer:
                if len(buffer)>512:buffer.clear()
                continue
            line,_,remaining=buffer.partition(b'\n');buffer=bytearray(remaining)
            if len(line)>512:continue
            request=json.loads(line)
            if set(request)!={'operation','request','guest_session'} or request['operation']!='source_integrity':continue
            if request['guest_session']!=guest or not isinstance(request['request'],str) or not 8<=len(request['request'])<=96:continue
            if app.poll()is not None:continue
            proof=inspect_source(config['project'],config['baseline'],owner)
            if app.poll()is not None:proof.update(complete=False,unchanged=None,reason='web_process_exited')
            emit('integrity',request=request['request'],proof=proof)
        except (OSError,ValueError,TypeError,KeyError):continue
controller=threading.Thread(target=control,daemon=True);controller.start()
code=app.wait();emit('exit',exit_code=code)
controller.join(6)
for reader in readers:reader.join(2)
raise SystemExit(code if code>=0 else 128-code)
'''


class IntegritySession:
    def __init__(self,binding):
        self.binding=dict(binding);self.token=uuid.uuid4().hex;self.process=None
        self.condition=threading.Condition();self.writer=threading.Lock();self.buffer=bytearray()
        self.guest='';self.ready=False;self.app_running=False;self.closed='';self.pending={};self.seen_ready=False

    def command(self,project,script):
        config=dict(token=self.token,environment=self.binding['environment'],baseline=self.binding['baseline_commit'],
                    project=project,argv=['bash','-lc',script])
        return ['python3','-I','-u','-c',RUNNER,base64.b64encode(json.dumps(config).encode()).decode()]

    def attach(self,process):
        self.process=process
        try:os.set_blocking(process.stdin.fileno(),False)
        except (OSError,AttributeError):self.invalidate('control_pipe_unavailable')

    def invalidate(self,reason,*,app_exited=False):
        with self.condition:
            self.closed=self.closed or reason;self.ready=False
            if app_exited:self.app_running=False
            self.condition.notify_all()

    def close(self,reason='control_pipe_closed'):
        self.invalidate(reason,app_exited=True)
        if self.process is not None and self.process.stdin is not None:
            try:self.process.stdin.close()
            except (OSError,ValueError):pass

    def feed(self,data,stdout=True):
        if not stdout:return [(data,False)]
        prefix=b'\x1eTEBB1 '+self.token.encode()+b' ';out=[]
        with self.condition:
            self.buffer.extend(data)
            while self.buffer:
                position=self.buffer.find(prefix)
                if position<0:
                    count=max(0,len(self.buffer)-len(prefix)+1)
                    if count:out.append((bytes(self.buffer[:count]),True));del self.buffer[:count]
                    break
                if position:out.append((bytes(self.buffer[:position]),True));del self.buffer[:position]
                end=self.buffer.find(b'\n',len(prefix))
                if end<0:
                    if len(self.buffer)>16384:self.invalidate('control_frame_limit');out.append((bytes(self.buffer),True));self.buffer.clear()
                    break
                raw=bytes(self.buffer[len(prefix):end]);del self.buffer[:end+1]
                try:
                    value=json.loads(raw);kind=value['kind'];guest=value['guest_session']
                    if kind=='ready':
                        if self.seen_ready or value.get('owner')!=self.binding['environment'] or not re.fullmatch('[a-f0-9]{32}',guest):
                            self.invalidate('control_generation_changed',app_exited=True);continue
                        self.seen_ready=True;self.guest=guest
                        if not self.closed:self.ready=True;self.app_running=True
                    elif kind=='output':
                        if value.get('stream')not in {'stdout','stderr'}:raise ValueError('invalid output stream')
                        body=base64.b64decode(value['data'],validate=True)
                        if len(body)>3072:raise ValueError('output limit')
                        out.append((body,value['stream']=='stdout'))
                    elif kind=='integrity':
                        if guest==self.guest and not self.closed and value.get('request')in self.pending:
                            self.pending[value['request']]=value['proof']
                    elif kind in {'exit','error'}:self.invalidate(value.get('reason','web_process_exited'),app_exited=True)
                    else:raise ValueError('invalid control frame')
                    self.condition.notify_all()
                except (ValueError,KeyError,TypeError):self.invalidate('invalid_control_frame')
        return out

    def eof(self,stdout=True):
        if not stdout:return []
        with self.condition:
            remainder=bytes(self.buffer);self.buffer.clear();self.invalidate('control_pipe_eof',app_exited=True)
        return [(remainder,True)]if remainder else []

    def inspect(self,request,check,*,timeout=7):
        deadline=time.monotonic()+timeout
        if not self.writer.acquire(timeout=timeout):raise TimeoutError('source_check_busy')
        try:
            check()
            with self.condition:
                if self.closed or not self.ready or not self.app_running or self.process is None or self.process.poll()is not None:
                    raise RuntimeError(self.closed or 'no_live_integrity_channel')
                self.pending[request]=None
                body=(json.dumps(dict(operation='source_integrity',request=request,guest_session=self.guest))+'\n').encode()
                try:
                    if os.write(self.process.stdin.fileno(),body)!=len(body):raise OSError('partial control write')
                except (OSError,ValueError):self.invalidate('control_pipe_write_failed');raise RuntimeError(self.closed)
            while True:
                check()
                with self.condition:
                    if self.closed:raise RuntimeError(self.closed)
                    if self.pending[request]is not None:return self.pending[request]
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('source_check_timeout')
                    self.condition.wait(min(.1,remaining))
        finally:
            with self.condition:self.pending.pop(request,None)
            self.writer.release()
