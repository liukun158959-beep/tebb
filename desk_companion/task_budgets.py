"""Product-owned root quotas. Events are projections, never admission authority."""
from contextlib import contextmanager, closing
from contextvars import ContextVar
import copy
import hashlib
import json
import math
import sqlite3
import time
import uuid
from functools import wraps

from .paths import data_root

VERSION=1
DIMENSIONS=('model_calls','tool_calls','input_tokens','output_tokens','time_ms')
_ROWS={
    'simple':(8,12,64000,16000,180000,4096),
    'research':(16,24,192000,48000,360000,8192),
    'coding':(24,40,384000,96000,600000,8192),
    'chart':(12,20,128000,32000,300000,6144),
    'clarify':(6,4,48000,12000,180000,2048),
    'probe':(2,0,4000,4096,90000,2048),
}
_SCOPE=ContextVar('task_budget',default=None)
_PHASE=ContextVar('task_budget_phase',default='work')


class BudgetExceeded(RuntimeError):
    def __init__(self,reason,partial=None):
        self.reason=reason
        self.partial=partial
        self.public_message=reason['message']+' 已保留部分成果和原操作回执；未自动重做外部操作。'
        super().__init__(self.public_message)


def policy(tier):
    if tier not in _ROWS:raise ValueError('未知任务预算档位。')
    *limits,per_call=_ROWS[tier]
    return dict(zip(DIMENSIONS,limits))|dict(per_call_output=per_call)


def default_tier(kind='',role=None,workflow=''):
    role=role or{}
    if workflow=='budget_probe':return'probe'
    if kind=='clarify_options':return'clarify'
    tools=set(role.get('tools',()))
    if role.get('id')=='coder'or tools&{'项目环境','文件编辑','终端'}:return'coding'
    if role.get('id')=='chart':return'chart'
    if role.get('id')=='research'or tools&{'联网搜索','网页读取'}or workflow in{'ai_news','video_note','automation_graph'}:return'research'
    return'simple'


def _tokens(value):
    return value if type(value)is int and value>=0 else None


