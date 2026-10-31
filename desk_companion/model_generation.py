"""Verified generation parameter bindings, separate from context capacity."""
import hashlib
import json
import sqlite3
from contextlib import closing

from .paths import data_root

PARAMETERS={'max_completion_tokens','max_tokens'}


def identity(model,endpoint):
    return hashlib.sha256(json.dumps([model,endpoint.rstrip('/')],ensure_ascii=False).encode()).hexdigest()


def path():return data_root()/'memory/model_generation.sqlite3'


def binding(model_id,model,endpoint):
    if not path().exists():return None
    with closing(sqlite3.connect(path().as_uri()+'?mode=ro',uri=True,timeout=10))as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bindings'").fetchone():return None
        row=db.execute('SELECT value FROM bindings WHERE model=?',(model_id,)).fetchone()
    if row is None:return None
    try:value=json.loads(row[0])
    except ValueError:return None
    if not isinstance(value,dict)or value.get('identity')!=identity(model,endpoint)or value.get('parameter')not in PARAMETERS:return None
    expected=[dict(requested=n,actual=n,finish_reason='length')for n in(64,128)]
    if value.get('probes')!=expected or type(value.get('verified_at'))not in(int,float):return None
    return value


def public(model_id):
    from .model_catalog import require_item
    item=require_item(model_id)
    value=binding(model_id,item['model'],item['base_url'].rstrip('/')+'/chat/completions')
    return dict(ok=True,model_id=model_id,verified=value is not None and not value.get('invalidated'),parameter=value['parameter']if value else None,
        verified_at=value['verified_at']if value else None,probes=value.get('probes',[])if value else [])


def invalidate(model_id,model,endpoint,parameter):
    if not path().exists():return
    with closing(sqlite3.connect(path(),timeout=10))as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT value FROM bindings WHERE model=?',(model_id,)).fetchone()
        if row:
            value=json.loads(row[0])
            if value.get('identity')==identity(model,endpoint)and value.get('parameter')==parameter:
                value['invalidated']=True
                db.execute('UPDATE bindings SET value=? WHERE model=?',(json.dumps(value,ensure_ascii=False),model_id))
        db.commit()


def verify(request,emit):
    """Exactly two bounded, synthetic, tool-free calls. Never guess another parameter."""
    from .model_catalog import require_item
    import time
    parameter=request.get('parameter')
    if parameter not in PARAMETERS:raise ValueError('请选择max_completion_tokens或max_tokens，不自动尝试其他参数。')
    item=require_item(request['model_id'])
    try:endpoint,results=_probe(item,parameter,emit)
    except Exception:
        invalidate(item['id'],item['model'],item['base_url'].rstrip('/')+'/chat/completions',parameter)
        raise
    value=dict(identity=identity(item['model'],endpoint),parameter=parameter,verified_at=time.time(),probes=results)
    path().parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path(),timeout=10))as db:
        db.execute('CREATE TABLE IF NOT EXISTS bindings(model TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('INSERT INTO bindings VALUES(?,?) ON CONFLICT(model) DO UPDATE SET value=excluded.value',
            (item['id'],json.dumps(value,ensure_ascii=False)));db.commit()
    emit('result','两次有限探针通过，已保存该模型/路由的输出参数绑定；未声明上下文窗口或费用。')


def _probe(item,parameter,emit):
    from atlas.llm import ChatOptions,IncompleteResponseError
    from .execution_context import ExecutionLLM
    from .model_catalog import bind_llm
    import time
    results=[]
    with ExecutionLLM(api_key=item['api_key'],base_url=item['base_url'],model=item['model'],context_model_id=item['id'])as llm:
        bind_llm(llm,item,timeout=30)
        endpoint=str(llm.client.base_url).rstrip('/')+'/chat/completions'
        for cap in (64,128):
            emit('llm_start',dict(phase='budget_probe',model=item['model'],requested_output=cap))
            started=time.monotonic()
            try:
                try:
                    result=llm.chat([dict(role='user',content='依次列出1到300，不要省略。')],tools=[],options=ChatOptions(extra_body={parameter:cap},stream=True))
                    finish='stop'
                except IncompleteResponseError as exc:result=exc.partial;finish=exc.finish_reason
                usage=result.get('usage')or{};actual=usage.get('completion_tokens')
                row=dict(requested=cap,actual=actual,finish_reason=finish)
                results.append(row)
                emit('output_limit_probe',row)
                if finish!='length'or type(actual)is not int or actual!=cap:
                    raise ValueError('该模型/路由未回报两次准确截断上限；没有保存已验证绑定。')
            finally:emit('llm_end',dict(phase='budget_probe',duration_ms=round((time.monotonic()-started)*1000)))
    return endpoint,results
