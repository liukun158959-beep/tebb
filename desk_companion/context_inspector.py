"""Read actual conversation calls at the existing model transport boundary."""
import json
import math
import re

from . import agent_debug,model_capacity


def estimate(value):
    # Character heuristic, including schemas; never pretend a provider tokenizer.
    text=json.dumps(value,ensure_ascii=False)
    cjk=len(re.findall(r'[\u3000-\u9fff]',text))
    return math.ceil(cjk+(len(text)-cjk)/4)


def budget(request,response,capacity):
    usage=response.get('usage') or {}
    if not isinstance(usage,dict):usage={}
    tokens=usage.get('prompt_tokens',usage.get('input_tokens'))
    actual=type(tokens) is int and tokens>=0
    tokens=tokens if actual else estimate(request)
    window=capacity.get('window');reserved=capacity.get('output_reserve',0)
    media=bool(re.search(r'"(?:image_url|input_audio|video_url)"',json.dumps(request)))
    return dict(input_tokens=tokens,input_source='provider' if actual else 'estimate',window=window,
                output_reserve=reserved,remaining=max(0,window-tokens-reserved) if window else None,
                over_by=max(0,tokens+reserved-window) if window else 0,media_estimate=media and not actual,
                capacity_source=capacity.get('source','unknown'))


def inspect(collaboration,conversation,call_id='',before=None,draft=''):
    if not isinstance(draft,str) or len(draft)>200000:raise ValueError('草稿格式无效。')
    if before is not None and (type(before) not in (int,float) or not math.isfinite(before)):raise ValueError('游标无效。')
    state=collaboration.snapshot()['state']
    chat=next((c for c in state['conversations'] if c['id']==conversation),None)
    if chat is None:raise ValueError('会话不存在。')
    runs={r['id']:r for r in state['runs'] if r['conversation']==conversation}
    sessions={'collab-'+ident:run for ident,run in runs.items()}
    rows=[]
    with agent_debug.database() as db:
        keys=list(sessions)
        for offset in range(0,len(keys),200):
            chunk=keys[offset:offset+200]
            query='SELECT * FROM calls WHERE session IN ('+','.join('?' for _ in chunk)+')'
            query+=' AND (? IS NULL OR created<?) ORDER BY created DESC LIMIT 101'
            rows.extend(dict(r) for r in db.execute(query,(*chunk,before,before)))
        if call_id:
            agent_debug.valid_id(call_id)
            row=db.execute('SELECT * FROM calls WHERE id=?',(call_id,)).fetchone()
            if row is None or row['session'] not in sessions:raise ValueError('调用不属于当前会话或记录不存在。')
    rows.sort(key=lambda r:(r['created'],r['id']),reverse=True)
    more=len(rows)>100;rows=rows[:100]
    for row in rows:
        run=sessions[row['session']]
        meta_path=agent_debug.root()/(row['id']+'.meta.json')
        meta=json.loads(meta_path.read_text('utf-8')) if meta_path.exists() else {}
        row.update(run_id=run['id'],parent=run.get('parent'),agent=run['agent'],phase=meta.get('phase') or '历史阶段未标注')
    # Prefer latest root execution; a sub-agent's large prompt is not main usage.
    selected=call_id or next((r['id'] for r in rows if not r['parent']),rows[0]['id'] if rows else '')
    detail=agent_debug.detail(selected) if selected else None
    current=None;model_id=''
    if detail:
        run=sessions[detail['call']['session']];model_id=run.get('model_id','')
        capacity=detail.get('meta',{}).get('capacity')
        if capacity and capacity.get('window'):capacity={**capacity,'source':'captured_config'}
        else:capacity=model_capacity.for_call(model_id,detail['call']['model'],detail['call']['endpoint'])
        current=budget(detail['request'],detail['response'],capacity)
        if draft:
            current['draft_reference']=dict(input_tokens=current['input_tokens']+estimate(draft),source='last_request_plus_draft',
                note='仅参考上次实际请求与当前草稿；未组装下一次路由、工具结果及压缩后的上下文。')
    agents=[dict(id=a['id'],name=a['name'],memory=a.get('memory','')) for a in state['agents'] if a['id'] in chat['members']]
    return dict(ok=True,items=rows,has_more=more,selected=selected,detail=detail,budget=current,model_id=model_id,agents=agents,
                recording=agent_debug.settings()['enabled'],note='请求正文按原样保留；查看不调用模型。没有记录不能还原过去的请求。')
