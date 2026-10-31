"""Quality experiments over the public durable collaboration lifecycle."""
import time
import uuid
import psutil


def worker_alive(call):
    try:return psutil.Process(call['pid']).create_time()<=call['created']
    except psutil.NoSuchProcess:return False


def judge(scenario, root, runs, proof):
    if scenario=='readonly':
        from .agent_eval_research import judge as judge_readonly
        return judge_readonly(root,runs,proof)
    from .agent_evals import same_json,json_answer
    if scenario=='cancel':
        return bool(root['state']=='cancelled' and proof.get('cancel_observed_active')
                    and proof.get('workers_exited_before_shutdown') and proof.get('tasks_terminal'))
    if root['state']!='succeeded':return False
    try:matched=same_json(json_answer(root['answer']),dict(total=18,checked=True))
    except (ValueError,TypeError,KeyError):return False
    if scenario=='clarify':
        return bool(matched and proof.get('paused_root')==root['id'] and proof.get('resumed_root')==root['id'])
    children=[r for r in runs if r.get('parent')==root['id']]
    if len(children)!=1:return False
    try:child_matched=same_json(json_answer(children[0].get('answer','')),dict(total=18,checked=True))
    except (ValueError,TypeError):child_matched=False
    return bool(matched and root.get('stage')=='deliver' and proof.get('accepted_delivery')
                and child_matched and children[0]['agent']=='writer' and children[0]['state']=='succeeded')


