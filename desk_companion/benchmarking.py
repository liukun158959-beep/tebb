"""Evidence and lifecycle for the existing benchmark CLI; no alternate agent."""
import concurrent.futures
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing,ExitStack
from dataclasses import dataclass

from .performance import summarize,task_timing


@dataclass(frozen=True)
class BenchmarkPlan:
    output:Path
    samples:int=100
    parallel:int=2
    live:bool=False
    prewarm:bool=False
    think_time:float=0
    python:Path|None=None
    generation_bindings:Path|None=None

    def __post_init__(self):
        if type(self.samples)is not int or not 1<=self.samples<=1000:raise ValueError('samples 1..1000')
        if type(self.parallel)is not int or not 1<=self.parallel<=4:raise ValueError('parallel 1..4')
        if not 0<=self.think_time<=30:raise ValueError('think-time 0..30')


def atomic_json(path,value):
    temporary=path.with_name(path.name+'.pending')
    with temporary.open('w',encoding='utf-8',newline='')as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def capture_runtime(destination):
    source=Path(__file__).resolve().parents[1];destination.mkdir()
    packages=[source/'desk_companion']
    spec=importlib.util.find_spec('atlas')
    if spec and spec.origin:packages.append(Path(spec.origin).parent)
    hashes={};originals={}
    for package in packages:
        for path in sorted(package.rglob('*')):
            if not path.is_file()or any(part in{'__pycache__','.git','node_modules'}for part in path.parts):continue
            if path.suffix not in{'.py','.md','.txt','.json','.toml','.yaml','.yml','.jinja','.html','.css','.js'}:continue
            relative=Path(package.name)/path.relative_to(package);body=path.read_bytes();target=destination/relative
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
            hashes[relative.as_posix()]=hashlib.sha256(body).hexdigest();originals[relative.as_posix()]=path
    for relative in('scripts/benchmark_agent.py','requirements.txt'):
        path=source/relative
        if path.is_file():
            body=path.read_bytes();target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
            hashes[relative]=hashlib.sha256(body).hexdigest();originals[relative]=path
    def git(*args):
        result=subprocess.run(['git',*args],cwd=source,capture_output=True,timeout=10)
        return result.stdout.decode('utf-8').strip()if result.returncode==0 else None
    versions={}
    for package in('openai','httpx','jsonschema','psutil','tiktoken'):
        try:versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:versions[package]=None
    return dict(directory=str(destination),sha256=hashes,source_commit=git('rev-parse','HEAD'),
            dirty=bool(git('status','--porcelain','--untracked-files=normal')),parent_python=sys.version,parent_dependencies=versions),originals


class Recorder:
    def __init__(self,plan,model,network,evidence):
        self.plan=plan;self.lock=threading.RLock();self.started=time.monotonic()
        self.report=dict(schema_version=2,run_state='preparing',source=('live_model'if plan.live else'local_sse_fixture')+('_prewarmed_workers'if plan.prewarm else'_cold_workers'),
            model=model['model'],model_id=model['id'],network_mode=network['mode'],parallel=plan.parallel,prewarm=plan.prewarm,
            think_time=plan.think_time,warmup_sec=None,attempts_per_sample=1,maximum_model_requests=plan.samples,
            call_idle_seconds=40,task_hard_seconds=70,evidence_directory=str(evidence),cost=None,
            samples=[dict(index=i,state='not_started',task_id=None,call_ids=[],usage=None,usage_complete=False)for i in range(plan.samples)],
            cleanup=dict(complete=False),manager_initialization='not_started',scope='One model request per task; queue included, UI excluded. Frozen runtime and local evidence retained; no retry or automatic replay. Idle 40s renews on progress; task execution hard limit70s.')

    def save(self):
        with self.lock:
            report=self.report;stats=summarize(report['samples'])
            limits=dict(duration_ms=30000 if self.plan.live else 5000,first_text_ms=15000 if self.plan.live else 4500,worker_prepare_ms=3500)
            checks={key:stats[key]['p99']is not None and stats[key]['p99']<=limit for key,limit in limits.items()}
            checks.update(no_failures=stats['failed']==0 and stats['completed']==self.plan.samples,
                cleanup=bool(report['cleanup'].get('complete')),frozen_source=report.get('source_unchanged')is True)
            report.update(summary=stats,limits_ms=limits,checks=checks,passed=report['run_state']=='completed'and all(checks.values()),
                prewarmed_samples=sum(bool(row.get('prewarmed'))for row in report['samples']),wall_sec=round(time.monotonic()-self.started,2))
            known=[row['usage']for row in report['samples']if isinstance(row.get('usage'),dict)]
            report['usage_summary']=dict(known_input_tokens=sum(row.get('input_tokens',0)for row in known),known_output_tokens=sum(row.get('output_tokens',0)for row in known),
                input_unknown_calls=sum(row.get('input_unknown_calls',0)for row in known),output_unknown_calls=sum(row.get('output_unknown_calls',0)for row in known),
                started_samples_without_complete_usage=sum(bool(row.get('task_id'))and not row.get('usage_complete',False)for row in report['samples']),cost=None)
            atomic_json(self.plan.output,report)

    def update(self,index,**values):
        with self.lock:self.report['samples'][index].update(values);self.save()


