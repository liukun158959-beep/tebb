"""Product input policy; Atlas owns model transport, streaming and compact primitives."""
import copy
import hashlib
import json

from atlas.llm import LLM
from atlas.context.compact import CompactManager

from . import model_capacity,model_tokenizer

MAX_TEXT_BYTES=2_000_000
_BODY_KEYS={'text','body','content','stdout','stderr'}
_WRITES={'write','prepare','create','start','stop','delete','update','send','post','publish','archive'}
_VIDEO={'read_video','read_video_transcript'}
_RELOADABLE_READS={'read_local_source','web_fetch','web_search','project_read','project_log',
                   'workspace_read','github_repository','project_chat_search'}
_NOTICE='部分旧工具正文在本轮材料中只保留摘录，原来源/操作编号与回执仍保留。需要原文时重新读取；不要声称本轮仍持有完整正文。'


class ContextAdmissionError(RuntimeError):
    def __init__(self,message,report):
        super().__init__(message);self.report=report


def _bytes(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')


def _text_bytes(request):
    total=len(_bytes(request.get('tools',[])))
    for message in request.get('messages',[]):
        value=message.get('content')
        if isinstance(value,str):total+=len(value.encode('utf-8'))
        elif isinstance(value,list):
            total+=sum(len(str(part.get('text','')).encode('utf-8'))for part in value if isinstance(part,dict)and part.get('type')in{'text','input_text'})
        total+=len(_bytes(message.get('tool_calls',[])))
    return total


def _body_candidates(messages):
    """Only re-readable successful old bodies; never discard calls or receipt fields."""
    tool_indices=[i for i,m in enumerate(messages)if m.get('role')=='tool']
    owners={};pending={};group=None
    for index,message in enumerate(messages):
        if message.get('role')=='assistant':
            calls=message.get('tool_calls')or[];group=index if calls else None
            pending={};duplicates=set();seen=set()
            for call in calls:
                ident=call.get('id')
                if not isinstance(ident,str):continue
                if ident in seen:duplicates.add(ident)
                seen.add(ident)
                function=call.get('function')or{}
                pending[ident]=function.get('name')if isinstance(function,dict)else None
            for ident in duplicates:pending.pop(ident,None)
        elif message.get('role')=='tool':
            ident=message.get('tool_call_id')
            owners[index]=(group,pending.pop(ident,None)if isinstance(ident,str)else None)
        else:pending={};group=None
    groups=list(dict.fromkeys(owner[0]for owner in owners.values()if owner[0]is not None))
    recent=set(groups[-2:])
    result=[]
    for index in tool_indices:
        message=messages[index]
        owner,name=owners.get(index,(None,None))
        if owner in recent or not isinstance(name,str)or name in _VIDEO or name not in _RELOADABLE_READS:continue
        try:value=json.loads(message.get('content',''))
        except (ValueError,TypeError):continue
        if not isinstance(value,dict)or value.get('ok')is not True:continue
        operation=value.get('operation')or{}
        if not isinstance(operation,dict):continue
        records=[value,operation,value.get('result')]
        if any(isinstance(record,dict)and any(record.get(key)is not None and not isinstance(record[key],str)
            for key in ('state','kind'))for record in records):continue
        if any(isinstance(record,dict)and(record.get('ok')is False or record.get('error')or record.get('outcome_unknown')
            or record.get('kind')in _WRITES or record.get('state')in{'unknown','failed','cancelled','needs_input','interrupted','timed_out','stopping'}
            or record.get('exit_code',0)!=0)for record in records):continue
        if operation.get('state')not in{None,'succeeded'}:continue
        reference=value.get('source_id')or value.get('log_id')or operation.get('id')
        if not isinstance(reference,str)or not reference:continue
        for prefix,body in [((),value),(('result',),value.get('result'))]:
            if not isinstance(body,dict)or body.get('exit_code',0)!=0:continue
            for key,text in body.items():
                if key in _BODY_KEYS and isinstance(text,str)and text:
                    result.append((len(text),index,prefix+(key,),reference))
    return sorted(result,reverse=True)[:8]


def prepare_request(request,capacity,*,counter=None):
    """Return a request copy plus inspectable decision, never edit stored history.

    The optional counter is for deterministic local tests, never a remote API option.
    """
    counter=counter or model_tokenizer.count
    before=counter(request,capacity);tokens=before.get('tokens')
    if type(tokens)is not int or tokens<0:tokens=None
    window=capacity.get('window');reserve=capacity.get('output_reserve',0)
    known=type(window)is int and window>0 and type(reserve)is int and 0<=reserve<window
    report=dict(policy_version=1,window=window if known else None,output_reserve=reserve if known else None,
        capacity_source=capacity.get('source','unknown'),tokenizer=before,input_tokens_before=tokens,input_tokens=tokens,
        input_source=before.get('source','unavailable'),text_bytes=_text_bytes(request),
        messages=len(request.get('messages',[])),tools=len(request.get('tools',[])),compacted=False,excerpts=[],
        material_sha256=hashlib.sha256(_bytes(request)).hexdigest(),decision='unchanged',admitted=True)
    def deny(reason,message):
        report.update(decision=reason,admitted=False);raise ContextAdmissionError(message,report)
    if report['text_bytes']>MAX_TEXT_BYTES:
        deny('text_limit','本轮文字材料超过2000000字节保护上限；未发送请求。请手动整理较早材料或分段处理。此上限不是模型token窗口。')
    if not known or tokens is None:
        report['decision']='unchanged_unknown_capacity'if not known else'unchanged_unknown_tokenizer'
        return request,report
    limit=window-reserve
    if tokens<=limit:return request,report
    projected=copy.deepcopy(request);messages=projected['messages']
    messages.insert(0,dict(role='system',content=_NOTICE))
    compact=CompactManager(llm=None)
    recent=[dict(role='tool',content=value.get('content',''))for value in request['messages']if value.get('role')=='tool'][-2:]
    def excerpt(text,size):
        # Keep the actual latest two results using Atlas's public/default tier.
        return compact.snip_tool_results([dict(role='tool',content=text),*recent],keep_recent=2,max_chars=size)[0]['content']
    for _,original_index,path,reference in _body_candidates(request['messages']):
        message=messages[original_index+1];value=json.loads(message['content']);body=value
        for key in path[:-1]:body=body[key]
        original=body[path[-1]];low,high=0,len(original)-1;best=None;counted=None
        # Bound local recounts; there are no extra model calls or network retries.
        for _ in range(21):
            size=(low+high)//2 if low<=high else 0
            body[path[-1]]=excerpt(original,size)
            value['context_excerpt']=dict(reference=reference,original_characters=len(original),retained_characters=size)
            message['content']=json.dumps(value,ensure_ascii=False)
            counted=counter(projected,capacity);count=counted.get('tokens')
            if type(count)is not int:deny('tokenizer_changed','整理期间真实分词不可用；未发送未经核对的材料，请重新检查分词绑定。')
            if count<=limit:best=(size,message['content'],count);low=size+1
            else:high=size-1
            if low>high:break
        if best:
            size,message['content'],count=best
            report.update(compacted=True,input_tokens=count,input_source=counted.get('source','unavailable'),decision='excerpted',
                          material_sha256=hashlib.sha256(_bytes(projected)).hexdigest())
            report['excerpts'].append(dict(message=original_index,path=list(path),reference=reference,original_characters=len(original),retained_characters=size))
            return projected,report
        body[path[-1]]=excerpt(original,0)
        value['context_excerpt']=dict(reference=reference,original_characters=len(original),retained_characters=0)
        message['content']=json.dumps(value,ensure_ascii=False)
        report['excerpts'].append(dict(message=original_index,path=list(path),reference=reference,original_characters=len(original),retained_characters=0))
    deny('protected_material_overflow',f'本轮材料与工具定义超过声明的可用输入窗口{limit} token；约束、当前职责和必要回执已保留，未发送超限请求。请手动整理或选择更大的已确认窗口。')


class ExecutionLLM(LLM):
    """Same public Atlas chat contract, with product context admission."""
    def __init__(self,*args,context_model_id='',on_context=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.context_model_id=context_model_id;self.on_context=on_context;self.last_context=None

    def chat(self,messages,tools=None,on_delta=None,*,options=None,callbacks=None):
        if self._closed:return super().chat(messages,tools,on_delta,options=options,callbacks=callbacks)
        endpoint=str(self.client.base_url).rstrip('/')+'/chat/completions'
        capacity=model_capacity.for_call(self.context_model_id,self.model,endpoint)if self.context_model_id else dict(source='unknown')
        request=dict(model=self.model,messages=messages)
        if tools:request['tools']=tools
        sampling=options.sampling if options is not None else self.sampling
        if sampling and 'reasoning_effort'in sampling:request['reasoning_effort']=sampling['reasoning_effort']
        if options is not None and options.extra_body:
            for key in ('enable_thinking','reasoning_effort'):
                if key in options.extra_body:request[key]=options.extra_body[key]
        try:prepared,report=prepare_request(request,capacity)
        except ContextAdmissionError as exc:
            self.last_context=exc.report;self._context_notice();raise
        self.last_context=report;self._context_notice()
        result=super().chat(prepared['messages'],tools,on_delta,options=options,callbacks=callbacks)
        self.last_context['provider_usage']=result.get('usage')
        return result

    def _context_notice(self):
        if self.on_context:
            try:self.on_context(copy.deepcopy(self.last_context))
            except Exception:pass


def replace_owned_message(messages,previous,content):
    """Refresh only the exact worker-owned object, never equal-looking user text."""
    if previous is not None:messages[:]=[value for value in messages if value is not previous]
    current=dict(role='user',content=content);messages.append(current)
    return current
