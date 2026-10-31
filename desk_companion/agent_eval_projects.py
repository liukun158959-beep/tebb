"""Project quality experiments through the normal authenticated parent and worker."""
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
BUG='def add(a, b):\n    return a - b\n'
FIX='def add(a, b):\n    return a + b\n'
TEST='import unittest\nfrom eval_math import add\nclass OriginalTests(unittest.TestCase):\n    def test_pairs(self):\n        for a,b in [(2,3),(5,-3),(-2,-4),(0,9)]:\n            self.assertEqual(add(a,b),a+b)\n'
TEST_COMMAND='.venv/bin/python -m unittest -v test_eval_math.py'
HOLD_COMMAND='python3 -u -c \'import time;print("tebb-eval-tool-active",flush=True);time.sleep(90)\''
PYTHON_SETUP='source .venv/bin/activate && pip install -e examples/tutorial && cd examples/tutorial && flask --app flaskr init-db'
PYTHON_START='source .venv/bin/activate && cd examples/tutorial && flask --app flaskr run --host 0.0.0.0 --port 5000'
REPOSITORIES={
    'node':('https://github.com/heroku/node-js-getting-started','8b8dec11fcf027522a857e0c831ee01b7124f673'),
    'python':('https://github.com/pallets/flask','49943b5a94413a530761350f04e896f76e8d620e'),
}


class ProjectPrerequisiteMissing(RuntimeError):pass


def read_json(path,default=None):
    deadline=time.monotonic()+1
    while True:
        try:return json.loads(Path(path).read_text('utf-8'))
        except (FileNotFoundError,json.JSONDecodeError):return default
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.01)


def atomic_json(path,value):
    path=Path(path);temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8')
    # Windows readers briefly deny replacement while their file handle is open.
    deadline=time.monotonic()+1
    while True:
        try:temporary.replace(path);return
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.01)


def prompt(scenario,variant,environment=''):
    if scenario=='coding':
        return ('这是隔离的合成代码修复验收，已有项目环境 environment_id='+environment+'。'
            '不要重新准备仓库；请实际读取eval_math.py与test_eval_math.py，修复add(a,b)使其返回和。'
            '保留原测试逐字不变，必须通过project_write修改实现，并原样执行验证命令：'+TEST_COMMAND+'。'
            '不要修改测试或只口头给补丁，依据真实退出码交付。')
    if scenario=='cancel':
        return ('这是隔离的活动工具取消验收，已有项目环境 environment_id='+environment+'。'
            '允许并必须先通过内部start_background_task派发执行任务（这是产品调度，不是命令后台化）。'
            '执行任务中不要重新准备仓库；通过project_execute原样前台运行下列命令并等待，'
            '禁止在命令中使用&、nohup或screen脱离该执行调用：'+HOLD_COMMAND+'。')
    repository,ref=REPOSITORIES[variant]
    details=('最终启动命令必须原样使用npm start，port=3000' if variant=='node' else
             '运行原examples/tutorial/flaskr，使用项目.venv解释器，安装教程并初始化SQLite；'
             '最终启动命令必须原样使用：'+PYTHON_START+'，port=5000')
    return (f'这是隔离公开项目验收。实际拉取{repository}，固定提交{ref}，读取实际项目文件；{details}。'
            '不要修改原源码/测试，不另写hello服务。经真实HTTP检查后查询project_status和project_log，'
            '停止前必须调用project_integrity取得原提交/跟踪文件未变的实际回执；untracked构建元数据单独说明。'
            '保留原服务/日志/校验回执，再使用project_stop停止原环境，最后简洁报告实际结果。')


def receipts_bound_to_environment(receipts,environment):
    bound=[value for value in receipts if value.get('ok')is True and value.get('tool','').startswith('project_')
           and value['tool']!='project_list']
    return bool(bound)and all(value.get('environment',{}).get('id')==environment for value in bound)


