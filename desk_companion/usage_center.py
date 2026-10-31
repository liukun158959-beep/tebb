"""Personal, body-free usage queries. Business owners supply task state."""
import base64
from collections import Counter,defaultdict
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import time

from .memory import TZ
from .paths import data_root
from .resource_lock import ResourceLock
from .usage_store import UsageStore,number,tokens
from .usage_sources import task_metadata


def dimension(value):return 'unknown'if value is None else'value:'+str(value)


def _summary(rows):
    models=[r for r in rows if r['kind']=='model'];tools=[r for r in rows if r['kind']=='tool']
    result=dict(model_calls=len(models),diagnostic_model_calls=sum(r.get('source')=='model_test'for r in models),tool_calls=len(tools),network_attempts=sum(len(r.get('network_attempts',[]))for r in models),
        network_unobserved_calls=sum(not r.get('network_attempts')for r in models),model_states=dict(Counter(r['state']for r in models)),
        tool_states=dict(Counter(r['state']for r in tools)),cost_cny_known=round(sum(r['cost_cny']for r in models if number(r.get('cost_cny'))is not None),8),
        cost_unknown_calls=sum(number(r.get('cost_cny'))is None for r in models))
    for field in ('input_tokens','output_tokens','cached_tokens','reasoning_tokens'):
        values=[(r.get('usage')or{}).get(field)for r in models]
        result[field+'_known']=sum(v for v in values if tokens(v)is not None)
        result[field+'_unknown_calls']=sum(tokens(v)is None for v in values)
    result['total_tokens_known']=result['input_tokens_known']+result['output_tokens_known']
    result['usage_incomplete_calls']=sum(tokens((r.get('usage')or{}).get('input_tokens'))is None or tokens((r.get('usage')or{}).get('output_tokens'))is None for r in models)
    return result


def _legacy(path):
    result=dict(ok=True,records=0,input_tokens_known=0,output_tokens_known=0,input_unknown_records=0,output_unknown_records=0,
        cost_cny_known=0.,cost_unknown_records=0,merged=False)
    path=Path(path)
    if not path.is_file():return result
    try:
        with ResourceLock('usage'),path.open(encoding='utf-8')as stream:
            for line in stream:
                if not line.strip():continue
                rec=json.loads(line)
                if not isinstance(rec,dict)or not isinstance(rec.get('ts'),str):raise ValueError()
                datetime.fromisoformat(rec['ts'])
                if result['records']>=100000:raise OverflowError()
                result['records']+=1
                for name in ('input_tokens','output_tokens'):
                    value=tokens(rec.get(name))
                    if value is None:result[name.split('_')[0]+'_unknown_records']+=1
                    else:result[name+'_known']+=value
                cost=number(rec.get('cost_cny'))
                if cost is None:result['cost_unknown_records']+=1
                else:result['cost_cny_known']+=cost
        result['cost_cny_known']=round(result['cost_cny_known'],8)
        return result
    except (OSError,ValueError,TypeError,OverflowError):
        return dict(ok=False,merged=False,records=None,error='历史汇总读取未完成或超出单次范围，保留原文件，未并入精确统计。')