class Ledger:
    def __init__(self,path=None):
        self.path=path or data_root()/'memory/task_budgets.sqlite3'

    def _connect(self,create=False):
        if create:
            self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=10)
        if create:
            db.execute('CREATE TABLE IF NOT EXISTS roots(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS members(task TEXT PRIMARY KEY,root TEXT NOT NULL,data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS owners(owner TEXT PRIMARY KEY,root TEXT NOT NULL UNIQUE)')
            db.commit()
        return db

    @contextmanager
    def change(self,ident):
        db=self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data FROM roots WHERE id=?',(ident,)).fetchone()
            if row is None:raise ValueError('任务预算不存在，未获得调用额度。')
            value=json.loads(row[0]);yield value
            db.execute('UPDATE roots SET data=? WHERE id=?',(json.dumps(value,ensure_ascii=False),ident));db.commit()
        finally:db.close()

    def create(self,owner,tier='simple',hard_time_ms=None,*,prior_usage_unknown=False):
        limits=policy(tier);hard=hard_time_ms if type(hard_time_ms)in(int,float)and hard_time_ms>0 else None
        if hard is not None:limits['time_ms']=min(limits['time_ms'],hard)
        ident=uuid.uuid4().hex
        root=dict(id=ident,owner=owner,tier=tier,version=VERSION,status='active',limits=limits,base=limits.copy(),
            hard_time_ms=hard,finish_reserve=dict(model_calls=0 if tier=='probe'else 2,output_tokens=0 if tier=='probe'else 4096),
            calls={},tools=0,progress=[],extensions=[],no_progress=0,
            created=time.time(),spent_ms=0,active_since=None,waiting_user=False,stop_reason=None,tier_decided=False)
        root['prior_usage_unknown']=bool(prior_usage_unknown)
        with closing(self._connect(create=True))as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT root FROM owners WHERE owner=?',(owner,)).fetchone()
            if old:return old[0]
            db.execute('INSERT INTO roots VALUES(?,?)',(ident,json.dumps(root,ensure_ascii=False)))
            db.execute('INSERT INTO owners VALUES(?,?)',(owner,ident));db.commit()
        return ident

    def for_owner(self,owner):
        if not self.path.exists():return None
        with closing(self._connect())as db:row=db.execute('SELECT root FROM owners WHERE owner=?',(owner,)).fetchone()
        return row[0]if row else None

    def bind(self,ident,task,*,root_member=False,purpose='task'):
        with closing(self._connect())as db:
            db.execute('BEGIN IMMEDIATE')
            root_row=db.execute('SELECT data FROM roots WHERE id=?',(ident,)).fetchone()
            if not root_row:raise ValueError('任务预算不存在。')
            previous=db.execute('SELECT root,data FROM members WHERE task=?',(task,)).fetchone()
            member=dict(root_member=bool(root_member),purpose=purpose)
            if previous:
                if previous[0]!=ident:raise ValueError('物理任务已绑定其他根预算。')
                return
            db.execute('INSERT INTO members VALUES(?,?,?)',(task,ident,json.dumps(member)))
            if root_member:
                root=json.loads(root_row[0]);root['last_root_member']=task
                db.execute('UPDATE roots SET data=? WHERE id=?',(json.dumps(root,ensure_ascii=False),ident))
            db.commit()

    def member(self,task):
        if not self.path.exists():return None
        with closing(self._connect())as db:row=db.execute('SELECT root,data FROM members WHERE task=?',(task,)).fetchone()
        return dict(id=row[0],**json.loads(row[1]))if row else None

    def read(self,ident):
        if not self.path.exists():return None
        with closing(self._connect())as db:row=db.execute('SELECT data FROM roots WHERE id=?',(ident,)).fetchone()
        return json.loads(row[0])if row else None

    @staticmethod
    def elapsed(root,now=None):
        return root['spent_ms']+max(0,((time.time()if now is None else now)-root['active_since'])*1000)if root['active_since']is not None else root['spent_ms']

    def start(self,task):
        member=self.member(task)
        if not member:return
        with self.change(member['id'])as root:
            if root['status']!='active':raise BudgetExceeded(root['stop_reason']or self.reason('closed','任务预算已停止。'))
            if root['active_since']is None:root['active_since']=time.time()
            active=root.setdefault('active_members',[])
            if task not in active:active.append(task)

    def end(self,task):
        member=self.member(task)
        if not member:return
        with self.change(member['id'])as root:
            root['active_members']=[ident for ident in root.get('active_members',[])if ident!=task]
            if root['waiting_user']and not root['active_members']and root['active_since']is not None:
                root['spent_ms']=self.elapsed(root);root['active_since']=None

    def waiting(self,ident,value):
        with self.change(ident)as root:
            if value is not None:root['waiting_user']=bool(value)
            active=root.get('active_members',[])
            if root['waiting_user']and not active and root['active_since']is not None:
                root['spent_ms']=self.elapsed(root);root['active_since']=None
            elif not root['waiting_user']and root['active_since']is None and root['status']=='active':root['active_since']=time.time()

    def finish(self,ident,status='finished'):
        with self.change(ident)as root:
            root['spent_ms']=self.elapsed(root);root['active_since']=None
            if root['status']not in{'blocked','cancelled'}:root['status']=status

    def resume(self,ident):
        with self.change(ident)as root:
            if root['status']=='blocked':raise BudgetExceeded(root['stop_reason']or self.reason('closed','该任务预算不能自动重置。'))
            root.update(status='active',waiting_user=False)
            if root['active_since']is None:root['active_since']=time.time()

    def choose_tier(self,ident,tier,*,decided=True):
        limits=policy(tier)
        with self.change(ident)as root:
            if root['tier_decided']:return
            if root['hard_time_ms']is not None:limits['time_ms']=min(limits['time_ms'],root['hard_time_ms'])
            root.update(tier=tier,limits=limits,base=limits.copy(),tier_decided=decided)

    @staticmethod
    def reason(dimension,message,**details):return dict(code='budget_'+dimension,dimension=dimension,message=message,**details)

    @staticmethod
    def usage(root):
        calls=list(root['calls'].values())
        return dict(model_calls=len(calls),tool_calls=root['tools'],
            input_tokens=sum(c['input_tokens']for c in calls if c.get('input_tokens')is not None),
            output_tokens=sum(c['output_tokens']for c in calls if c.get('output_tokens')is not None),
            input_unknown_calls=sum(c.get('input_tokens')is None for c in calls),
            output_unknown_calls=sum(c.get('output_tokens')is None for c in calls),
            output_occupied=sum(c.get('output_tokens')if c.get('output_tokens')is not None else c['reserved_output']for c in calls),
            input_occupied=sum(c.get('input_tokens')if c.get('input_tokens')is not None else c.get('reserved_input')or 0 for c in calls))

    def _check_time(self,root):
        if root['status']!='active':return root['stop_reason']or self.reason('closed','任务预算已停止；继续原目标需要调度器恢复，已用额度不重置。')
        if self.elapsed(root)>=root['limits']['time_ms']:return self.reason('time_ms','任务执行时间预算已到。')

    def time_expired(self,ident):
        with self.change(ident)as root:
            reason=self._check_time(root)
            if reason and reason['dimension']=='time_ms'and self._extend(root,'time_ms'):reason=self._check_time(root)
            if reason:root['stop_reason']=reason
            return reason

    def _extend(self,root,dimension):
        if root['extensions']or not root['progress']or root['no_progress']>=3 or root['tier']=='probe':return False
        before=root['limits'].copy()
        for key in DIMENSIONS:
            root['limits'][key]=root['base'][key]+math.floor(root['base'][key]*.25)
        if root['hard_time_ms']is not None:root['limits']['time_ms']=min(root['limits']['time_ms'],root['hard_time_ms'])
        root['extensions'].append(dict(at=time.time(),trigger=dimension,before=before,after=root['limits'].copy(),progress=copy.deepcopy(root['progress'][-3:])))
        return True

    def reserve_call(self,task,input_tokens,*,phase='work',requested_output=None,enforced=False,requested_parameter=None):
        member=self.member(task)
        if not member:raise ValueError('模型请求缺少可信任务预算成员。')
        ident=uuid.uuid4().hex;reason=None;output=None
        with self.change(member['id'])as root:
            finish=member['root_member']and phase in{'finish','feedback_review'}
            for attempt in range(2):
                used=self.usage(root);reserve=root['finish_reserve']if not finish else dict(model_calls=0,output_tokens=0)
                reason=self._check_time(root)
                if not reason and phase=='work'and root['no_progress']>=3:reason=self.reason('no_progress','连续模型工作未产生可核对新进展，已停止新调用。')
                if not reason and used['model_calls']>=root['limits']['model_calls']-reserve['model_calls']:
                    reason=self.reason('model_calls','模型请求额度已到，保留收尾额度。')
                if not reason and input_tokens is not None and used['input_occupied']+input_tokens>root['limits']['input_tokens']:
                    reason=self.reason('input_tokens','已知输入token超过任务预算。')
                if not reason and used['input_tokens']>=root['limits']['input_tokens']:reason=self.reason('input_tokens','已回报输入用量达到任务预算。')
                available=root['limits']['output_tokens']-used['output_occupied']-reserve['output_tokens']
                output=min(root['limits']['per_call_output'],available)
                if requested_output is not None:output=min(output,requested_output)
                if not reason and output<=0:reason=self.reason('output_tokens','输出token额度已到，保留收尾额度。')
                if reason and attempt==0 and reason['dimension']not in{'no_progress','closed','provider_limit'}and root['status']=='active'and self._extend(root,reason['dimension']):continue
                break
            if reason:root['stop_reason']=reason
            else:
                root['calls'][ident]=dict(task=task,phase=phase,state='reserved',reserved_input=input_tokens,reserved_output=output,
                    input_tokens=None,output_tokens=None,enforced=bool(enforced),requested_parameter=requested_parameter,progress_seq=len(root['progress']))
        if reason:raise BudgetExceeded(reason)
        return ident,output

    def settle(self,task,call_id,usage,*,finish_reason=None):
        member=self.member(task)
        with self.change(member['id'])as root:
            call=root['calls'][call_id]
            if call['state']!='reserved':return
            usage=usage if isinstance(usage,dict)else{}
            call.update(input_tokens=_tokens(usage.get('prompt_tokens')),output_tokens=_tokens(usage.get('completion_tokens')))
            call['state']='settled'if call['input_tokens']is not None and call['output_tokens']is not None else'unknown'
            call['finish_reason']=finish_reason
            if finish_reason=='length'and member['purpose']!='budget_probe':
                parameter=call.get('requested_parameter')
                note=f"实际请求{parameter}={call['reserved_output']}。"if parameter else'服务输出上限未绑定。'
                root['stop_reason']=self.reason('per_call_output','模型以length结束，片段未当作完整结果执行。'+note,requested_output=call['reserved_output']if parameter else None)
            if call['phase']=='work':
                root['no_progress']=root['no_progress']+1 if len(root['progress'])==call['progress_seq']else 0
            if call['enforced']and call['output_tokens']is not None and call['output_tokens']>call['reserved_output']:
                root.update(status='blocked',stop_reason=self.reason('provider_limit','模型服务回报超过请求的输出上限，已停止后续调用。'))
                return root['stop_reason']

    def reserve_tool(self,task):
        member=self.member(task);reason=None
        if not member:raise ValueError('工具执行缺少可信任务预算成员。')
        with self.change(member['id'])as root:
            reason=self._check_time(root)
            if not reason and root['tools']>=root['limits']['tool_calls']:
                if not self._extend(root,'tool_calls'):reason=self.reason('tool_calls','真实工具执行额度已到。')
            if reason:root['stop_reason']=reason
            else:root['tools']+=1
        if reason:raise BudgetExceeded(reason)

    def progress(self,task,kind,reference,fingerprint):
        member=self.member(task)
        if not member:return
        key=hashlib.sha256(json.dumps([kind,reference,fingerprint],sort_keys=True).encode()).hexdigest()
        with self.change(member['id'])as root:
            if any(p['key']==key for p in root['progress']):return
            root['progress'].append(dict(key=key,kind=kind,reference=reference,at=time.time()))
            root['no_progress']=0

    def log_progress(self,task,reference,start,end,fingerprint):
        member=self.member(task)
        if not member:return
        with self.change(member['id'])as root:
            covered=start
            ranges=sorted((p['start'],p['end'])for p in root['progress']if p['kind']=='log_bytes'and p['reference']==reference)
            for begin,finish in ranges:
                if begin>covered:break
                covered=max(covered,finish)
                if covered>=end:return
            key=hashlib.sha256(json.dumps(['log_bytes',reference,start,end,fingerprint]).encode()).hexdigest()
            root['progress'].append(dict(key=key,kind='log_bytes',reference=reference,start=start,end=end,at=time.time()))
            root['no_progress']=0

    def view(self,ident):
        root=self.read(ident)
        if root is None:return None
        used=self.usage(root);elapsed=self.elapsed(root)
        return dict(root_budget_id=root['id'],tier=root['tier'],policy_version=root['version'],status=root['status'],
            limits=root['limits'],consumed=used|dict(time_ms=round(elapsed)),
            remaining=dict(model_calls=max(0,root['limits']['model_calls']-used['model_calls']),
                tool_calls=max(0,root['limits']['tool_calls']-used['tool_calls']),
                input_tokens=None if not root['calls']or any(c['reserved_input']is None for c in root['calls'].values())or used['input_unknown_calls']else max(0,root['limits']['input_tokens']-used['input_tokens']),
                output_tokens=max(0,root['limits']['output_tokens']-used['output_occupied']),time_ms=max(0,round(root['limits']['time_ms']-elapsed))),
            input_mode='exact'if root['calls']and all(c['reserved_input']is not None for c in root['calls'].values())else'measured_or_unknown',output_mode='requested_hard'if root['calls']and all(c['enforced']for c in root['calls'].values())else'measured_or_unknown',
            finish_reserve=root['finish_reserve'],extensions=root['extensions'],no_progress_calls=root['no_progress'],
            waiting_user=root['waiting_user'],stop_reason=root['stop_reason'],prior_usage_unknown=root.get('prior_usage_unknown',False),cost=None)


