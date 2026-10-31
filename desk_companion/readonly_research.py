"""Atlas loop driver only; task, sources, permissions and acceptance stay in tebb."""
import json
import os
import uuid

from .research_tools import ToolOutcome

READ_TOOLS=frozenset({'web_search','web_fetch','read_local_source','project_chat_search'})


def eligible_child(request):
    if os.name!='nt':return False
    role=request.get('role')or{};permissions=role.get('tools')or[]
    return bool(request.get('kind')=='task'and request.get('parent_run')and request.get('stage')=='collect'
        and request.get('dependencies')==[]and role.get('id')!='tebb'and isinstance(permissions,list)
        and permissions and set(permissions)<={'联网搜索','网页读取'})


class QuietRenderer:
    def print(self,*args,**kwargs):pass
    rule=markdown=panel=table=print


class ReadonlyLoop:
    def __init__(self,*,model_name,schemas,messages,prepare,chat,invoke,on_tools=None):
        if not isinstance(model_name,str)or not model_name.strip():raise ValueError('Atlas观测只接受模型名称。')
        from atlas import Agent,Toolkit
        from atlas.journal import InMemoryJournal
        self.prepare=prepare;self.chat=chat;self.invoke=invoke;self.pending=[];self.last_message=None;self.on_tools=on_tools
        self.names=set()
        toolkit=Toolkit()
        for schema in schemas:
            spec=schema['function'];name=spec['name']
            if name not in READ_TOOLS or schema.get('read_only')is not True:raise ValueError('只读研究工具不在允许范围。')
            self.names.add(name)
            toolkit.register(name=name,description=spec['description'],parameters=spec['parameters'],
                func=lambda args,name=name:self._invoke(name,args),isReadOnly=True,retry_max=0)
        class UncachedJournal(InMemoryJournal):
            def cache_get(self,key):return None
            def cache_set(self,key,value):pass
        self.journal=UncachedJournal(save_full_payload=False)
        outer=self
        class Model:
            model=model_name
            def chat(self,messages,tools=None,on_delta=None):
                schemas=outer.prepare()
                permitted={schema['function']['name']for schema in schemas}
                if not permitted<=outer.names:raise ValueError('运行中不能增加研究权限。')
                outer.available=permitted
                actual=[{key:value for key,value in schema.items()if key in {'type','function'}}for schema in schemas]
                result=outer.chat(outer.agent.memory.messages,tools=actual or None,on_delta=on_delta)
                if not isinstance(result,dict)or not isinstance(result.get('message'),dict):raise ValueError('模型未返回有效消息。')
                result={**result,'message':{**result['message'],'role':'assistant'}}
                outer.last_message=result['message']
                outer.pending=[]
                for call in result.get('message',{}).get('tool_calls')or[]:
                    try:
                        function=call['function'];arguments=function.get('arguments')or{}
                        if isinstance(arguments,str):arguments=json.loads(arguments)
                        if isinstance(arguments,dict):outer.pending.append((function['name'],arguments,call['id']))
                    except (ValueError,TypeError,KeyError):continue
                if result['message'].get('tool_calls')and outer.on_tools:outer.on_tools()
                return result
        self.agent=Agent(llm=Model(),tools=toolkit,system_prompt=None,journal=self.journal,
            renderer=QuietRenderer(),auto_inject_spawn_tool=False,auto_default_registry=False,compact_threshold=None)
        self.agent.memory.load_runtime_messages(messages)
        self.messages=self.agent.memory.runtime_messages
        self.available=set()

    def _invoke(self,name,arguments):
        if name not in self.available:raise ValueError('该工具本轮未获准，未执行。')
        selected=next((i for i,row in enumerate(self.pending)if row[0]==name and row[1]==arguments),None)
        if selected is None:raise ValueError('缺少原模型调用身份，未执行。')
        _,_,ident=self.pending.pop(selected)
        result=self.invoke(name,arguments,ident)
        if not isinstance(result,ToolOutcome):raise TypeError('研究工具未提供明确结果。')
        if not result.ok:raise ValueError(result.text)
        return result.text

    def run(self,call_limit):
        from atlas.core.terminator import MaxSteps
        if type(call_limit)is not int or call_limit<2:raise ValueError('缺少候选与独立审阅额度。')
        # Pinned Atlas stops at >= after increment: leave one request for review.
        terminator=MaxSteps(call_limit)
        self.agent.terminator=terminator;self.agent.runtime.terminator=terminator
        return self.agent.run_detailed('继续当前获准研究职责，按真实材料交付。',run_id=uuid.uuid4().hex)
