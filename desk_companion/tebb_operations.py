"""Parent-owned capability dispatch for an actual active tebb run."""
import hashlib
import copy
from .collaboration import ACTIVE, transaction

def execute(host,run_id,tool,args):
    try:
        with host.collaboration.lock,transaction(host.collaboration.path) as (state,_):
            run=next((r for r in state['runs'] if r['id']==run_id),None)
            if not run or run['agent']!='tebb' or run['state'] not in ACTIVE:
                raise ValueError('tebb执行已结束或身份不匹配。')
            project_id=run.get('workspace',{}).get('project_id','')
        args=copy.deepcopy(args)
        call_id=str(args.pop('_call_id',''))
        if tool=='calendar_query':
            result=host.scheduling.load_month(args['day'],True,args.get('request_id','')) if args.get('month') else host.scheduling.load(args['day'],refresh=True,request_id=args.get('request_id',''))
            if args.get('query') and result.get('events'):
                result['events']=[e for e in result['events'] if args['query'].casefold() in e['summary'].casefold()]
            return result
        if tool in {'calendar_change','automation_save','automation_manage'} and not call_id:raise ValueError('缺少本轮工具调用编号。')
        if tool=='calendar_change':
            action=args['action'];action['request_id']='tebb-'+hashlib.sha256((run_id+':'+call_id).encode()).hexdigest()[:50]
            kind=action.get('type')
            if kind in {'create','update','sync','resolve_delete'}:return host.scheduling.local.change(action)
            return host.scheduling.change(action)
        if tool=='automation_list':return host.automation.list_automation_jobs()
        if tool=='automation_save':
            payload=args['payload'];payload.setdefault('project_id',project_id);payload.setdefault('agent_id','tebb');payload.setdefault('doc','')
            payload['operation_id']='tebb-'+hashlib.sha256((run_id+':'+call_id).encode()).hexdigest()[:50]
            return host.automation.save_automation_job(payload)
        if tool=='automation_manage':
            if args['action']=='delete':return host.automation.delete_automation_job(args['job_id'])
            if args['action']=='run':return host.automation.run_automation_job(args['job_id'],'tebb-'+hashlib.sha256((run_id+':'+call_id).encode()).hexdigest()[:50])
            if args['action']=='check':return host.automation.check_agent_execution(args['job_id'])
        raise ValueError('不支持的tebb能力。')
    except Exception as exc:return dict(ok=False,error=str(exc),stage='tebb_operation')