class Scope:
    def __init__(self,task,emit=None):
        self.task=task;self.ledger=Ledger();self.emit=emit
        self.member=self.ledger.member(task)
        if not self.member:raise ValueError('任务预算归属未登记。')

    def phase(self):
        explicit=current_phase()
        if explicit!='work':return explicit
        if self.member['purpose']=='feedback_review':return'feedback_review'
        if self.member['purpose']in{'route','plan','compress','chat','clarify_options','debug_explain','budget_probe'}:return'control'
        if self.finishing():
            with self.ledger.change(self.member['id'])as root:
                if not root['stop_reason']:
                    dimension='no_progress'if root['no_progress']>=3 else'model_calls'
                    root['stop_reason']=self.ledger.reason(dimension,'新的工作请求已停止，正在用保留额度整理已有成果与缺口。')
            return'finish'
        return'work'

    def finishing(self):
        if not self.member['root_member']:return False
        root=self.ledger.read(self.member['id'])
        return root['no_progress']>=3 or len(root['calls'])>=root['limits']['model_calls']-root['finish_reserve']['model_calls']or(root.get('stop_reason')or{}).get('dimension')in{'tool_calls','input_tokens','output_tokens'}

    def notice(self):
        if self.emit:self.emit('task_budget',self.ledger.view(self.member['id']))

    def tool(self,read_only=True,kind=''):
        tier='coding'if kind.startswith(('project_','workspace_'))and kind not in{'project_chat_search'}else'research'if kind in{'web_search','web_fetch','github_repository','read_video'}else'chart'if kind=='build_chart'else None
        root=self.ledger.read(self.member['id'])
        if tier and self.member['root_member']and root['tier']in{'simple','clarify'}:self.ledger.choose_tier(self.member['id'],tier)
        if not read_only and self.finishing():raise BudgetExceeded(self.ledger.reason('finish','当前仅保留整理已有成果的收尾额度，未执行新的写入。'))
        self.ledger.reserve_tool(self.task);self.notice()

    def progress(self,kind,reference,fingerprint):self.ledger.progress(self.task,kind,reference,fingerprint);self.notice()

    def log_progress(self,reference,start,end,fingerprint):
        self.ledger.log_progress(self.task,reference,start,end,fingerprint);self.notice()