def observed_alive(call):
    import psutil
    if type(call.get('pid'))is not int or not isinstance(call.get('created'),(int,float)):return None
    try:return psutil.Process(call['pid']).create_time()<=call['created']
    except psutil.NoSuchProcess:return False
    except psutil.AccessDenied:return None


def run_benchmark(plan,model,network,*,capacity=None,manager_factory=None):
    """Always retain execution evidence; invalid/reused output is refused before sending."""
    output=Path(plan.output).resolve();evidence=output.with_name(output.stem+'-evidence')
    if output.exists()or evidence.exists():raise FileExistsError('Use a new output; earlier evidence is retained')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8')as stream:stream.write('{}')
    recorder=Recorder(plan,model,network,evidence);recorder.save();profile=evidence/'profile'
    sensitive=[profile/'models.json',profile/'model-network.json']
    previous={key:os.environ.get(key)for key in('DESK_COMPANION_DATA_DIR','DESK_TERMINAL_PORT','DESK_TERMINAL_TOKEN')}
    manager=None;pool=None;futures={};calls={};originals={};snapshot=None;stop=threading.Event();shutdown=dict(complete=True,pending_task_ids=[])
    report=recorder.report
    def remove_sensitive():
        for path in sensitive:
            try:
                assert path.resolve().parent==profile.resolve();path.unlink(missing_ok=True)
            except OSError:pass
    def restore_environment():
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
    def call_rows(ident):
        from .agent_debug import listing
        return listing(task_id=ident)['items']
    def record_task(index,task):
        from .agent_debug import load_timing
        chain=task_timing(task);events=task.get('events',[]);rows=call_rows(task['id'])
        with recorder.lock:
            for row in rows:calls[row['id']]=row
        budget=task.get('task_budget');used=budget.get('consumed',{})if isinstance(budget,dict)else{}
        started=task.get('created');ended=task.get('ended');first=next((e['ts']for e in events if e['kind']=='token'and e.get('data')),None)
        measured=isinstance(started,(int,float))and isinstance(ended,(int,float))and ended>started
        prepare=[s['duration_ms']for s in chain['stages']if s['kind']=='prepare']
        attempts=sum(e['kind']=='llm_start'for e in events)
        result=dict(task_id=task['id'],state=task['state'],duration_ms=round((ended-started)*1000,2)if measured else None,
            first_text_ms=round((first-started)*1000,2)if first and isinstance(started,(int,float))else None,
            worker_prepare_ms=sum(prepare)if prepare else None,model_calls=attempts,
            prewarmed=any(e['kind']=='worker_pool'and e['data'].get('prewarmed')for e in events),
            error='Timeout'if task['state']=='timed_out'else next((e['data'].get('type','')for e in events if e['kind']=='failure'and isinstance(e['data'],dict)),''),
            longest=chain['longest']['label']if chain['longest']else None,call_ids=[row['id']for row in rows],
            http=[load_timing(row['id'])for row in rows],task_budget=budget,usage=used or None,
            usage_complete=bool(budget and used.get('model_calls')==len(rows)and used.get('input_unknown_calls')==0 and used.get('output_unknown_calls')==0))
        if task['state']=='succeeded'and(attempts!=1 or not task.get('answer','').strip()):result.update(state='failed',error='InvalidModelReceipt')
        atomic_json(evidence/'samples'/f'{index:04d}.json',dict(task=task,calls=rows,chain=chain))
        return result
    def sample(index):
        with recorder.lock:
            if stop.is_set():return dict(state='not_started')
            ident=manager.submit('hello，请只用一句短话回应。','bench-'+str(index),'collaboration',submission_key='benchmark:'+report['cohort_id']+':'+str(index),
                workflow='collaboration',kind='chat',role=dict(id='tebb',name='tebb',role='直接自然地回复用户',tools=[]),context=[],members=[],model_id=model['id'],
                budget=1,collab_run='bench-'+str(index),collab_path=str(profile/'workspace.sqlite3'),task_limits=dict(call_timeout=40,task_timeout=70))
            recorder.update(index,task_id=ident,state='queued')
        result=record_task(index,manager.wait(ident))
        if plan.think_time:stop.wait(plan.think_time)
        return result
    def settle(future,index):
        try:result=future.result()
        except BaseException as exc:
            result=dict(state='recording_failed',error=type(exc).__name__)
            if isinstance(exc,KeyboardInterrupt):
                stop.set();report['run_state']='interrupted';result['state']='interrupted'
            ident=report['samples'][index].get('task_id')
            if ident:
                try:
                    observed=record_task(index,manager.get(ident));result={**observed,**result,'actual_task_state':observed['state']}
                except BaseException:pass
            path=evidence/'samples'/f'{index:04d}.json'
            if not path.exists():atomic_json(path,dict(index=index,state='recording_failed',error=type(exc).__name__))
        recorder.update(index,**result)
    with ExitStack()as guard:
        guard.callback(restore_environment);guard.callback(remove_sensitive)
        try:
            evidence.mkdir();profile.mkdir();(evidence/'samples').mkdir()
            snapshot,originals=capture_runtime(evidence/'source_snapshot');report['source_snapshot']=snapshot;report['source_commit']=snapshot['source_commit'];report['cohort_id']=uuid.uuid4().hex
            sensitive[0].write_text(json.dumps(dict(active=model['id'],items=[model])),encoding='utf-8')
            sensitive[1].write_text(json.dumps(network),encoding='utf-8')
            (profile/'model-capacity.json').write_text(json.dumps({model['id']:capacity}if capacity else{}),encoding='utf-8')
            report['capacity_snapshot']=capacity or{}
            report['configuration_fingerprint']=hashlib.sha256(json.dumps(dict(model=model,network=network,capacity=capacity),sort_keys=True).encode()).hexdigest()
            report['verified_output_binding_snapshot']=bool(plan.generation_bindings)
            if plan.generation_bindings:
                destination=profile/'memory/model_generation.sqlite3';destination.parent.mkdir()
                with closing(sqlite3.connect(Path(plan.generation_bindings).resolve().as_uri()+'?mode=ro',uri=True))as source,closing(sqlite3.connect(destination))as target:source.backup(target)
                frozen_binding=evidence/'generation-binding-snapshot.sqlite3';frozen_binding.write_bytes(destination.read_bytes())
                report['generation_binding_sha256']=hashlib.sha256(frozen_binding.read_bytes()).hexdigest()
            os.environ['DESK_COMPANION_DATA_DIR']=str(profile)
            for key in('DESK_TERMINAL_PORT','DESK_TERMINAL_TOKEN'):os.environ.pop(key,None)
            with closing(sqlite3.connect(profile/'workspace.sqlite3'))as db:
                with db:db.execute('CREATE TABLE workspace(id INTEGER PRIMARY KEY,data TEXT)');db.execute('INSERT INTO workspace VALUES(1,?)',(json.dumps(dict(runs=[])),))
            if manager_factory is None:
                from .tasks import TaskManager
                manager_factory=TaskManager
            bootstrap=f"import sys;sys.path.insert(0,{snapshot['directory']!r});from desk_companion.task_worker import main;main()"
            interpreter=str(Path(plan.python).resolve())if plan.python else sys.executable
            probe="import sys,json,importlib.metadata as m;print(json.dumps(dict(python=sys.version,executable=sys.executable,dependencies={p:m.version(p) for p in ('openai','httpx','jsonschema','psutil')})))"
            runtime=subprocess.run([interpreter,'-c',probe],capture_output=True,timeout=15,check=True)
            report['worker_runtime']=json.loads(runtime.stdout);command=[interpreter,'-u','-c',bootstrap]
            warm_start=time.monotonic();report['manager_initialization']='starting'
            manager=manager_factory(path=profile/'memory/tasks.sqlite3',prewarm=plan.parallel if plan.prewarm else 0,command=command)
            report['manager_initialization']='ready'
            manager.configure(plan.parallel,40,70,False)
            if plan.prewarm and not manager.standby.wait_ready(30):raise RuntimeError('WarmupNotReady')
            report.update(warmup_sec=round(time.monotonic()-warm_start,2),run_state='running');recorder.save()
            pool=concurrent.futures.ThreadPoolExecutor(plan.parallel)
            pending={}
            def enqueue(index):
                future=pool.submit(sample,index);futures[future]=index;pending[future]=index
            next_index=min(plan.samples,plan.parallel)
            for index in range(next_index):enqueue(index)
            while pending:
                done,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    index=pending.pop(future);settle(future,index)
                    if next_index<plan.samples and not stop.is_set():enqueue(next_index);next_index+=1
                print(json.dumps(dict(completed=report['summary']['completed'],samples=plan.samples,failed=report['summary']['failed'])),flush=True)
                if stop.is_set():break
            if not stop.is_set():report['run_state']='completed'
        except BaseException as exc:
            with recorder.lock:report.update(run_state='interrupted'if isinstance(exc,KeyboardInterrupt)else'failed',run_error=type(exc).__name__)
        finally:
            with recorder.lock:stop.set()
            if manager:
                try:shutdown=manager.shutdown()
                except Exception as exc:shutdown=dict(complete=False,pending_task_ids=[],error=type(exc).__name__)
            if pool:
                pool.shutdown(wait=True,cancel_futures=True)
                for future,index in futures.items():
                    if report['samples'][index]['state']in{'queued','not_started'}and future.done():settle(future,index)
            for row in report['samples']:
                if manager and row.get('task_id'):
                    try:
                        for call in call_rows(row['task_id']):calls[call['id']]=call
                    except Exception:report['call_evidence_incomplete']=True
            guard.close()
            mismatches=[]
            if snapshot:
                for relative,digest in snapshot['sha256'].items():
                    for name,path in(('frozen',Path(snapshot['directory'])/relative),('original',originals[relative])):
                        if not path.is_file()or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:mismatches.append(dict(kind=name,file=relative))
            report.update(source_unchanged=bool(snapshot)and not mismatches,source_mismatches=mismatches)
            alive=[ident for ident,call in calls.items()if observed_alive(call)is not False]
            evidence_complete=not report.get('call_evidence_incomplete')and all(type(row.get('model_calls'))is int and row['model_calls']==len(row.get('call_ids',[]))for row in report['samples']if row.get('task_id'))
            observed=not alive and evidence_complete and report['manager_initialization']!='starting'
            cleanup=dict(shutdown=shutdown,call_evidence_complete=evidence_complete,observed_workers_exited=observed,unsettled_call_ids=alive,model_credential_removed=not sensitive[0].exists(),network_policy_removed=not sensitive[1].exists())
            cleanup['complete']=bool(shutdown.get('complete')and observed and cleanup['model_credential_removed']and cleanup['network_policy_removed'])
            report['cleanup']=cleanup;recorder.save()
    return report