def final_test_receipt(receipts,command):
    expected=hashlib.sha256(command.encode()).hexdigest()
    last_write=max((index for index,value in enumerate(receipts)if value.get('tool')=='project_write'and value.get('ok')is True),default=-1)
    for index in range(len(receipts)-1,last_write,-1):
        value=receipts[index]
        if value.get('tool')=='project_execute'and value.get('command_sha256')==expected:return value
    return {}


def active_tool_marker(raw):
    # Echoed command text is not proof that the guest reached its print statement.
    return any(line.strip()==b'tebb-eval-tool-active'for line in raw.splitlines())


def original_cancel_command(tasks,owner):
    task=next((value for value in tasks if value['id']==owner),{})
    starts=[event['data']for event in task.get('events',[])if event['kind']=='tool_start'
            and isinstance(event.get('data'),dict)and event['data'].get('tool')=='project_execute']
    return bool(starts and starts[-1].get('query')==HOLD_COMMAND)


def judge(scenario,root,proof):
    """Negative receipt/identity/cleanup facts cannot be replaced by oral completion."""
    if not proof.get('sdk_stopped') or not proof.get('identity_matches') or not proof.get('model_workers_exited'):return False
    receipts=proof.get('model_receipts',[])
    succeeded={value.get('tool')for value in receipts if value.get('ok')is True}
    if scenario=='cancel':
        return bool(root.get('state')=='cancelled' and proof.get('active_guest_marker') and proof.get('cancel_requested')
                    and proof.get('original_tool_command')
                    and not proof.get('fallback_cleanup_used'))
    if root.get('state')!='succeeded':return False
    if scenario=='coding':
        return bool({'project_read','project_write','project_execute'}<=succeeded
                    and proof.get('original_tests_unchanged') and proof.get('implementation_changed')
                    and proof.get('model_test_exit_code')==0 and proof.get('verification_test_exit_code')==0)
    required={'project_prepare','project_read','project_start','project_status','project_log','project_integrity','project_stop'}
    return bool(required<=succeeded and proof.get('model_source_integrity') and proof.get('stopped_vm_not_resumed_for_inspection') and proof.get('checkout_matches') and proof.get('tracked_files_unchanged')
                and proof.get('original_service_command')
                and proof.get('http_ready_before_stop') and proof.get('actual_log_bytes',0)>0
                and proof.get('preview_unreachable') and proof.get('model_stop_same_environment'))