def execute(spec, *, profile, workspace, model_id, timeout, command=None, progress=lambda value:None):
    from .collaboration import Collaboration
    from .tasks import TaskManager,TERMINAL
    from .agent_debug import listing
    from .agent_evals import call_evidence
    tag=uuid.uuid4().hex
    managers={};service=None
    proof=dict(verified=False,scope='durable_product_lifecycle',timeline=[])
    root={};runs=[];tasks={};calls={};run_id='';expired=False
    started=time.monotonic();deadline=started+timeout;cancel_deadline=None
    scenario=spec['scenario']
    readonly=scenario=='readonly'
    def collect():
        for manager in managers.values():
            for item in manager.list()['items']:
                task=manager.get(item['id'])
                tasks[task['id']]=task
                for call in listing(task_id=task['id'])['items']:calls[call['id']]=call
        progress(dict(task_id=next(iter(tasks),''),task_ids=list(tasks),root_run=run_id,
                      call_ids=list(calls),calls=[call_evidence(c) for c in calls.values()],
                      tools=[dict(kind=e['kind'],**e['data']) for task in tasks.values() for e in task.get('events',[])
                             if e['kind'] in {'tool_start','tool_end'} and isinstance(e['data'],dict)]))
    try:
        for kind in ('work','chat'):
            managers[kind]=TaskManager(path=profile/'memory'/f'eval-{tag}-{kind}.sqlite3',command=command)
        service=Collaboration(path=workspace,managers=managers,start=False,
            readonly_runtime='atlas'if not readonly or spec['research_mode']=='atlas_parallel'else'legacy')
        for kind,manager in managers.items():manager.configure(2 if readonly and kind=='work'and spec['research_mode']!='serial'else 1,min(timeout,90),timeout,False)
        for ident in ('research','writer'):
            agent=next(a for a in service.snapshot()['state']['agents'] if a['id']==ident)
            service.action(dict(type='agent',agent={**agent,'model':model_id,'tools':[]},request_id=uuid.uuid4().hex))
        if scenario in{'delegate','readonly'}:
            if readonly:
                from .agent_eval_research import configure
                conversation,prompt=configure(spec,service,model_id,profile,proof)
                state=service.snapshot()['state'];progress(dict(input=prompt))
            else:
                state=service.action(dict(type='group',title='合成求和验收',goal='写作者核对数值，研究员汇总',members=['research','writer'],request_id=uuid.uuid4().hex))['state']
                conversation=state['selected']
            before={r['id'] for r in state['runs']}
            state=service.action(dict(type='send',conversation=conversation,text=spec['prompt'],mode='task',
                sampling=dict(reasoning_effort='low',temperature=.1,top_p=1),request_id=uuid.uuid4().hex))['state']
            run_id=next(r['id'] for r in state['runs'] if r['id'] not in before)
        else:
            admitted=service.submit_execution('research',spec['prompt'],'',dict(kind='eval',id=tag),uuid.uuid4().hex)
            if not admitted['ok']:raise ValueError('root admission rejected')
            run_id,conversation=admitted['run_id'],admitted['conversation']
        progress(dict(root_run=run_id,conversation=conversation))
        resumed=False;cancel_requested=False;last=None
        while True:
            service.tick();root=service.execution_status(run_id=run_id)
            runs=[r for r in service.snapshot()['state']['runs'] if r['id']==run_id or r.get('parent')==run_id]
            collect()
            transition=(root['state'],root['kind'])
            if transition!=last:
                proof['timeline'].append(dict(state=root['state'],kind=root['kind'],elapsed_ms=round((time.monotonic()-started)*1000)))
                last=transition;progress(dict(lifecycle=proof))
            if scenario=='clarify' and root['state']=='needs_input' and not resumed and not expired:
                proof['paused_root']=root['id']
                service.action(dict(type='send',conversation=conversation,run=run_id,
                    text='两个数值是7和11。请只返回JSON对象total=总和、checked=true，不添加其他键。',request_id=uuid.uuid4().hex))
                proof['resumed_root']=run_id;resumed=True
                continue  # The local root snapshot predates the accepted supplement.
            if scenario=='cancel' and not cancel_requested:
                active=next((c for c in calls.values() if c['state'] in {'sending','receiving'} and worker_alive(c)),None)
                if active:
                    proof.update(cancel_observed_active=dict(call_id=active['id'],pid=active['pid'],created=active['created'],state=active['state']),
                                 cancellation_scope='local_worker_only; provider-side inference termination is unknown')
                    service.action(dict(type='cancel',id=run_id,request_id=uuid.uuid4().hex));cancel_requested=True
            if root['state']=='needs_input':break
            if root['state'] in TERMINAL:
                proof['tasks_terminal']=all(t['state'] in TERMINAL for t in tasks.values())
                proof['workers_exited_before_shutdown']=bool(calls) and not any(worker_alive(c) for c in calls.values())
                if scenario not in{'cancel','readonly'}or proof['workers_exited_before_shutdown']and proof['tasks_terminal']:break
            now=time.monotonic()
            if now>=deadline and not expired:
                expired=True;cancel_deadline=now+8
                service.action(dict(type='cancel',id=run_id,request_id=uuid.uuid4().hex))
            if expired and now>=cancel_deadline:break
            time.sleep(.1)
        events=[e for task in tasks.values() for e in task.get('events',[])]
        proof['accepted_delivery']=any(e['kind']=='delivery_check' and isinstance(e['data'],dict) and e['data'].get('ok') for e in events)
        if readonly:
            children=[run for run in runs if run.get('parent')==run_id]
            # Public collaboration snapshots deliberately hide the private task field.
            # Correlate actual TaskManager rows by their durable, exact session owner.
            intervals=[dict(run=run['id'],task=task['id'],started=task.get('started'),ended=task.get('ended'),runtime=run.get('execution_runtime'))
                for run in children for task in tasks.values()if task.get('session')=='collab-'+run['id']]
            proof['child_intervals']=intervals
            proof['child_intervals_complete']=bool(len(intervals)==2 and len({item['run']for item in intervals})==2
                and all(type(item['started'])in(int,float)and type(item['ended'])in(int,float)and 0<item['started']<item['ended']for item in intervals))
            proof['worker_overlap']=None if not proof['child_intervals_complete']else max(item['started']for item in intervals)<min(item['ended']for item in intervals)
        proof.update(verified=not expired and judge(scenario,root,runs,proof),deadline_exceeded=expired,
                     root_state=root.get('state'),chain_state=root.get('chain',{}).get('state'),
                     runs=[{k:r.get(k) for k in ('id','parent','agent','kind','stage','state','execution_runtime','runtime_observations')} for r in runs],
                     task_states={ident:t['state'] for ident,t in tasks.items()})
        result=dict(state=root['state'],answer=root.get('answer',''),error=root.get('chain',{}).get('error') or root.get('error',''),events=events,
                    task_budget=root.get('task_budget'))
        return result,proof,list(tasks),list(calls.values())
    finally:
        if service:service.shutdown()
        else:
            for manager in managers.values():manager.shutdown()