@contextmanager
def installed(task,emit=None):
    scope=Scope(task,emit);token=_SCOPE.set(scope)
    try:yield scope
    finally:_SCOPE.reset(token)


@contextmanager
def phase(name):
    token=_PHASE.set(name)
    try:yield
    finally:_PHASE.reset(token)


def current():return _SCOPE.get()
def current_phase():return _PHASE.get()


def task_view(task):
    ledger=Ledger();member=ledger.member(task)
    return ledger.view(member['id'])if member else None


def budgeted_operation(name,tier='simple'):
    """Legacy synchronous user operations share one scope, or inherit a worker's."""
    def decorate(function):
        @wraps(function)
        def run(*args,**kwargs):
            if current():return function(*args,**kwargs)
            task='operation-'+uuid.uuid4().hex;ledger=Ledger()
            root=ledger.create(name+':'+task,tier);ledger.bind(root,task,root_member=True)
            ledger.start(task)
            try:
                from .agent_debug import context
                with installed(task),context(task_id=task):return function(*args,**kwargs)
            finally:ledger.end(task);ledger.finish(root)
        return run
    return decorate


def record_progress(tool,arguments,result):
    scope=current()
    if scope is None:return
    if isinstance(result,dict)and'output'in result:result=result['output']
    if isinstance(result,str):
        try:result=json.loads(result)
        except (ValueError,TypeError):return
    if not isinstance(result,dict)or result.get('ok')is False or result.get('error'):return
    operation=result.get('operation')or{}
    if isinstance(operation,dict)and operation.get('state')in{'unknown','failed','cancelled','running'}:return
    from .web_sources import evidence_sources
    for source in evidence_sources([result]):
        if source.get('level')=='body'and source.get('text'):
            scope.progress('body',source.get('url',source.get('source_id','')),hashlib.sha256(source['text'].encode()).hexdigest())
    digest=result.get('sha256')or result.get('revision')
    if tool in{'project_read','workspace_read','workspace_write'}and isinstance(digest,str):scope.progress('file',str(arguments.get('path','')),digest)
    text=result.get('text')or result.get('content')
    if tool in{'read_local_source','project_read','workspace_read'}and isinstance(text,str)and text:
        scope.progress('local_body',str(arguments.get('source_id')or arguments.get('path','')),hashlib.sha256(text.encode()).hexdigest())
    if tool=='project_log'and isinstance(text,str)and text:
        start=result.get('start_offset');end=result.get('offset');size=result.get('bytes')
        if (result.get('environment')==arguments.get('environment')and result.get('operation')==arguments.get('operation')
            and type(start)is int and type(end)is int and type(size)is int and 0<size<=65536
            and start==arguments.get('offset',0)and end-start==size and start>=0
            and isinstance(digest,str)and len(digest)==64 and all(c in'0123456789abcdef'for c in digest)):
            reference=f"{result['environment']}/{result['operation']}"
            scope.log_progress(reference,start,end,digest)
    if tool=='build_chart'and isinstance(result.get('id'),str):scope.progress('artifact',result['id'],result['id'])
    if isinstance(operation,dict)and operation.get('state')=='succeeded':
        meaningful={k:result.get(k)for k in ('exit_code','command_sha256','service_ready')}
        meaningful['arguments']={key:value for key,value in arguments.items()if key not in{'_call_id','request_id'}}
        scope.progress('operation',tool,hashlib.sha256(json.dumps(meaningful,sort_keys=True,default=str).encode()).hexdigest())
