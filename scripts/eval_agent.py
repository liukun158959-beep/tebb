"""Isolated task-quality eval. Default fixture; --live calls the configured model."""
import argparse
from contextlib import ExitStack
import json
import os
import re
from pathlib import Path
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desk_companion.agent_evals import CASES, CONTRACT_VERSION, CancellationNotSettled, aggregate_usage, call_evidence, evaluate, refresh_call_evidence, summarize, wait_task
from desk_companion.agent_eval_projects import ProjectPrerequisiteMissing

# Also cleaned if setup fails before TaskManager is available.
CREDENTIAL_PATH=None
NETWORK_PATH=None
RESOURCES=ExitStack()


def run_with_cleanup(operation):
    keys=('DESK_COMPANION_DATA_DIR','DESK_TERMINAL_PORT','DESK_TERMINAL_TOKEN')
    previous={key:os.environ.get(key) for key in keys}
    try: return operation()
    finally:
        try:RESOURCES.close()
        finally:
            with ExitStack() as cleanup:
                for path in (CREDENTIAL_PATH,NETWORK_PATH):
                    if path is not None:cleanup.callback(path.unlink,missing_ok=True)
                for key,value in previous.items():
                    if value is None:os.environ.pop(key,None)
                    else:os.environ[key]=value


class Fixture(BaseHTTPRequestHandler):
    spec=None
    release=threading.Event()
    def log_message(self,*args): pass
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        from desk_companion.agent_eval_tools import FIXTURE_URL
        spec=self.spec;release=self.release
        value=self.spec['expected']
        if self.spec.get('kind')=='route':
            value={**value,'text':'请补充目标、日期和时间。' if value['mode']=='clarify' else '幂等表示重复执行产生相同效果。'}
        delta=dict(content=json.dumps(value,ensure_ascii=False))
        def tool(name,arguments):
            return dict(tool_calls=[dict(index=0,id=uuid.uuid4().hex,type='function',function=dict(name=name,arguments=json.dumps(arguments,ensure_ascii=False)))])
        receipts=[m for m in request['messages'] if m.get('role')=='tool']
        executor=spec.get('executor')
        available={v.get('function',{}).get('name')for v in request.get('tools',[])}
        if request['messages'][0].get('content','').startswith('反馈审阅协议'):
            material=json.loads(request['messages'][1]['content'])['materials']
            row=next((m for m in material if m['level']=='body'),material[0])
            status='supported';dimension='task_acceptance';evidence=[dict(source_id=row['id'],quote=row['segments'][0][:500])]
            if executor=='feedback':
                scenario=spec['scenario']
                status='contradicted'if scenario in{'unsupported','incomparable'}else'unverified'if scenario=='partial'else'supported'
                dimension='numeric_comparability'if scenario=='incomparable'else'claim_support'if scenario=='unsupported'else'task_acceptance'
                if scenario=='incomparable':evidence=[dict(source_id=m['id'],quote=m['segments'][0][:500])for m in material if m['level']=='body']
            delta=dict(content=json.dumps(dict(requirements=[dict(requirement='模拟交付检查',dimension=dimension,status=status,
                reason='受控模拟审阅，不构成真实语义验收',evidence=evidence)]),ensure_ascii=False))
        elif executor=='project'and 'start_background_task' in available:
            goal=next(m['content']for m in reversed(request['messages'])if m.get('role')=='user')
            delta=tool('start_background_task',dict(instructions=goal,strategy='direct'))
        elif executor=='project':
            from desk_companion.agent_eval_projects import FIX,TEST_COMMAND,HOLD_COMMAND,PYTHON_SETUP,PYTHON_START,REPOSITORIES
            scenario=spec['scenario'];text=json.dumps(request['messages'],ensure_ascii=False)
            # Tool messages do not carry names in every compatible provider.
            called=[call['function']['name']for m in request['messages']if m.get('role')=='assistant'for call in m.get('tool_calls',[])]
            def done(name):return name in called
            ident_match=re.search(r'environment_id=([a-f0-9]{32})',text)
            if scenario in{'coding','cancel'}:
                ident=ident_match[1]
                if scenario=='cancel':delta=tool('project_execute',dict(environment=ident,command=HOLD_COMMAND,timeout=120))
                elif not receipts:delta=tool('project_read',dict(environment=ident,path='eval_math.py'))
                elif len(receipts)==1:delta=tool('project_read',dict(environment=ident,path='test_eval_math.py'))
                elif not done('project_write'):delta=tool('project_write',dict(environment=ident,path='eval_math.py',text=FIX))
                elif not done('project_execute'):delta=tool('project_execute',dict(environment=ident,command=TEST_COMMAND,timeout=30))
                else:delta=dict(content='已按原测试验证合成实现。')
            else:
                variant='python'if 'pallets/flask' in text else'node';repository,ref=REPOSITORIES[variant]
                if not done('project_prepare'):delta=tool('project_prepare',dict(repository=repository,ref=ref))
                else:
                    prepared=next(json.loads(m['content'])for m in receipts if 'environment' in json.loads(m['content']))
                    ident=prepared['environment']['id']
                    if not done('project_read'):delta=tool('project_read',dict(environment=ident,path='README.md'))
                    elif variant=='python'and not done('project_execute'):delta=tool('project_execute',dict(environment=ident,command=PYTHON_SETUP,timeout=120))
                    elif not done('project_start'):delta=tool('project_start',dict(environment=ident,command=PYTHON_START if variant=='python'else'npm start',port=5000 if variant=='python'else 3000))
                    elif not done('project_status'):delta=tool('project_status',dict(environment=ident))
                    elif not done('project_log'):
                        started=next(json.loads(m['content'])for m in receipts if json.loads(m['content']).get('service_ready'))
                        delta=tool('project_log',dict(environment=ident,operation=started['service']['operation']))
                    elif not done('project_stop'):delta=tool('project_stop',dict(environment=ident))
                    else:
                        started=next(json.loads(m['content'])for m in receipts if json.loads(m['content']).get('service_ready'))
                        delta=dict(content='合成SDK服务已核对并停止。实际回执中的[仓库]('+repository+'/tree/'+ref+')；'
                                   '[验证预览（已停止）]('+started['service']['url']+')。')
        elif executor=='lifecycle':
            names={t['function']['name'] for t in request.get('tools',[])}
            if 'request_clarification' in names:
                if spec['scenario']=='clarify' and '用户补充目标' not in json.dumps(request['messages'],ensure_ascii=False):
                    delta=tool('request_clarification',dict(question='请补充两个数值。'))
                else:delta=tool('start_background_task',dict(instructions='将7与11相加，只返回JSON total和checked=true。',strategy='delegate' if spec['scenario']=='delegate' else 'direct'))
            elif 'submit_plan' in names:
                delta=tool('submit_plan',dict(tasks=[dict(agent_id='writer',title='核对求和',instructions='将7与11相加，只返回JSON total和checked=true。',depends_on=None,stage='collect',required_sources=[])]))
            elif 'submit_deliverable' in names:
                delta=tool('submit_deliverable',dict(report=json.dumps(value),complete=True,missing=[],limitations=[]))
        elif executor=='public_source':
            if not receipts:delta=tool('web_fetch',dict(url=FIXTURE_URL))
            else:
                actual=json.loads(receipts[-1]['content']);text=actual['text']
                delta=dict(content=json.dumps(dict(tlds=re.findall(r'\.[a-z]+',text.split('2. 保留TLD\n')[1].split('3.')[0]),
                    example_domains=re.findall(r'example\.(?:com|net|org)',text.split('3. 示例二级域名\n')[1]),citation='[合成来源]('+actual['url']+')')))
        elif executor=='chart':
            if not receipts:
                source_id=re.search(r'source_id=([a-f0-9]{32})','\n'.join(str(m.get('content','')) for m in request['messages']))[1]
                delta=tool('read_local_source',dict(source_id=source_id))
            elif len(receipts)==1:
                actual=json.loads(receipts[0]['content'])
                nodes=actual['text'].split('：',1)[1].split('。',1)[0].split('→')
                delta=tool('build_chart',dict(type='flow',title='交付流程',source_ids=[],nodes=nodes,edges=[[0,1],[1,2]],flow_style='steps'))
            else:delta=dict(content=json.loads(receipts[-1]['content'])['markdown']+'\n关系示意。')
        if self.spec.get('executor')=='local_calendar':
            receipt=next((m for m in reversed(request['messages']) if m.get('role')=='tool'),None)
            if receipt:
                actual=json.loads(receipt['content'])
                delta=dict(content=json.dumps(dict(count=actual.get('total'),titles=[e['summary'] for e in actual.get('events',[])]),ensure_ascii=False))
            else:
                delta=dict(tool_calls=[dict(index=0,id='calendar-read',type='function',function=dict(name='read_local_calendar',arguments=json.dumps(dict(day='2040-01-02'))))])
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        if executor=='lifecycle' and spec['scenario']=='cancel':
            try:
                while not release.wait(.1):
                    self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":" "}}]}\n\n');self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
            return
        frames=[dict(choices=[dict(index=0,delta=delta,finish_reason='tool_calls' if 'tool_calls' in delta else 'stop')]),dict(choices=[],usage=dict(prompt_tokens=20,completion_tokens=12))]
        for frame in frames:self.wfile.write(('data: '+json.dumps(frame)+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()


def main():
    global CREDENTIAL_PATH,NETWORK_PATH
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--case',action='append',default=[])
    parser.add_argument('--limit',type=int,default=len(CASES))
    parser.add_argument('--samples',type=int,default=1)
    parser.add_argument('--project-sdk',choices=['disabled','official'],default='disabled')
    parser.add_argument('--project-timeout',type=int,default=600)
    parser.add_argument('--timeout',type=int,default=120)
    parser.add_argument('--output-limit-bindings',type=Path,help='Explicit verified generation-binding SQLite snapshot for this isolated live cohort')
    args=parser.parse_args()
    if not 1<=args.limit<=len(CASES) or not 20<=args.timeout<=300 or not 1<=args.samples<=5:parser.error(f'limit 1..{len(CASES)}, timeout 20..300, samples 1..5')
    if args.output.exists():parser.error('--output must be a new directory; earlier failures are retained')
    if args.output_limit_bindings and(not args.live or not args.output_limit_bindings.is_file()):parser.error('output-limit-bindings requires --live and an existing verified SQLite snapshot')
    selected=[s for s in CASES if not args.case or s['id'] in args.case][:args.limit]
    if not selected or set(args.case)-{s['id'] for s in CASES}:parser.error('unknown or empty case selection')
    projects=any(s.get('executor')=='project'for s in selected)
    if args.project_sdk=='official'and not args.live:parser.error('official SDK requires --live')
    if args.live and projects and args.project_sdk!='official':parser.error('live project cases require explicit --project-sdk official')
    if not 20<=args.project_timeout<=1200:parser.error('project-timeout 20..1200')
    from desk_companion import model_catalog,model_network,model_capacity
    model=model_catalog.require_active() if args.live else dict(id='eval-fixture',model='eval-fixture',api_key='eval-fixture-only',base_url='')
    policy=model_network.load() if args.live else dict(mode='direct')
    capacity=model_capacity.load(model['id'])if args.live else{}
    server=None
    if not args.live:
        server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        server.daemon_threads=True
        RESOURCES.callback(server.server_close)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        RESOURCES.callback(server.shutdown)
        model['base_url']=f'http://127.0.0.1:{server.server_port}/v1'
    output=args.output.resolve();output.mkdir(parents=True)
    profile=output/'private-profile';profile.mkdir()
    # This private profile contains a credential for the worker, never report it.
    CREDENTIAL_PATH=profile/'models.json'
    CREDENTIAL_PATH.write_text(json.dumps(dict(active=model['id'],items=[model])),'utf-8')
    NETWORK_PATH=profile/'model-network.json'
    NETWORK_PATH.write_text(json.dumps(policy),'utf-8')
    (profile/'model-capacity.json').write_text(json.dumps({model['id']:capacity}if capacity else{}),'utf-8')
    if args.output_limit_bindings:
        import sqlite3
        from contextlib import closing
        destination=profile/'memory/model_generation.sqlite3';destination.parent.mkdir()
        with closing(sqlite3.connect(args.output_limit_bindings.resolve().as_uri()+'?mode=ro',uri=True))as source,closing(sqlite3.connect(destination))as target:
            source.backup(target)
    previous=os.environ.get('DESK_COMPANION_DATA_DIR')
    os.environ['DESK_COMPANION_DATA_DIR']=str(profile)
    for key in ('DESK_TERMINAL_PORT','DESK_TERMINAL_TOKEN'):os.environ.pop(key,None)
    report=dict(schema_version=1,contract_version=CONTRACT_VERSION,mode='live' if args.live else 'fixture',model=model['model'],cases=[],attempts=1,
                samples_per_case=args.samples,sampling_plan='independent; no retry or success selection',
                network_mode=policy['mode'],budget=dict(max_cases=args.limit,max_samples=args.limit*args.samples,case_timeout_seconds=args.timeout,
                    project_timeout_seconds=args.project_timeout if projects else None,project_sdk=('official'if args.live else'fixture')if projects else'disabled',
                    collaboration_rounds=max(s.get('phase_budget',s.get('budget',2)) for s in selected),max_case_model_calls=max(s.get('budget',2) for s in selected),transport_retries=0),
                source_commit='',semantic_scope='machine-verifiable fixed tasks; not a general capability guarantee')
    import subprocess
    report['source_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    report['root_budget_policy_version']=1
    report['verified_output_binding_snapshot']=bool(args.output_limit_bindings)
    report['capacity_snapshot']=dict(window=capacity.get('window'),output_reserve=capacity.get('output_reserve'),
        tokenizer_bound=bool(capacity.get('tokenizer_dir')),tokenizer_signature=capacity.get('tokenizer_signature'))
    import hashlib
    source_paths=(
            'scripts/eval_agent.py','desk_companion/agent_evals.py','desk_companion/tasks.py',
            'desk_companion/task_worker.py','desk_companion/collaboration_worker.py',
            'desk_companion/collaboration_llm.py','desk_companion/workflow_worker.py','desk_companion/workflow_nodes.py',
            'desk_companion/local_calendar.py','desk_companion/tebb_tools.py','desk_companion/collaboration.py',
            'desk_companion/agent_eval_tools.py','desk_companion/agent_eval_lifecycle.py','scripts/eval_fixture_worker.py',
            'desk_companion/agent_eval_projects.py','scripts/eval_project_host.py','scripts/eval_project_fixture.py',
            'desk_companion/project_environments.py','desk_companion/project_environment_store.py','desk_companion/project_operations.py',
            'desk_companion/project_tools.py','desk_companion/project_receipts.py','desk_companion/docker_sandboxes.py',
            'desk_companion/worker_rpc.py','desk_companion/bridge.py','desk_companion/local_api/host.py','desk_companion/local_api/server.py',
            'desk_companion/execution_context.py','desk_companion/model_capacity.py','desk_companion/model_tokenizer.py',
            'desk_companion/task_budgets.py','desk_companion/model_generation.py',
            'desk_companion/context_pack.py','desk_companion/assistant.py','desk_companion/model_catalog.py',
            'desk_companion/json_document.py','desk_companion/feedback.py','desk_companion/feedback_review.py',
            'desk_companion/delivery_feedback.py','desk_companion/web_sources.py','desk_companion/project_workspace.py')
    hashes={}
    for name in source_paths:
        raw=(ROOT/name).read_bytes()
        captured=output/'source_snapshot'/name;captured.parent.mkdir(parents=True,exist_ok=True);captured.write_bytes(raw)
        hashes[name]=hashlib.sha256(raw).hexdigest()
    report['source_snapshot']=dict(dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
                                   directory='source_snapshot',capture='before_execution',sha256=hashes)
    from desk_companion.tasks import TaskManager
    from desk_companion.workflows import WorkflowService
    from desk_companion.agent_debug import listing, detail
    command=None if args.live else [sys.executable,'-u',str(ROOT/'scripts/eval_fixture_worker.py')]
    manager=TaskManager(path=profile/'memory/tasks.sqlite3',command=command)
    RESOURCES.callback(manager.shutdown)
    service=WorkflowService(lambda:manager)
    from desk_companion.collaboration import initialize_workspace
    from desk_companion.agent_eval_tools import prepare,verify
    def save():
        report['summary'],report['totals']=summarize(report['cases'])
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    try:
        for spec,sample in [(spec,sample) for spec in selected for sample in range(1,args.samples+1)]:
            row=dict(case_id=spec['id'],category=spec['category'],input=spec['prompt'],expected=spec['expected'],expected_state=spec['expected_state'],prerequisite=spec['prerequisite'] or ('configured_model' if args.live else 'local_fixture'),required_tools=spec.get('required_tools',[]),outcome='incomplete',task_id='',call_ids=[],calls=[],tools=[],artifacts=[],usage=None,usage_complete=False,cost=None,elapsed_ms=0)
            row['call_budget']=spec.get('budget',2)
            row['sample']=sample
            report['cases'].append(row);save()
            if spec['prerequisite']:
                row.update(outcome='incomplete',reason='executor_not_implemented:'+spec['prerequisite']);save();continue
            Fixture.spec=spec
            Fixture.release=threading.Event()
            started=time.monotonic()
            row['stage']='prepare'
            try:
                run_id=uuid.uuid4().hex
                workspace=profile/'workspaces'/(run_id+'.sqlite3')
                project=spec.get('executor')=='project'
                if not project:initialize_workspace(workspace)
                context=prepare(spec,workspace,run_id,fixture=not args.live)
                row.update(input=context['prompt'],selection=context['selection'],collab_run=run_id)
                lifecycle=spec.get('executor')=='lifecycle'
                verification=None;calls=None
                if project:
                    from desk_companion.agent_eval_projects import execute
                    def progress(value):
                        row.update(value)
                        if value.get('root_run'):row['collab_run']=value['root_run']
                        if value.get('project_case',{}).get('stage'):row['stage']=value['project_case']['stage']
                        save()
                    task,verification,task_ids,calls=execute(spec,profile=profile,model_id=model['id'],timeout=args.project_timeout,sample=sample,official=args.live,progress=progress)
                    row['task_ids']=task_ids;task_id=task_ids[0]if task_ids else''
                    expired=verification.get('deadline_exceeded',False)
                    row['project_case']=verification
                elif lifecycle:
                    from desk_companion.agent_eval_lifecycle import execute
                    def progress(value):row.update(value);save()
                    task,verification,task_ids,calls=execute(spec,profile=profile,workspace=workspace,model_id=model['id'],timeout=args.timeout,command=command,progress=progress)
                    row['task_ids']=task_ids;task_id=task_ids[0] if task_ids else ''
                    expired=verification['deadline_exceeded']
                elif spec['category']=='workflow':
                    row['stage']='workflow_save'
                    definition=service.operation('save',dict(name=spec['id']+'-s'+str(sample),graph=dict(nodes=[dict(id='trigger',kind='trigger',config={},position=dict(x=0,y=0)),dict(id='ai',kind='ai',config=dict(prompt=spec['prompt'],model_id=model['id']),position=dict(x=0,y=100))],edges=[dict(source='trigger',target='ai',branch='')])) )
                    if not definition['ok']:
                        row['error']=definition.get('error','workflow definition rejected');raise ValueError('workflow definition rejected')
                    definition=definition['definition']
                    row['stage']='workflow_start'
                    accepted=service.operation('start',dict(id=definition['id'],version=definition['version'],request_id=run_id,mode='full',input={}))
                    if not accepted['ok']:
                        row['error']=accepted.get('error','workflow admission rejected');raise ValueError('workflow admission rejected')
                    run=accepted['run'];task_id=run['task_id'];row['workflow_run']=run['id']
                else:
                    task_id=manager.submit(context['prompt'],'eval-'+run_id,'collaboration',workflow='collaboration',kind=spec.get('kind','chat'),role=dict(id='research',name='验收助手',role='按实际输入完成可验证任务',tools=spec.get('tools',[])),selection=context['selection'],context=context.get('history',[]),members=[],model_id=model['id'],collab_run=run_id,collab_path=str(workspace),budget=spec.get('budget',2),search_budget=0,fetch_budget=spec.get('fetch_budget',0),task_limits=dict(call_timeout=min(args.timeout,90),task_timeout=args.timeout),sampling=dict(reasoning_effort='low',temperature=0.1,top_p=1),**context.get('request_extra',{}))
                row['task_id']=task_id;save()
                if not lifecycle and not project:row['task_ids']=[task_id]
                row['stage']='wait'
                if not lifecycle and not project:task,expired=wait_task(manager,task_id,started+args.timeout)
                row['deadline_exceeded']=expired
                if spec['category']=='workflow':
                    observed=service.operation('get',dict(run_id=run['id']))['run']
                    row['node_states']={k:v['state'] for k,v in observed['receipts'].items()}
                    task={**task,'answer':observed['receipts'].get('ai',{}).get('output',{}).get('text','')}
                if not lifecycle and not project:verification=verify(spec,task,context)
                row['stage']='verify'
                outcome,evidence=evaluate(spec,task,verification=verification)
                if expired:outcome='timed_out'
                row.update(outcome=outcome,evidence=evidence,answer=task.get('answer','').replace(model['api_key'],'[redacted]'),termination=task['state'],error=task.get('error','').replace(model['api_key'],'[redacted]'))
                row['task_budget']=task.get('task_budget')or(verification or{}).get('task_budget')
                if verification:
                    row['artifacts']=verification.get('artifacts',[])
                    if spec.get('executor')=='local_calendar':
                        row.update(calendar_before_sha256=verification.get('before_sha256'),calendar_after_sha256=verification.get('after_sha256'))
                if calls is None:calls=listing(task_id=task_id)['items']
                row['call_ids']=[v['id'] for v in calls]
                row['calls']=[call_evidence(v) for v in calls]
                usages=[detail(v['id'])['response'].get('usage') for v in calls]
                row['usage'],row['usage_complete']=aggregate_usage(usages)
                row['tools']=[dict(kind=e['kind'],**e['data']) for e in task['events'] if e['kind'] in {'tool_start','tool_end'} and isinstance(e['data'],dict)]
                if row['outcome'] in {'success','needs_input'} and not calls:
                    row.update(outcome='false_success',reason='actual_model_call_not_recorded')
                row['stage']='finished'
            except ProjectPrerequisiteMissing as exc:
                row.update(outcome='skip',reason='official_sdk_prerequisite_missing',error=str(exc))
            except CancellationNotSettled as exc:
                row.update(outcome='timed_out',termination=exc.task['state'],deadline_exceeded=True,cancellation_confirmed=False,error_type=type(exc).__name__)
            except Exception as exc:
                row.update(outcome='failed',error_type=type(exc).__name__)
            finally:
                Fixture.release.set()
                complete=refresh_call_evidence(row,listing,detail)
                if not complete and row['outcome'] in {'success','needs_input'}:
                    row.update(outcome='failed',error_type='EvidenceCollectionFailed')
                row['elapsed_ms']=round((time.monotonic()-started)*1000,2);save()
    finally:
        RESOURCES.close()
        # Credentials must not survive the acceptance run.
        (profile/'models.json').unlink(missing_ok=True)
        NETWORK_PATH.unlink(missing_ok=True)
        from desk_companion.agent_eval_lifecycle import worker_alive
        for row in report['cases']:
            if not refresh_call_evidence(row,listing,detail) and row['outcome'] in {'success','needs_input'}:
                row.update(outcome='failed',error_type='EvidenceCollectionFailed')
            from desk_companion.task_budgets import task_view
            ids=row.get('task_ids')or([row['task_id']]if row.get('task_id')else[])
            if ids:row['task_budget']=task_view(ids[0])
        complete=all(row.get('call_evidence_complete',False) for row in report['cases'] if row.get('task_id'))
        observed=[call for row in report['cases'] for call in row.get('calls',[]) if call.get('pid') and call.get('created')]
        alive=[call['id'] for call in observed if worker_alive(call)]
        report['cleanup']=dict(model_credential_removed=not CREDENTIAL_PATH.exists(),network_policy_removed=not NETWORK_PATH.exists(),
                               observed_workers_exited=not alive if complete else None,call_evidence_complete=complete,still_alive_call_ids=alive)
        save()
        if previous is None:os.environ.pop('DESK_COMPANION_DATA_DIR',None)
        else:os.environ['DESK_COMPANION_DATA_DIR']=previous
    print(json.dumps(dict(mode=report['mode'],summary=report['summary'],report=str(output/'report.json')),ensure_ascii=False))
    return int(bool(alive) or not complete or any(r['outcome'] in {'failed','false_success','timed_out','incomplete','cancelled'} for r in report['cases']))


if __name__=='__main__':raise SystemExit(run_with_cleanup(main))