class UsageCenter:
    def __init__(self,store=None,sources=None,legacy_path=None,clock=None,admissions=None):
        self.store=store or UsageStore();self.sources=sources or task_metadata
        from .usage import usage_path
        self.legacy_path=legacy_path if legacy_path is not None else usage_path();self.clock=clock or time.time
        if admissions is None:
            from .task_budgets import Ledger
            admissions=Ledger().usage_admissions
        self.admissions=admissions

    def query(self,filters=None,cursor='',limit=50):
        try:return self._query(filters if filters is not None else{},cursor,limit)
        except ValueError as exc:return dict(ok=False,error=str(exc))
        except Exception as exc:return dict(ok=False,error='使用记录读取未完成（'+type(exc).__name__+'），原数据未改动。')

    def _query(self,filters,cursor,limit):
        if not isinstance(filters,dict)or set(filters)-{'period','model','project','channel','kind'}:raise ValueError('使用筛选参数无效。')
        selected=dict(period='30',model='*',project='*',channel='*',kind='all')|filters
        if not all(isinstance(v,str)and len(v)<=512 for v in selected.values())or selected['period']not in{'7','30','all'}or selected['kind']not in{'all','model','tool','task','physical','diagnostic'}:raise ValueError('使用筛选值无效。')
        if type(limit)is not int or not 1<=limit<=200:raise ValueError('每页条数须为1到200。')
        signature=hashlib.sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest();until=self.clock();before=None
        if cursor:
            try:
                if not isinstance(cursor,str)or len(cursor)>2048:raise ValueError()
                value=json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if set(value)!={'version','until','started','id','signature'}or value['version']!=1 or value['signature']!=signature:raise ValueError()
                if number(value['until'])is None or value['until']>until+60 or number(value['started'])is None or value['started']>value['until']or not isinstance(value['id'],str)or not 1<=len(value['id'])<=512:raise ValueError()
                until=value['until'];before=(value['started'],value['id'])
            except Exception:raise ValueError('分页游标无效或筛选已变化，请从第一页读取。')from None
        today=datetime.fromtimestamp(until,TZ).replace(hour=0,minute=0,second=0,microsecond=0)
        since=0 if selected['period']=='all'else(today-timedelta(days=int(selected['period'])-1)).timestamp()
        rows=self.store.rows(since,until);source=self.sources()
        terminal={r['id']for r in source['attempts']if r['state']in{'succeeded','failed','cancelled','timed_out','interrupted'}}
        rows=[{**r,'observed_state':r['state'],'state':'unknown'}if r['state']=='running'and r.get('task')in terminal else r for r in rows]
        tasks=[r for r in source['tasks']if since<=r['created']<until]
        physical=[r for r in source['attempts']if since<=r['created']<until]
        options=dict(models=[],projects=[],channels=[])
        models={r['model_key']:dict(key=r['model_key'],model=r.get('model',''),model_id=r.get('model_id',''),route=r.get('route',''))for r in rows if r['kind']=='model'}
        options['models']=sorted(models.values(),key=lambda r:(r['model'],r['key']))
        options['projects']=sorted({dimension(r.get('project_id'))for r in[*rows,*tasks]})
        options['channels']=sorted({dimension(r.get('channel'))for r in[*rows,*tasks]})
        def matches(row):
            return(selected['project']=='*'or dimension(row.get('project_id'))==selected['project'])and(selected['channel']=='*'or dimension(row.get('channel'))==selected['channel'])
        tasks=[r for r in tasks if matches(r)];physical=[r for r in physical if matches(r)]
        rows=[r for r in rows if matches(r)and(r['kind']!='model'or selected['model']=='*'or r.get('model_key')==selected['model'])]
        goals=[r for r in tasks if r['kind']=='task'];diagnostics=[r for r in tasks if r['kind']=='diagnostic']
        owners={r['id']for r in tasks};observed_ids=self.store.model_ids(owners)
        unobserved=sum(r['id']not in observed_ids for r in self.admissions(owners))
        totals=_summary(rows)|dict(tasks=len(goals),diagnostics=len(diagnostics),physical_attempts=len(physical),
            task_states=dict(Counter(r['state']for r in goals)),physical_states=dict(Counter(r['state']for r in physical)))
        by_model=[]
        for key,meta in models.items():
            group=[r for r in rows if r['kind']=='model'and r['model_key']==key]
            if group:by_model.append({**meta,**_summary(group)})
        days=defaultdict(list)
        for row in rows:days[datetime.fromtimestamp(row['started'],TZ).date().isoformat()].append(row)
        goal_days=Counter(datetime.fromtimestamp(r['created'],TZ).date().isoformat()for r in goals)
        by_day=[dict(day=day,tasks=goal_days[day],**_summary(days[day]))for day in sorted(set(days)|set(goal_days))]
        task_items=[{**r,'started':r['created'],'updated':r['created']}for r in tasks]
        physical_items=[{**r,'kind':'physical','task':r['id'],'id':'physical:'+r['id'],'started':r['created'],'updated':r['created']}for r in physical]
        items=sorted([r for r in[*rows,*task_items,*physical_items]if selected['kind']=='all'or r['kind']==selected['kind']or selected['kind']=='diagnostic'and r.get('source')=='model_test'],key=lambda r:(r['started'],r['id']),reverse=True)
        if before:items=[r for r in items if(r['started'],r['id'])<before]
        page=items[:limit];more=len(items)>limit;next_cursor=''
        if more:
            last=page[-1]
            next_cursor=base64.urlsafe_b64encode(json.dumps(dict(version=1,until=until,started=last['started'],id=last['id'],signature=signature)).encode()).decode()
        return dict(ok=True,version=1,filters=selected,range=dict(since=since,until=until,timezone='UTC+8'),
            totals=totals,by_model=by_model,by_day=by_day,options=options,items=page,next_cursor=next_cursor,
            legacy=_legacy(self.legacy_path),coverage=dict(model='adapter_attempts',tool='actual_adapter_attempts',history_merged=False,
                model_filter_scope='model_calls_only',task_time='first_submission',attempt_time='adapter_start',payloads=False,
                first_observation=self.store.first_observation(),unobserved_model_admissions=unobserved,
                unattributed_physical_attempts=sum(not r.get('root')for r in physical),
                admission_scope='matched_task_roots_not_physical_calls'))