def execute(spec,*,profile,model_id,timeout,sample=1,official=False,variant=None,progress=lambda value:None):
    """One isolated case. Setup, model action, semantic proof and cleanup stay distinct."""
    from websockets.sync.client import connect
    from .agent_debug import listing
    from .agent_evals import call_evidence
    from .agent_eval_lifecycle import worker_alive
    scenario=spec['scenario']
    if variant not in {None,'node','python'}:raise ValueError('unsupported project variant')
    variant=variant or ('python'if scenario=='coding'or scenario=='clone'and sample%2==0 else'node')
    folder=Path(profile)/'project_cases'/(spec['id']+'-s'+str(sample)+'-'+uuid.uuid4().hex)
    folder.mkdir(parents=True)
    with socket.socket()as reserved:reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
    token=uuid.uuid4().hex;backend=None;tree=None;stream=None;root={};goal_root=None;goal_events=[];calls=[];tasks=[];run_id='';expired=False
    proof=dict(verified=False,scope='authenticated_project_lifecycle',provider_mode='official'if official else'fixture',
               variant=variant,scenario=scenario,case_directory=str(folder),cancel_requested=False,active_guest_marker=False,
               stage='parent_setup',timeout_seconds=timeout,worker_exit_grace_seconds=8,verification_grace_seconds=90,parent_exit_grace_seconds=110)
    def rpc(method,**args):
        with connect(f'ws://127.0.0.1:{port}/ws?token={token}',proxy=None,open_timeout=5,close_timeout=1)as ws:
            ws.send(json.dumps(dict(type='rpc',method=method,args=args)))
            envelope=json.loads(ws.recv(timeout=80))['result']
            if not envelope['ok']:raise RuntimeError(envelope.get('error','project RPC failed'))
            return envelope['result']
    def collect():
        nonlocal root,calls,tasks
        observed=read_json(folder/'progress.json',{})
        root=next((value for value in observed.get('runs',[])if value['id']==run_id),root)
        tasks=[value for value in observed.get('tasks',[])if value.get('session')=='collab-'+run_id]
        progress(dict(root_run=run_id,task_id=tasks[0]['id']if tasks else'',task_ids=[v['id']for v in tasks]))
        observed_calls={}
        for task in tasks:
            page=listing(task_id=task['id'])
            if page['has_more']:raise RuntimeError('project task exceeded bounded call observation page')
            observed_calls.update((value['id'],value)for value in page['items'])
        calls=list(observed_calls.values())
        progress(dict(root_run=run_id,task_id=tasks[0]['id']if tasks else'',task_ids=[v['id']for v in tasks],
            call_ids=[v['id']for v in calls],calls=[call_evidence(v)for v in calls],project_case=dict(proof),
            tools=[dict(kind=e['kind'],**e['data'])for task in tasks for e in task.get('events',[])if e['kind']in{'tool_start','tool_end'}and isinstance(e.get('data'),dict)]))
        return observed
    def stage(value):
        proof['stage']=value;progress(dict(project_case=copy.deepcopy(proof)))
    started=time.monotonic();deadline=started+timeout
    try:
        stage('parent_setup')
        stream=(folder/'backend.log').open('wb')
        env={**os.environ,'DESK_COMPANION_DATA_DIR':str(profile),'DESK_TERMINAL_PORT':str(port),'DESK_TERMINAL_TOKEN':token,'PYTHONUTF8':'1'}
        backend=subprocess.Popen([sys.executable,'-u',str(ROOT/'scripts/eval_project_host.py'),'--port',str(port),'--token',token,
            '--directory',str(folder),'--scenario',scenario,'--variant',variant,'--timeout',str(timeout),
            '--sdk','official'if official else'fixture'],cwd=ROOT,env=env,stdout=stream,stderr=stream,
            creationflags=0x08000000 if os.name=='nt'else 0)
        from .task_process import ProcessTree
        tree=ProcessTree(backend)
        while not(folder/'ready.json').exists():
            error=read_json(folder/'setup-error.json')
            if error:
                proof['setup_error']=error
                if error.get('prerequisite_missing'):raise ProjectPrerequisiteMissing(error['error'])
                raise RuntimeError(error['error'])
            if backend.poll()is not None or time.monotonic()>=deadline:raise TimeoutError('project parent setup did not settle')
            time.sleep(.1)
        setup=read_json(folder/'ready.json');proof.update(setup=setup)
        stage('configure_role')
        state=rpc('load_collaboration')['state'];agent=next(value for value in state['agents']if value['id']=='research')
        configured=rpc('collaboration_action',action=dict(type='agent',agent={**agent,'model':model_id,'tools':['项目环境']},request_id=uuid.uuid4().hex))
        if not configured['ok']:raise ValueError('project role configuration rejected')
        goal=prompt(scenario,variant,setup.get('environment',''));proof['input']=goal
        progress(dict(input=goal))
        before={value['id']for value in state['runs']}
        stage('submit')
        admitted=rpc('collaboration_action',action=dict(type='send',mode='task',conversation='dm-research',text=goal,
            sampling=dict(reasoning_effort='low',temperature=.1,top_p=1),request_id=uuid.uuid4().hex))
        root=next(value for value in admitted['state']['runs']if value['id']not in before);run_id=root['id']
        stage('run')
        while True:
            observed=collect()
            if backend.poll()is not None:raise RuntimeError('project evaluation parent exited; see case backend.log')
            if scenario=='cancel'and observed.get('active_guest_marker')and not proof['cancel_requested']:
                proof.update(active_guest_marker=True,cancel_requested=True)
                rpc('collaboration_action',action=dict(type='cancel',id=run_id,request_id=uuid.uuid4().hex))
            if len(calls)>spec.get('budget',16):raise RuntimeError('declared project model-call budget exceeded')
            if root.get('state')in{'succeeded','failed','cancelled','timed_out','interrupted','needs_input'}:break
            if time.monotonic()>=deadline:
                expired=True;rpc('collaboration_action',action=dict(type='cancel',id=run_id,request_id=uuid.uuid4().hex));break
            time.sleep(.15)
        goal_root=copy.deepcopy(root)
        stage('verify')
        atomic_json(folder/'verify-request.json',dict(run_id=run_id,cancel_requested=proof['cancel_requested'],active_guest_marker=proof['active_guest_marker']))
        verify_deadline=time.monotonic()+90
        while not(folder/'verification.json').exists():
            collect()
            if backend.poll()is not None or time.monotonic()>=verify_deadline:raise TimeoutError('project result/SDK-stop verification unconfirmed')
            time.sleep(.2)
        proof.update(read_json(folder/'verification.json'));collect()
        proof['model_receipts']=goal_root.get('project_receipts',[])
        stage('worker_exit')
        worker_deadline=time.monotonic()+8
        while any(worker_alive(value)for value in calls)and time.monotonic()<worker_deadline:
            time.sleep(.1);collect()
        proof['worker_exit_grace_seconds']=8
        proof['model_workers_exited']=bool(calls)and not any(worker_alive(value)for value in calls)
        goal_events=copy.deepcopy([e for task in tasks for e in task.get('events',[])])
        proof.update(deadline_exceeded=expired,verified=judge(scenario,goal_root,proof))
    except Exception as exc:
        proof.update(error_type=type(exc).__name__,failure_stage=proof['stage'])
        if isinstance(exc,OSError):proof['io_error']=dict(errno=exc.errno,winerror=getattr(exc,'winerror',None),file=Path(exc.filename).name if exc.filename else None)
        raise
    finally:
        if backend and backend.poll()is None:
            if(folder/'ready.json').exists():
                try:
                    if run_id and root.get('state')not in{'succeeded','failed','cancelled','timed_out','interrupted'}:
                        rpc('collaboration_action',action=dict(type='cancel',id=run_id,request_id=uuid.uuid4().hex))
                    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=4)
                    connection.request('GET','/shutdown',headers={'Authorization':'Bearer '+token});connection.getresponse().read();connection.close()
                except Exception as exc:proof['shutdown_request_error']=type(exc).__name__
            try:
                backend.wait(timeout=110)
            except Exception as exc:
                proof['parent_wait_error']=type(exc).__name__
                if backend.poll()is None:
                    proof['forced_parent_stop']=True
                    if tree:tree.close()
                    else:backend.kill();backend.wait(timeout=3)
        if stream:stream.close()
        if tree:tree.close()
        proof['parent_exited']=bool(backend and backend.poll()is not None)
        proof['parent_exit_code']=backend.poll()if backend else None
        proof['cleanup']=read_json(folder/'cleanup.json')
        if not proof['cleanup']or not proof['cleanup'].get('sdk_stopped')or proof.get('forced_parent_stop'):proof['verified']=False
        try:collect()
        except Exception as exc:
            proof.update(verified=False,evidence_collection_error=type(exc).__name__)
        proof['goal_state_before_cleanup']=(goal_root or{}).get('state')
        proof['cleanup_root_state']=root.get('state')
        atomic_json(folder/'result.json',proof)
        progress(dict(project_case=dict(proof)))
    result=goal_root or root
    return dict(state=result.get('state','failed'),answer=result.get('answer',''),error=result.get('error',''),events=goal_events),proof,[v['id']for v in tasks],calls
