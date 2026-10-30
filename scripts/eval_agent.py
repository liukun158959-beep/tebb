"""Isolated task-quality eval. Default fixture; --live calls the configured model."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desk_companion.agent_evals import CASES, CONTRACT_VERSION, CancellationNotSettled, aggregate_usage, call_evidence, evaluate, summarize, wait_task

# Also cleaned if setup fails before TaskManager is available.
CREDENTIAL_PATH=None


def run_with_cleanup(operation):
    try: return operation()
    finally:
        if CREDENTIAL_PATH is not None: CREDENTIAL_PATH.unlink(missing_ok=True)


class Fixture(BaseHTTPRequestHandler):
    spec=None
    def log_message(self,*args): pass
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        value=self.spec['expected']
        if self.spec.get('kind')=='route':
            value={**value,'text':'请补充目标、日期和时间。' if value['mode']=='clarify' else '幂等表示重复执行产生相同效果。'}
        delta=dict(content=json.dumps(value,ensure_ascii=False))
        if self.spec.get('executor')=='local_calendar':
            receipt=next((m for m in reversed(request['messages']) if m.get('role')=='tool'),None)
            if receipt:
                actual=json.loads(receipt['content'])
                delta=dict(content=json.dumps(dict(count=actual.get('total'),titles=[e['summary'] for e in actual.get('events',[])]),ensure_ascii=False))
            else:
                delta=dict(tool_calls=[dict(index=0,id='calendar-read',type='function',function=dict(name='read_local_calendar',arguments=json.dumps(dict(day='2040-01-02'))))])
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        frames=[dict(choices=[dict(index=0,delta=delta,finish_reason='tool_calls' if 'tool_calls' in delta else 'stop')]),dict(choices=[],usage=dict(prompt_tokens=20,completion_tokens=12))]
        for frame in frames:self.wfile.write(('data: '+json.dumps(frame)+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()


def main():
    global CREDENTIAL_PATH
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--case',action='append',default=[])
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--timeout',type=int,default=120)
    args=parser.parse_args()
    if not 1<=args.limit<=20 or not 20<=args.timeout<=300:parser.error('limit 1..20, timeout 20..300')
    if args.output.exists():parser.error('--output must be a new directory; earlier failures are retained')
    selected=[s for s in CASES if not args.case or s['id'] in args.case][:args.limit]
    if not selected or set(args.case)-{s['id'] for s in CASES}:parser.error('unknown or empty case selection')
    from desk_companion import model_catalog,model_network
    model=model_catalog.require_active() if args.live else dict(id='eval-fixture',model='eval-fixture',api_key='eval-fixture-only',base_url='')
    policy=model_network.load() if args.live else dict(mode='direct')
    server=None
    if not args.live:
        server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        model['base_url']=f'http://127.0.0.1:{server.server_port}/v1'
    output=args.output.resolve();output.mkdir(parents=True)
    profile=output/'private-profile';profile.mkdir()
    # This private profile contains a credential for the worker, never report it.
    CREDENTIAL_PATH=profile/'models.json'
    CREDENTIAL_PATH.write_text(json.dumps(dict(active=model['id'],items=[model])),'utf-8')
    (profile/'model-network.json').write_text(json.dumps(policy),'utf-8')
    previous=os.environ.get('DESK_COMPANION_DATA_DIR')
    os.environ['DESK_COMPANION_DATA_DIR']=str(profile)
    report=dict(schema_version=1,contract_version=CONTRACT_VERSION,mode='live' if args.live else 'fixture',model=model['model'],cases=[],attempts=1,
                network_mode=policy['mode'],budget=dict(max_cases=args.limit,case_timeout_seconds=args.timeout,collaboration_rounds=max(s.get('budget',2) for s in selected),transport_retries=0),
                source_commit='',semantic_scope='machine-verifiable fixed tasks; not a general capability guarantee')
    import subprocess
    report['source_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    import hashlib
    report['source_snapshot']=dict(dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
        sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'scripts/eval_agent.py','desk_companion/agent_evals.py','desk_companion/tasks.py',
            'desk_companion/task_worker.py','desk_companion/collaboration_worker.py',
            'desk_companion/collaboration_llm.py','desk_companion/workflow_worker.py','desk_companion/workflow_nodes.py',
            'desk_companion/local_calendar.py','desk_companion/tebb_tools.py')})
    from desk_companion.tasks import TaskManager
    from desk_companion.workflows import WorkflowService
    from desk_companion.agent_debug import listing, detail
    manager=TaskManager(path=profile/'memory/tasks.sqlite3')
    service=WorkflowService(lambda:manager)
    workspace=profile/'workspace.sqlite3'
    from desk_companion.collaboration import initialize_workspace
    initialize_workspace(workspace)
    def save():
        report['summary'],report['totals']=summarize(report['cases'])
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    try:
        for spec in selected:
            row=dict(case_id=spec['id'],category=spec['category'],input=spec['prompt'],expected=spec['expected'],expected_state=spec['expected_state'],prerequisite=spec['prerequisite'] or ('configured_model' if args.live else 'local_fixture'),required_tools=spec.get('required_tools',[]),outcome='incomplete',task_id='',call_ids=[],calls=[],tools=[],artifacts=[],usage=None,usage_complete=False,cost=None,elapsed_ms=0)
            row['call_budget']=spec.get('budget',2)
            report['cases'].append(row);save()
            if spec['prerequisite']:
                row.update(outcome='incomplete',reason='executor_not_implemented:'+spec['prerequisite']);save();continue
            Fixture.spec=spec
            started=time.monotonic()
            try:
                calendar=None
                if spec.get('executor')=='local_calendar':
                    from desk_companion.scheduling import SchedulingService
                    from desk_companion.work_board import WorkBoardService
                    calendar=SchedulingService(WorkBoardService(path=workspace)).local
                    for name,day,hour in [('验收事件B','2040-01-02',11),('验收事件A','2040-01-02',9),('其他日期','2040-01-03',9)]:
                        result=calendar.change(dict(type='create',request_id='eval-'+uuid.uuid4().hex,
                            summary=name,start=f'{day}T{hour:02}:00:00+08:00',end=f'{day}T{hour+1:02}:00:00+08:00',item_id=''))
                        if not result['ok']:raise ValueError('synthetic local calendar setup failed')
                    row['calendar_before_sha256']=hashlib.sha256(json.dumps(calendar.load()['events'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                if spec['category']=='workflow':
                    definition=service.operation('save',dict(name=spec['id'],graph=dict(nodes=[dict(id='trigger',kind='trigger',config={},position=dict(x=0,y=0)),dict(id='ai',kind='ai',config=dict(prompt=spec['prompt'],model_id=model['id']),position=dict(x=0,y=100))],edges=[dict(source='trigger',target='ai',branch='')])) )
                    if not definition['ok']:raise ValueError('workflow definition rejected')
                    definition=definition['definition']
                    accepted=service.operation('start',dict(id=definition['id'],version=definition['version'],request_id='eval-'+spec['id'],mode='full',input={}))
                    if not accepted['ok']:raise ValueError('workflow admission rejected')
                    run=accepted['run'];task_id=run['task_id'];row['workflow_run']=run['id']
                else:
                    task_id=manager.submit(spec['prompt'],'eval-'+spec['id'],'collaboration',workflow='collaboration',kind=spec.get('kind','chat'),role=dict(id='research',name='验收助手',role='按实际输入完成可验证任务',tools=spec.get('tools',[])),context=[],members=[],model_id=model['id'],collab_run=spec['id'],collab_path=str(workspace),budget=spec.get('budget',2),search_budget=0,fetch_budget=0,task_limits=dict(call_timeout=min(args.timeout,90),task_timeout=args.timeout),sampling=dict(reasoning_effort='low',temperature=0.1,top_p=1))
                row['task_id']=task_id;save()
                task,expired=wait_task(manager,task_id,started+args.timeout)
                row['deadline_exceeded']=expired
                if spec['category']=='workflow':
                    observed=service.operation('get',dict(run_id=run['id']))['run']
                    row['node_states']={k:v['state'] for k,v in observed['receipts'].items()}
                    task={**task,'answer':observed['receipts'].get('ai',{}).get('output',{}).get('text','')}
                outcome,evidence=evaluate(spec,task)
                row.update(outcome=outcome,evidence=evidence,answer=task.get('answer','').replace(model['api_key'],'[redacted]'),termination=task['state'])
                if calendar is not None:
                    row['calendar_after_sha256']=hashlib.sha256(json.dumps(calendar.load()['events'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                    if row['calendar_before_sha256']!=row['calendar_after_sha256']:
                        row.update(outcome='false_success' if row['outcome']=='success' else row['outcome'],reason='readonly_calendar_changed')
                calls=listing(task_id=task_id)['items']
                row['call_ids']=[v['id'] for v in calls]
                row['calls']=[call_evidence(v) for v in calls]
                usages=[detail(v['id'])['response'].get('usage') for v in calls]
                row['usage'],row['usage_complete']=aggregate_usage(usages)
                row['tools']=[dict(kind=e['kind'],**e['data']) for e in task['events'] if e['kind'] in {'tool_start','tool_end'} and isinstance(e['data'],dict)]
                if row['outcome'] in {'success','needs_input'} and not calls:
                    row.update(outcome='false_success',reason='actual_model_call_not_recorded')
            except CancellationNotSettled as exc:
                row.update(outcome='timed_out',termination=exc.task['state'],deadline_exceeded=True,cancellation_confirmed=False,error_type=type(exc).__name__)
            except Exception as exc:
                row.update(outcome='failed',error_type=type(exc).__name__)
            finally:
                row['elapsed_ms']=round((time.monotonic()-started)*1000,2);save()
    finally:
        manager.shutdown()
        if server:server.shutdown();server.server_close()
        # Credentials must not survive the acceptance run.
        (profile/'models.json').unlink(missing_ok=True)
        if previous is None:os.environ.pop('DESK_COMPANION_DATA_DIR',None)
        else:os.environ['DESK_COMPANION_DATA_DIR']=previous
    print(json.dumps(dict(mode=report['mode'],summary=report['summary'],report=str(output/'report.json')),ensure_ascii=False))
    return int(any(r['outcome'] in {'failed','false_success','timed_out','incomplete','cancelled'} for r in report['cases']))


if __name__=='__main__':raise SystemExit(run_with_cleanup(main))
