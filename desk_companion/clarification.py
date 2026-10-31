"""Validated human choices; the execution owns waiting and resumption."""
import hashlib
import json
import re
import uuid
import time

ID = re.compile(r'[A-Za-z0-9_-]{1,40}\Z')
QUESTION_SCHEMA = dict(type='array', minItems=1, maxItems=3, items=dict(type='object', properties={
    'id':dict(type='string',pattern='^[A-Za-z0-9_-]{1,40}$'), 'question':dict(type='string',minLength=1,maxLength=600),
    'options':dict(type='array', minItems=2, maxItems=4, items=dict(type='object', properties={
        'id':dict(type='string',pattern='^(?!custom$)[A-Za-z0-9_-]{1,40}$'), 'label':dict(type='string',minLength=1,maxLength=100), 'value':dict(type='string',minLength=1,maxLength=500)}, required=['id','label','value'], additionalProperties=False))},
    required=['id','question','options'], additionalProperties=False))


def duration_options(question):
    # Offer durations only: do not let generated arithmetic contradict start time.
    heading=question.split('。',1)[0][:1000]
    return dict(question=heading+'。只需选择持续时长；未要求飞书时默认本地保存。',questions=[dict(id='duration',question='这场日程持续多久？',options=[
        dict(id='half_hour',label='30分钟',value='持续30分钟'),dict(id='one_hour',label='1小时',value='持续1小时'),dict(id='ninety_minutes',label='1.5小时',value='持续1.5小时')])])


def static_options(request):
    text,question=request.get('text',''),request.get('question','')
    calendar='日程' in text or bool(re.search(r'(?i)\bcalendar\b',text))
    missing=bool(re.search(r'(?:缺少|缺|未指定|未提供|请选择).{0,20}(?:时长|结束时间)|(?:时长|结束时间).{0,12}(?:多久|未指定)',question))
    anchored=bool(re.search(r'20\d{2}(?:年|-)\d{1,2}(?:月|-)\d{1,2}',question) and re.search(r'\d{1,2}:\d{2}',question))
    other_missing=bool(re.search(r'哪天|几点开始|标题.*(?:缺少|未提供|是什么)|日期.*(?:未定|未明确)',question))
    return duration_options(question) if calendar and missing and anchored and not other_missing else None


