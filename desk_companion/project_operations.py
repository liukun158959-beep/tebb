"""Authenticate a physical collaboration worker before using project infrastructure."""
import copy
import hashlib

CAPABILITY='项目环境'
OPERATIONS={
    'prepare':({'repository'},{'ref'}),
    'read':({'environment','path'},set()),
    'write':({'environment','path','text'},set()),
    'execute':({'environment','command'},{'timeout'}),
    'start':({'environment','command','port'},{'expected_http_text'}),
    'status':({'environment'},set()),
    'log':({'environment','operation'},{'offset'}),
    'stop':({'environment'},set()),
    'list':(set(),set()),
    'reconcile':({'environment'},{'verify'}),
    'integrity':({'environment'},set()),
}


def authorize(host,run_id,task_id):
    from .collaboration import transaction
    service=host.collaboration
    with service.lock,transaction(service.path) as (state,_):
        run=next((value for value in state['runs'] if value['id']==run_id),None)
        if not run or run.get('kind')!='task' or run.get('state') not in {'running','queued'}:
            raise ValueError('项目操作必须来自仍在执行的实际任务。')
        if CAPABILITY not in run.get('agent_config',{}).get('tools',[]):raise ValueError('此伙伴未获项目环境权限。')
        if not task_id or run.get('task')!=task_id:raise ValueError('项目操作不属于当前执行阶段。')
        snapshot=copy.deepcopy(run)
    task=service.managers['work'].get(task_id)
    if not task or task.get('state')!='running' or task.get('channel')!='collaboration' or task.get('session')!='collab-'+run_id:
        raise ValueError('原任务已停止或工作进程身份不匹配。')
    project=snapshot.get('workspace',{}).get('project_id','')
    conversation=snapshot.get('conversation','')
    if not conversation:raise ValueError('缺少项目会话归属。')
    return dict(scope='project:'+project if project else 'conversation:'+conversation,
                owner_task=task_id,owner_run=run_id,conversation=conversation,project_id=project)


def execute(host,run_id,task_id,operation,args):
    try:
        owner=authorize(host,run_id,task_id)
        if operation not in OPERATIONS or not isinstance(args,dict):raise ValueError('项目操作参数无效。')
        arguments=copy.deepcopy(args);call_id=arguments.pop('_call_id','')
        required,optional=OPERATIONS[operation]
        if not required<=arguments.keys() or arguments.keys()-required-optional:raise ValueError('项目操作字段缺失或包含未授权字段。')
        if not isinstance(call_id,str) or not call_id or len(call_id)>200:raise ValueError('缺少实际工具调用编号。')
        request=hashlib.sha256((run_id+':'+task_id+':'+call_id+':'+operation).encode()).hexdigest()
        scope=owner['scope'];service=host.project_environments
        if operation=='prepare':return service.prepare(request=request,repo=arguments['repository'],ref=arguments.get('ref',''),**owner)
        if operation=='list':return service.list(scope)
        ident=arguments.pop('environment')
        if operation=='status':return service.status(ident,scope)
        if operation=='log':return service.log(ident,scope,arguments['operation'],arguments.get('offset',0))
        if operation=='stop':return service.stop(ident,scope)
        if operation=='reconcile':return service.reconcile(ident,scope,verify=arguments.get('verify',False),owner_task=task_id)
        common=dict(ident=ident,scope=scope,request=request,owner_task=task_id)
        if operation=='integrity':return service.integrity(**common)
        if operation=='read':return service.read(**common,path=arguments['path'])
        if operation=='write':return service.write(**common,path=arguments['path'],text=arguments['text'])
        if operation=='execute':return service.execute(**common,command=arguments['command'],timeout=arguments.get('timeout',120))
        return service.start(**common,command=arguments['command'],guest_port=arguments['port'],expected=arguments.get('expected_http_text',''))
    except Exception as exc:return dict(ok=False,error=str(exc),stage='project_operation')
