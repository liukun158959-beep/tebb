"""Parent-owned project operations and foreground official sandbox sessions.

This service owns infrastructure, not a model loop or a second task queue.
Callers authenticate the run and supply its immutable scope/owner snapshot.
"""
import base64
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import socket
import subprocess
import threading
import time
import uuid

from .docker_sandboxes import SandboxesClient, SandboxError, NO_WINDOW, port, cli_environment, diagnostic_text
from .project_environment_store import EnvironmentStore

LOG_LIMIT=4*1024*1024
PROJECT='/home/agent/project'


def repository(value):
    if not isinstance(value,str) or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?',value):
        raise ValueError('首版仅支持无凭据的公开GitHub HTTPS仓库地址。')
    if value.rsplit('/',1)[-1] in {'.','..'}:raise ValueError('仓库名称无效。')
    return value.removesuffix('.git')


def relative_path(value):
    if not isinstance(value,str) or not value or len(value)>240 or '\\' in value or '\0' in value:
        raise ValueError('项目文件路径无效。')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:raise ValueError('请使用项目内相对路径。')
    return str(path)


def request_id(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{8,96}',value):raise ValueError('项目操作请求编号无效。')
    return value


class OperationCancelled(RuntimeError):pass


class HealthCheckFailure(RuntimeError):
    def __init__(self,message,health):
        super().__init__(message);self.details=dict(last_health=health)


class ProjectEnvironmentService:
    def __init__(self,path,*,client=None,owner_active=lambda task:True):
        self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
        self.store=EnvironmentStore(self.path/'environments.sqlite3')
        self.store.recover_pending()
        self.context=threading.local();self.lock=threading.RLock();self.processes={};self.readers={};self.runtime_locks={};self.closed=False
        self.owner_active=owner_active
        self.client=client or SandboxesClient()
        if isinstance(self.client,SandboxesClient):self.client.observe_control(self._record_control)

    def _log_path(self,operation):
        return self.path/'logs'/(request_id(operation)+'.log')

    def _append(self,operation,data):
        if not operation:return False
        data=data.encode('utf-8',errors='replace') if isinstance(data,str) else data
        path=self._log_path(operation)
        with self.lock:
            path.parent.mkdir(exist_ok=True)
            size=path.stat().st_size if path.exists() else 0
            remaining=max(0,LOG_LIMIT-size)
            with path.open('ab') as stream:stream.write(data[:remaining])
            return len(data)>remaining

    def _record_control(self,command,*,result=None,error=None):
        operation=getattr(self.context,'operation','')
        if not operation:return None
        truncated=[]
        def append(value):
            truncated.append(self._append(operation,diagnostic_text(value)))
        if error is not None:
            for output in (getattr(error,'stdout',None),getattr(error,'stderr',None)):
                if output:append(output)
            append('\n[official CLI '+command[1]+' '+type(error).__name__+'; receipt requires checking]\n')
            return not any(truncated)
        # ls includes other users' locally owned SDK resources; it is not this project's log.
        outputs=(result.stderr,) if command[1] in {'ls','policy'} else (result.stdout,result.stderr)
        for output in outputs:
            if output:append(output)
        append(f'\n[official CLI {command[1]} exit={result.returncode}]\n')
        return not any(truncated)

    @contextmanager
    def _context(self,operation):
        previous=getattr(self.context,'operation','');self.context.operation=operation
        try:yield
        finally:self.context.operation=previous

    def _environment(self,ident,scope):
        if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('项目环境编号无效。')
        environment=self.store.get(ident)
        if not scope or environment['scope']!=scope:raise ValueError('此项目环境不属于当前项目或会话。')
        return environment

    def _check(self,operation):
        record=self.store.operation(operation)
        if self.closed or record['cancel_requested'] or record['owner_task'] and not self.owner_active(record['owner_task']):
            self.store.cancel(record['environment']);raise OperationCancelled('项目操作已取消，正在核对停止状态。')

    def _spawn(self,environment,operation,script):
        self._check(operation)
        command=self.client.exec_command(environment['sandbox_name'],environment['sandbox_id'],['bash','-lc',script])
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 creationflags=NO_WINDOW,env=cli_environment())
        with self.lock:self.processes[operation]=process
        overflow=threading.Event();tail=bytearray()
        def read(stream,stdout):
            while True:
                try:chunk=os.read(stream.fileno(),4096)
                except (OSError,ValueError):break
                if not chunk:break
                if stdout:
                    tail.extend(chunk)
                    if len(tail)>131072:del tail[:-131072]
                if self._append(operation,chunk):overflow.set()
        readers=[threading.Thread(target=read,args=(stream,stdout),daemon=True)
                 for stream,stdout in ((process.stdout,True),(process.stderr,False))]
        for reader in readers:reader.start()
        with self.lock:self.readers[operation]=readers
        return process,readers,overflow,tail

    def _exec(self,environment,operation,script,timeout=120):
        """Capture untrusted output as bytes; only the CLI exit code is a control signal."""
        process,readers,overflow,tail=self._spawn(environment,operation,script)
        deadline=time.monotonic()+timeout
        try:
            while process.poll() is None:
                self._check(operation)
                if overflow.is_set():raise RuntimeError('命令输出达到4MiB上限，已请求停止环境。')
                if time.monotonic()>=deadline:raise TimeoutError('项目命令达到时限，正在核对停止状态。')
                time.sleep(.1)
            for reader in readers:reader.join(2)
            if any(reader.is_alive() for reader in readers):raise RuntimeError('命令退出后输出管道尚未关闭，需核对环境停止。')
            result=dict(exit_code=process.returncode,stdout=bytes(tail).decode('utf-8',errors='replace'),log_id=operation)
            self._append(operation,f'\n[guest command exit={process.returncode}]\n')
            if process.returncode:raise RuntimeError(f'项目命令退出码{process.returncode}，详见日志{operation}。')
            return result
        finally:
            if process.poll() is not None:
                for reader in readers:reader.join(2)
                process.stdout.close();process.stderr.close()
                with self.lock:self.processes.pop(operation,None);self.readers.pop(operation,None)

    def _runtime_lock(self,ident):
        with self.lock:return self.runtime_locks.setdefault(ident,threading.RLock())

    def _stop_runtime(self,environment):
        with self._runtime_lock(environment['id']):return self._stop_locked(environment)

    def _stop_locked(self,environment):
        ident=environment.get('sandbox_id')
        if not ident:raise SandboxError('outcome_unknown','创建回执尚未核对，未操作同名环境。',unknown=True)
        service=environment.get('service') or {}
        mapping_error=None
        if service.get('host_port') and not service.get('port_unpublish_confirmed'):
            try:self.client.unpublish(environment['sandbox_name'],ident,service['guest_port'],service['host_port'])
            except SandboxError as exc:mapping_error=exc
        stopped=self.client.stop(environment['sandbox_name'],ident)
        with self.lock:owned=[(key,value) for key,value in self.processes.items()
                              if self.store.operation(key)['environment']==environment['id']]
        for key,process in owned:
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()  # This exact Popen object belongs to this service, never a restored PID.
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
            with self.lock:readers=self.readers.get(key,[])
            for reader in readers:reader.join(2)
            if process.stdout:process.stdout.close()
            if process.stderr:process.stderr.close()
            with self.lock:self.processes.pop(key,None);self.readers.pop(key,None)
        if mapping_error:raise SandboxError('port_cleanup_unconfirmed','环境已停止，端口映射撤销尚未确认，请核对原环境。',unknown=True)
        stopped['port_unpublish_confirmed']=True if service.get('host_port') else None
        return stopped

    def _failure(self,operation,exc):
        record=self.store.operation(operation);environment=self.store.get(record['environment'])
        log_error=False
        try:self._append(operation,'\n['+type(exc).__name__+'] '+str(exc)+'\n')
        except OSError:log_error=True
        error=str(exc);unknown=isinstance(exc,SandboxError) and exc.outcome_unknown
        stopped=False
        with self.lock:process=self.processes.get(operation)
        read_finished=record['kind']=='read' and (process is None or process.poll() is not None)
        preserve_service=record['kind']=='read' and (read_finished or (environment.get('service') or {}).get('state')=='ready')
        if preserve_service:
            unknown=unknown or bool(process and process.poll() is None)
        elif environment.get('sandbox_id'):
            try:self._stop_runtime(environment);stopped=True
            except Exception as stop_exc:
                unknown=True;self._append(operation,'\n[stop unconfirmed] '+str(stop_exc)+'\n')
        elif isinstance(exc,OperationCancelled):unknown=True
        state='unknown' if unknown and not stopped else 'cancelled' if isinstance(exc,OperationCancelled) or record['cancel_requested'] else 'failed'
        changes=dict(state=environment['state'] if preserve_service else state,
                     service=environment.get('service') if preserve_service or state=='unknown' else None)
        details=dict(getattr(exc,'details',{}))
        if log_error:details['diagnostics_available']=False
        environment,record=self.store.finish(operation,state,error=error,changes=changes,error_details=details)
        return dict(ok=False,environment=environment,operation=record,error=error,log_id=operation,stop_confirmed=stopped,
                    details=details)

    def prepare(self,scope,request,repo,*,ref='',owner_task='',owner_run='',conversation='',project_id=''):
        repo=repository(repo);request=request_id(request)
        if not isinstance(scope,str) or not scope or len(scope)>180:raise ValueError('项目作用域无效。')
        if not isinstance(ref,str) or len(ref)>128 or ref and (not re.fullmatch('[A-Za-z0-9_./-]+',ref) or ref.startswith('-') or '..' in ref):
            raise ValueError('仓库引用无效。')
        if self.closed:raise RuntimeError('应用正在退出，未创建项目环境。')
        ident=uuid.uuid4().hex
        initial=dict(id=ident,scope=scope,repository=repo,ref=ref,sandbox_name='tebb-project-'+ident,
                     sandbox_id='',state='creating',service=None,created=time.time(),owner_run=owner_run,
                     conversation=conversation,project_id=project_id,commit='',dependencies='not_configured',prepare_operation=request)
        environment,operation,fresh=self.store.begin(ident,'prepare',request,dict(repository=repo,ref=ref),owner_task,initial=initial)
        if not fresh:
            self._environment(environment['id'],scope)
            return dict(ok=operation['state']=='succeeded',environment=environment,operation=operation,replayed=True)
        try:
            guard=self.store.creation_guard(request);guard.acquire()
        except OSError as exc:
            return self._failure(request,exc)  # No SDK call has begun; admission can finish failed.
        try:
            with self._context(request):
                try:
                    self._check(request)
                    self.store.checkpoint(request,'create')
                    observed=self.client.create(environment['sandbox_name'],environment['id'])
                    environment=self.store.bind_runtime(request,observed['id'])
                    self._check(request)
                    self.store.checkpoint(request,'network_policy')
                    network=self.client.dependency_network(environment['sandbox_name'],environment['sandbox_id'])
                    self.store.checkpoint(request,'clone',network=network)
                    self.store.checkpoint(request,'clone',state='preparing')
                    checkout=''
                    if ref:checkout='git -c credential.helper= fetch --depth 1 origin '+shlex.quote(ref)+'\ngit checkout --detach FETCH_HEAD\n'
                    clone=('set -eu\nexport GIT_TERMINAL_PROMPT=0 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null\n'
                           'test ! -e '+PROJECT+'\ngit -c credential.helper= -c core.hooksPath=/dev/null clone --depth 1 -- '+shlex.quote(repo)+' '+PROJECT+'\n'
                           'cd '+PROJECT+'\n'+checkout+'git rev-parse HEAD\n')
                    result=self._exec(environment,request,clone)
                    commit=result['stdout'].strip().splitlines()[-1]
                    if not re.fullmatch('[a-f0-9]{40,64}',commit):raise RuntimeError('未取得实际仓库提交编号。')
                    self.store.checkpoint(request,'install',commit=commit)
                    install=('set -eu\ncd '+PROJECT+'\n'
                             'if test -f package.json; then\n'
                             '  if test -f package-lock.json; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi\n'
                             'elif test -f requirements.txt || test -f pyproject.toml || test -f setup.py; then\n'
                             '  python3 -m venv .venv\n'
                             '  if test -f requirements.txt; then .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt; '
                             'else .venv/bin/python -m pip install --disable-pip-version-check -e .; fi\n'
                             'else echo "No supported dependency manifest; inspect README and configure explicitly." >&2; exit 42; fi\n')
                    self._exec(environment,request,install,timeout=240)
                    runtime_code=('from pathlib import Path;import json,subprocess;'
                        'node=Path("package.json").is_file();'
                        'info=dict(language="node",node=subprocess.check_output(["node","--version"],text=True).strip(),'
                        'npm=subprocess.check_output(["npm","--version"],text=True).strip(),start_hint="npm start") if node else '
                        'dict(language="python",python=subprocess.check_output([".venv/bin/python","--version"],text=True).strip(),'
                        'python_command=".venv/bin/python",activate="source .venv/bin/activate",flask_command=".venv/bin/flask");'
                        'print(json.dumps(info))')
                    runtime=json.loads(self._exec(environment,request,'cd '+PROJECT+'\npython3 -I -c '+shlex.quote(runtime_code),30)['stdout'])
                    self._check(request)
                    environment,operation=self.store.finish(request,'succeeded',result=dict(commit=commit,log_id=request),
                                                           changes=dict(state='available',dependencies='installed',runtime=runtime))
                    return dict(ok=True,environment=environment,operation=operation,service_ready=False,log_id=request)
                except Exception as exc:return self._failure(request,exc)
        finally:guard.__exit__()

    def execute(self,ident,scope,request,command,*,timeout=120,owner_task=''):
        environment=self._environment(ident,scope);request=request_id(request)
        if not isinstance(command,str) or not command.strip() or len(command)>16000 or '\0' in command:raise ValueError('项目命令无效或过长。')
        if type(timeout) is not int or not 1<=timeout<=240:raise ValueError('命令时限须为1–240秒。')
        environment,operation,fresh=self.store.begin(ident,'execute',request,dict(command=command,timeout=timeout),owner_task)
        if not fresh:return dict(ok=operation['state']=='succeeded',environment=environment,operation=operation,replayed=True)
        with self._context(request):
            try:
                if not environment.get('network'):
                    network=self.client.dependency_network(environment['sandbox_name'],environment['sandbox_id'])
                    self.store.checkpoint(request,'network_policy',network=network)
                result=self._exec(environment,request,'set -eu\nmkdir -p '+PROJECT+'\ncd '+PROJECT+'\n'+command,timeout)
                self._check(request)
                environment,operation=self.store.finish(request,'succeeded',result=result,changes=dict(state='available'))
                return dict(ok=True,environment=environment,operation=operation,**result)
            except Exception as exc:return self._failure(request,exc)

    def read(self,ident,scope,request,path,*,owner_task=''):
        environment=self._environment(ident,scope);request=request_id(request);path=relative_path(path)
        environment,operation,fresh=self.store.begin(ident,'read',request,dict(path=path),owner_task)
        if not fresh:return dict(ok=operation['state']=='succeeded',environment=environment,operation=operation,replayed=True)
        code=('from pathlib import Path\nimport json,base64,hashlib\n'
              'root=Path('+repr(PROJECT)+').resolve();p=(root/'+repr(path)+').resolve()\n'
              'assert p.is_relative_to(root),"Path outside project"\n'
              'if p.is_dir():\n'
              '    children=sorted(p.iterdir(),key=lambda item:item.name)\n'
              '    entries=[dict(name=item.name,type="symlink" if item.is_symlink() else "directory" if item.is_dir() else "file") for item in children[:200]]\n'
              '    result=dict(path='+repr(path)+',kind="directory",entries=entries,truncated=len(children)>200)\n'
              'else:\n'
              '    assert p.is_file() and p.stat().st_size<=65536,"File unavailable or exceeds 64KiB"\n'
              '    data=p.read_bytes();result=dict(path='+repr(path)+',kind="file",base64=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest())\n'
              'print(json.dumps(result))\n')
        with self._context(request):
            try:
                raw=self._exec(environment,request,'python3 -I -c '+shlex.quote(code),30)
                result=json.loads(raw['stdout'])
                if result.get('kind')=='directory':
                    if not isinstance(result.get('entries'),list) or len(result['entries'])>200:raise ValueError('项目目录列表校验失败。')
                else:
                    content=base64.b64decode(result['base64'],validate=True)
                    if len(content)>65536 or hashlib.sha256(content).hexdigest()!=result['sha256']:raise ValueError('项目文件读取内容校验失败。')
                    try:result.update(text=content.decode('utf-8'),encoding='utf-8')
                    except UnicodeDecodeError:result.update(text=None,encoding='binary')
                self._check(request)
                environment,operation=self.store.finish(request,'succeeded',result=result)
                return dict(ok=True,environment=environment,operation=operation,**result)
            except Exception as exc:return self._failure(request,exc)

    def write(self,ident,scope,request,path,text,*,owner_task=''):
        path=relative_path(path)
        if not isinstance(text,str) or len(text.encode('utf-8'))>8192:raise ValueError('单次文件写入最多8KiB，请分拆修改。')
        encoded=base64.b64encode(text.encode('utf-8')).decode()
        code=('from pathlib import Path;import base64,hashlib,json;'
              'root=Path('+repr(PROJECT)+').resolve();p=(root/'+repr(path)+').resolve();'
              'assert p.is_relative_to(root),"Path outside project";'
              'p.parent.mkdir(parents=True,exist_ok=True);data=base64.b64decode('+repr(encoded)+');'
              'p.write_bytes(data);print(json.dumps(dict(path='+repr(path)+',bytes=len(data),sha256=hashlib.sha256(data).hexdigest())))')
        return self.execute(ident,scope,request,'python3 -I -c '+shlex.quote(code),timeout=30,owner_task=owner_task)

    def log(self,ident,scope,operation,offset=0):
        self._environment(ident,scope);record=self.store.operation(request_id(operation))
        if record['environment']!=ident:raise ValueError('日志不属于此项目环境。')
        if type(offset) is not int or offset<0:raise ValueError('日志位置无效。')
        path=self._log_path(operation);size=path.stat().st_size if path.exists() else 0
        if offset>size:raise ValueError('日志位置超过已保存输出。')
        content=b''
        if path.exists():
            with path.open('rb') as stream:stream.seek(offset);content=stream.read(65536)
        with self.lock:process=self.processes.get(operation)
        active=bool(process and process.poll() is None)
        return dict(ok=True,environment=ident,operation=operation,base64=base64.b64encode(content).decode(),
                    offset=offset+len(content),size=size,done=not active and (record['state'] not in {'running','unknown','stopping'} or bool(record.get('creation_archive')) and not self.store.creation_active(operation)),
                    creation_outcome_unknown=record['kind']=='prepare' and record['state']=='unknown',
                    truncated=size>=LOG_LIMIT,limit=LOG_LIMIT)

    @staticmethod
    def _http(host_port,expected=''):
        # Fixed loopback only, no OS proxy and no redirects to arbitrary host URLs.
        connection=http.client.HTTPConnection('127.0.0.1',host_port,timeout=2)
        try:
            connection.request('GET','/');response=connection.getresponse();body=response.read(65537)
            ok=response.status==200 and len(body)<=65536 and (not expected or expected.encode() in body)
            return dict(ok=bool(ok),status=response.status,bytes=min(len(body),65536),sha256=hashlib.sha256(body[:65536]).hexdigest())
        except (OSError,http.client.HTTPException):return dict(ok=False,status=None)
        finally:connection.close()

    def start(self,ident,scope,request,command,guest_port,*,expected='',owner_task=''):
        environment=self._environment(ident,scope);request=request_id(request);guest_port=port(guest_port)
        if not isinstance(command,str) or not command.strip() or len(command)>16000 or '\0' in command:raise ValueError('启动命令无效或过长。')
        if not isinstance(expected,str) or len(expected)>200:raise ValueError('健康检查文本无效或过长。')
        environment,operation,fresh=self.store.begin(ident,'start',request,dict(command=command,guest_port=guest_port,expected=expected),owner_task)
        if not fresh:return dict(ok=operation['state']=='succeeded',environment=environment,operation=operation,replayed=True)
        with self._context(request):
            try:
                self._check(request)
                with socket.socket() as reserved:
                    reserved.bind(('127.0.0.1',0));host_port=reserved.getsockname()[1]
                service=dict(id=uuid.uuid4().hex,state='starting',operation=request,command=command,
                             host_port=host_port,guest_port=guest_port,log_id=request,url='')
                self.store.checkpoint(request,'publish',service=service)
                environment=self.store.get(ident)
                self.client.publish(environment['sandbox_name'],environment['sandbox_id'],guest_port,host_port)
                script=('set -eu\ncd '+PROJECT+f'\nexport HOST=0.0.0.0 PORT={guest_port}\nexec bash -c '+shlex.quote(command))
                process,readers,overflow,tail=self._spawn(environment,request,script)
                self.store.checkpoint(request,'health_check')
                deadline=time.monotonic()+40;health=dict(ok=False)
                while time.monotonic()<deadline:
                    self._check(request)
                    if process.poll() is not None:raise RuntimeError('前台服务进程已退出，项目尚未就绪，请查看启动日志。')
                    if overflow.is_set():raise RuntimeError('启动日志达到4MiB上限。')
                    health=self._http(host_port,expected)
                    if health['ok']:break
                    time.sleep(.2)
                if not health['ok']:
                    message=('HTTP已响应200，但正文不含指定文本；终端的Listening/Running日志不是HTTP正文条件。'
                             if health.get('status')==200 else '未通过本机HTTP健康检查，项目尚未就绪。')
                    raise HealthCheckFailure(message,health)
                self._check(request)
                if process.poll() is not None:raise RuntimeError('健康检查后前台服务已退出。')
                service.update(state='ready',url=f'http://127.0.0.1:{host_port}/',health=health,checked=time.time(),cli_pid=process.pid)
                environment,operation=self.store.finish(request,'succeeded',result=service,changes=dict(state='available',service=service))
                threading.Thread(target=self._watch,args=(ident,service['id'],process),daemon=True).start()
                return dict(ok=True,environment=environment,operation=operation,service_ready=True,service=service)
            except Exception as exc:return self._failure(request,exc)

    def _watch(self,ident,service_id,process):
        code=process.wait()
        with self._runtime_lock(ident):
            environment=self.store.get(ident);service=environment.get('service') or {}
            if self.closed or service.get('id')!=service_id or service.get('state')!='ready':return
            error=f'项目服务进程退出，退出码{code}。'
            confirmed=False
            try:self._stop_runtime(environment);confirmed=True
            except Exception as exc:error+='停止核对未完成：'+str(exc)
            service.update(state='failed' if confirmed else 'stopping',exit_code=code,url='',port_unpublish_confirmed=confirmed)
            self.store.observe_service(ident,service_id,service,error)

    def status(self,ident,scope):
        environment=self._environment(ident,scope)
        observed=None;error=''
        if environment.get('sandbox_id'):
            try:observed=self.client.owned(environment['sandbox_name'],environment['sandbox_id'])
            except SandboxError as exc:error=exc.public_message
        service=environment.get('service') or {}
        health=self._http(service['host_port']) if service.get('state') in {'ready','unknown'} else dict(ok=False)
        # A persisted PID is diagnostic only. After restart, never adopt or kill it.
        with self.lock:
            process=self.processes.get(service.get('operation'))
            held=bool(process and process.poll() is None)
        ready=bool(service.get('state')=='ready' and held and observed and observed['status']=='running' and health['ok'])
        operation_id=environment.get('active_operation') or (environment.get('prepare_operation') if environment.get('creation_archive') else '')
        operation=self.store.operation(operation_id) if operation_id else None
        return dict(ok=True,environment=environment,observed_runtime=observed,error=error,service_ready=ready,
                    health=health,archive_status=self.store.archive_status(ident),recovery_required=bool(environment['state']=='unknown' or (operation or {}).get('state')=='unknown'
                        or service.get('state') in {'ready','unknown'} and not held),operation=operation)

    def archive_unknown_create(self,ident,scope,expected_operation,request,*,confirm=False):
        self._environment(ident,scope)
        if confirm is not True:raise ValueError('请明确确认释放应用准入，创建结果仍未知。')
        expected_operation=request_id(expected_operation);request=request_id(request)
        environment,receipt,replayed=self.store.archive_create(ident,expected_operation,request)
        return dict(ok=True,environment=environment,operation=receipt,replayed=replayed,
                    admission_released=True,creation_outcome='unknown',stop_confirmed=False)

    def reconcile(self,ident,scope,*,verify=False,owner_task=''):
        environment=self._environment(ident,scope)
        if type(verify) is not bool:raise ValueError('归属验证选项无效。')
        original=environment.get('active_operation') or environment.get('prepare_operation')
        if verify and original:
            lock=self.store.creation_guard(original)
            if not lock.acquire(blocking=False):
                return dict(ok=False,environment=environment,error='原创建或归属验证仍在父后台执行，请稍后核对。')
            try:return self._reconcile(ident,scope,verify=verify,owner_task=owner_task)
            finally:lock.__exit__()
        return self._reconcile(ident,scope,verify=verify,owner_task=owner_task)

    def _reconcile(self,ident,scope,*,verify=False,owner_task=''):
        """Observe the old candidate, then explicitly verify its saved ownership marker."""
        environment=self._environment(ident,scope)
        known=environment.get('sandbox_id')
        original=self.store.operation(environment.get('active_operation') or environment['prepare_operation']) if environment.get('active_operation') or environment.get('prepare_operation') else None
        if verify and original and original['state']=='running' and not original['cancel_requested']:
            return dict(ok=False,environment=environment,error='原操作仍在执行，先观察原请求；未启动额外验证。')
        candidate=self.client.owned(environment['sandbox_name'],known) if known else next(
            (value for value in self.client.inventory() if value['name']==environment['sandbox_name']),None)
        if candidate is None:
            return dict(ok=False,environment=environment,error='尚未观察到原创建候选，未重新创建。',code='candidate_not_found')
        if not verify:
            return dict(ok=bool(known),environment=environment,candidate={key:candidate.get(key) for key in ('name','id','status')},
                        verification_required=not bool(known),error='' if known else '原回执ID未知；需明确验证候选归属，可能启动此候选以读取固定标记。')
        if not known and candidate.get('agent')!='sbx-kit-shell':
            return dict(ok=False,environment=environment,error='候选workload与原创建记录不符，未启动、绑定或停止。')
        probe=('import os,json,subprocess;from pathlib import Path;'
               'p=Path('+repr(PROJECT)+');repo=(p/".git").exists();'
               'origin=subprocess.check_output(["git","-C",str(p),"remote","get-url","origin"],text=True,timeout=4).strip() if repo else "";'
               'matched=origin.removesuffix(".git")=='+repr(environment['repository'])+';'
               'commit=subprocess.check_output(["git","-C",str(p),"rev-parse","HEAD"],text=True,timeout=4).strip() if matched else "";'
               'print(json.dumps(dict(owner=os.getenv("TEBB_ENV_ID",""),project_exists=p.is_dir(),repository_matches=matched,commit=commit)))')
        operation=environment.get('active_operation') or environment.get('prepare_operation')
        with self._context(operation):
            metadata=json.loads(self.client.capture(environment['sandbox_name'],candidate['id'],['python3','-I','-c',probe]))
            if metadata.get('owner')!=ident:
                return dict(ok=False,environment=environment,error='候选归属不符，未绑定或停止；请在Docker核对此候选的启动状态。',code='identity_changed')
            if type(metadata.get('project_exists')) is not bool or type(metadata.get('repository_matches')) is not bool:
                raise ValueError('原环境观察格式无效。')
            if not isinstance(metadata.get('commit'),str) or metadata['commit'] and not re.fullmatch('[a-f0-9]{40,64}',metadata['commit']):
                raise ValueError('原项目提交观察格式无效。')
            if not known:environment=self.store.bind_runtime(environment['prepare_operation'],candidate['id'])
            original=self.store.operation(environment.get('active_operation') or environment['prepare_operation']) if environment.get('active_operation') or environment.get('prepare_operation') else None
            if original and original['cancel_requested']:
                stopped=self.stop(ident,scope)
                return dict(**stopped,identity_verified=True)
            if original and original['kind']=='prepare' and original['state']=='unknown':
                environment,_=self.store.finish(original['id'],'failed',
                    error='原环境身份已核对；配置结果尚未确认，未自动重放。可按原环境文件和日志继续配置。',
                    changes=dict(state='observed' if metadata['repository_matches'] else 'created',commit=metadata['commit']))
            else:environment=self.store.observe(ident,commit=metadata['commit'] or environment.get('commit',''))
            if self.closed or candidate['status']=='stopped' and (owner_task and not self.owner_active(owner_task) or not environment.get('service')):
                stopped=self.stop(ident,scope)
                return dict(**stopped,identity_verified=True,repository_present=metadata['repository_matches'])
            result=self.status(ident,scope)
            return dict(**result,identity_verified=True,repository_present=metadata['repository_matches'],
                        project_directory_present=metadata['project_exists'])

    def stop(self,ident,scope):
        environment=self._environment(ident,scope)
        with self._runtime_lock(ident):
            environment=self.store.cancel(ident)
            with self._context(environment.get('active_operation') or (environment.get('service') or {}).get('operation','')):
                try:
                    stopped=self._stop_runtime(environment)
                    changes=dict(state='stopped',service=None,error='')
                    if environment.get('active_operation'):
                        environment,_=self.store.finish(environment['active_operation'],'cancelled',changes=changes)
                    else:environment=self.store.observe(ident,**changes)
                    return dict(environment=environment,**stopped)
                except Exception as exc:
                    return dict(ok=False,environment=self.store.get(ident),error=str(exc),stop_confirmed=False)

    def list(self,scope):
        return dict(ok=True,environments=[value for value in self.store.list() if value.get('scope')==scope])

    def shutdown(self):
        self.closed=True
        # Physical stops run independently; a slow environment must not delay the others.
        environments=self.store.list();results=[];lock=threading.Lock()
        for environment in environments:
            if environment.get('active_operation'):self.store.cancel(environment['id'])
        try:inventory={value['id']:value for value in self.client.inventory()} if environments else {}
        except Exception:inventory={}
        targets=[value for value in environments if value.get('active_operation') or value.get('service')
                 or value.get('sandbox_id') and inventory.get(value['sandbox_id'],{}).get('status',value['state'])!='stopped']
        def stop_one(environment):
            result=self.stop(environment['id'],environment['scope'])
            with lock:results.append(result)
        threads=[threading.Thread(target=stop_one,args=(value,),daemon=True) for value in targets]
        for thread in threads:thread.start()
        deadline=time.monotonic()+65
        for thread in threads:thread.join(max(0,deadline-time.monotonic()))
        return dict(ok=all(result['ok'] for result in results) and not any(thread.is_alive() for thread in threads),results=results)