def _text(value, limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError('补充问题的文本长度或类型无效。')
    return value.strip()


def questions(raw):
    if not isinstance(raw,list) or not 1<=len(raw)<=3:raise ValueError('请提供1至3项补充问题。')
    result=[];seen=set()
    for row in raw:
        if not isinstance(row,dict) or set(row)!={'id','question','options'}:raise ValueError('补充问题结构无效。')
        ident=_text(row['id'],40)
        if not ID.fullmatch(ident) or ident in seen:raise ValueError('补充问题编号无效或重复。')
        seen.add(ident);options=row['options']
        if not isinstance(options,list) or not 2<=len(options)<=4:raise ValueError('每个问题提供2至4个选项。')
        choices=[];used=set()
        for option in options:
            if not isinstance(option,dict) or set(option)!={'id','label','value'}:raise ValueError('选项结构无效。')
            key=_text(option['id'],40)
            if not ID.fullmatch(key) or key=='custom' or key in used:raise ValueError('选项编号无效或重复。')
            used.add(key);choices.append(dict(id=key,label=_text(option['label'],100),value=_text(option['value'],500)))
        result.append(dict(id=ident,question=_text(row['question'],600),options=choices))
    return result


def version(run):
    return run.get('clarification_version') or hashlib.sha256((run['id']+'\0'+run.get('clarification',run.get('answer',''))).encode()).hexdigest()[:24]


def pause(run, question, items=None):
    run.update(answer=_text(question,4000),clarification=question,clarification_version=uuid.uuid4().hex)
    run.pop('clarification_form',None)
    if items:run['clarification_form']=dict(version=version(run),questions=questions(items))


def answer(run, payload):
    form=run.get('clarification_form')
    if run['state']!='needs_input' or not form or payload.get('version')!=version(run):
        raise ValueError('补充问题已变化或已处理，请重新读取；没有恢复执行。')
    selected=payload.get('answers')
    if not isinstance(selected,dict) or set(selected)!={q['id'] for q in form['questions']}:raise ValueError('请完成所有补充问题。')
    lines=[]
    for question in form['questions']:
        item=selected[question['id']]
        if not isinstance(item,dict):raise ValueError('答案结构无效。')
        if set(item)=={'custom'}:value=_text(item['custom'],500)
        elif set(item)=={'option_id'}:
            option=next((o for o in question['options'] if o['id']==item['option_id']),None)
            if not option:raise ValueError('选项已失效，请重新读取。')
            # Only visible text is the human's answer. A model-supplied hidden
            # value must never manufacture extra user instructions/authority.
            value=option['label']
        else:raise ValueError('请选择一个选项或填写自定义内容。')
        lines.append(question['question']+'：'+value)
    return '补充信息：\n'+'\n'.join(lines)


def public_version(run):
    if run.get('state')=='needs_input':run['clarification_version']=version(run)


def options_decision(text):
    value=json.loads(text)
    return dict(question=_text(value.get('question'),4000),questions=questions(value.get('questions')))


def request_options(run, expected):
    if run['state']!='needs_input' or expected!=version(run):raise ValueError('问题已变化或已处理，请重新读取。')
    if run.get('clarification_form'):raise ValueError('已有选项，请直接选择或使用文字补充。')
    if run.get('options_generation',{}).get('state') in {'pending','queued','running','cancelling'}:raise ValueError('正在生成选项，请稍后读取。')
    run['options_generation']=dict(state='pending',version=version(run),id=uuid.uuid4().hex,cursor=0,calls=0,started=time.time(),error='')


def stop_options(run, manager):
    generation=run.get('options_generation',{})
    if generation.get('state') in {'pending','queued','running','cancelling'}:
        if generation.get('task'):manager.cancel(generation['task'])
        generation['state']='cancelled'


def poll_options(run, manager, path):
    generation=run.get('options_generation',{})
    if generation.get('state') not in {'pending','queued','running','cancelling'}:return
    if run['state']!='needs_input' or generation['version']!=version(run):
        stop_options(run,manager);return
    if generation['state']=='pending':
        generation['task']=manager.submit(run['input'],'clarification-'+run['id'],'collaboration',
            _budget_root=run.get('budget_id'),_budget_root_member=not run.get('parent'),
            submission_key='clarification:'+generation['id'],workflow='collaboration',collab_path=str(path),collab_run=run['id'],
            kind='clarify_options',role={**run['agent_config'],'tools':[]},model_id=run['model_id'],
            context=run.get('context',[])[-4:],question=run.get('clarification',run.get('answer','')),
            reference_time=run.get('created',0),budget=2,sampling=run.get('sampling') or dict(reasoning_effort='low',temperature=.5,top_p=1))
        generation['state']='queued'
    task=manager.get(generation['task'])
    for event in task['events']:
        if event['seq']<=generation['cursor']:continue
        generation['cursor']=event['seq']
        if event['kind']=='llm_start':
            run['calls']+=1;generation['calls']+=1
            run['phase_start_calls']=run.get('phase_start_calls',0)+1
        elif event['kind']=='usage':
            run['input_tokens']+=int(event['data'].get('input_tokens',0));run['output_tokens']+=int(event['data'].get('output_tokens',0))
    generation['state']=task['state']
    if task['state'] not in {'succeeded','failed','cancelled','timed_out','interrupted'}:return
    run.setdefault('phases',[]).append(dict(kind='clarify_options',calls=generation['calls'],started=generation['started'],ended=time.time()))
    if task['state']=='succeeded':
        try:
            value=options_decision(task['answer'])
            pause(run,value['question'],value['questions'])
            run['events'].append('已生成补充选项；仍等待用户选择，未恢复执行')
        except (ValueError,TypeError,AttributeError):
            generation.update(state='failed',error='选项结构无效，原问题保留，可重试或文字补充。')
    else:generation['error']='生成选项未完成，原问题保留；没有执行业务操作。'


def generate_options(request, emit, llm):
    preset=static_options(request)
    if preset:emit('result',json.dumps(preset,ensure_ascii=False));return
    from datetime import datetime
    from .calendar_time import TZ
    stamp=datetime.fromtimestamp(request['reference_time'],TZ).isoformat()
    schema=dict(type='function',function=dict(name='submit_questions',description='仅提交用户需要选择的补充问题，不开始或恢复任何任务。',parameters=dict(type='object',
        properties={'question':dict(type='string'),'questions':QUESTION_SCHEMA},required=['question','questions'],additionalProperties=False)))
    messages=[dict(role='system',content='把已暂停任务的追问整理为1至3个必要问题，每题2至4个清晰选项，系统另提供自定义。只生成问题，不调用业务工具，不回答或声称执行原目标。默认本地日程不重复确认；只有明确需要用户决定的缺项才问。保持原提问时间对应的绝对日期，不因跨日重新解释明天/明晚。必须调用submit_questions。'),
        dict(role='user',content=json.dumps(dict(original_request=request['text'],asked_at=stamp,existing_question=request.get('question','')),ensure_ascii=False))]
    for turn in range(2):
        emit('llm_start',dict(turn=turn+1))
        started=time.monotonic()
        try:response=llm.chat(messages,tools=[schema],on_delta=lambda _:None)
        finally:emit('llm_end',dict(duration_ms=int((time.monotonic()-started)*1000)))
        from .collaboration_worker import record_usage
        from .model_catalog import require_item
        record_usage(request,response.get('usage') or {},require_item(request['model_id'])['model'],emit)
        message=response['message'];calls=message.get('tool_calls') or []
        try:
            text=calls[0]['function']['arguments'] if len(calls)==1 and calls[0]['function']['name']=='submit_questions' else message.get('content','')
            value=options_decision(text);emit('result',json.dumps(value,ensure_ascii=False));return
        except (ValueError,TypeError,KeyError,IndexError,AttributeError) as exc:
            messages.append(dict(role='user',content='上次选项未通过校验：'+str(exc)[:160]+'。请只提交submit_questions：question为说明，questions为数组；每题id/question/options，每项选项id/label/value，id仅英文字母数字下划线和连字符，不重复；不要包含自定义选项，系统会提供。不得增加其他字段。原任务仍暂停，不开始任务。'))
    from .collaboration_worker import PublicFailure
    raise PublicFailure('生成选项未取得合法问题，原问题保留，未执行任何业务操作。')
