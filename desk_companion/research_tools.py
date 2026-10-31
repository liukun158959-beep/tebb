"""Product source admission and evidence, shared by both research loop drivers."""
from dataclasses import dataclass
import hashlib
import json

from .task_budgets import current
from .web_search import search_sources,format_search
from .web_sources import search_ledger,urls,url_key,is_http_url


@dataclass(frozen=True)
class ToolOutcome:
    ok:bool
    text:str


class ResearchTools:
    def __init__(self,*,sources,discovered,emit,search_limit,fetch_limit,page_reader=None,search=None,fetch=None,redact=''):
        self.sources=sources;self.discovered=discovered;self.emit=emit
        self.search_limit=search_limit;self.fetch_limit=fetch_limit;self.page_reader=page_reader
        self.search=search;self.fetch=fetch;self.redact=redact
        self.search_count=0;self.fetch_count=0;self.successes=0;self.errors=[]

    def invoke(self,name,arguments):
        if name not in {'web_search','web_fetch'}:raise ValueError('未授权的研究工具')
        query=arguments.get('query'if name=='web_search'else'url')
        if (name=='web_search'and self.search_count>=self.search_limit)or(name=='web_fetch'and self.fetch_count>=self.fetch_limit):
            self.emit('tool_end',dict(tool=name,status='failed',error='本任务该工具调用预算已用尽，未发送请求'))
            return ToolOutcome(False,'该工具调用预算已用尽，未发送请求。请使用已取得证据，不能编造未取得的内容。')
        if not isinstance(query,str)or not query.strip()or len(query)>(200 if name=='web_search'else 2048):raise ValueError('工具参数无效')
        if name=='web_fetch'and (not is_http_url(query)or url_key(query)not in self.discovered):
            self.emit('tool_end',dict(tool=name,status='failed',error='链接尚未发现，请先读取已发现资料中的链接或搜索；本次未发送请求、未占用读取额度。'))
            return ToolOutcome(False,'链接尚未从用户资料或实际来源中发现，未发送请求，也未占用网页读取额度。请先读已发现来源，使用其实际链接；不能猜URL。')
        if name=='web_search':self.search_count+=1
        else:self.fetch_count+=1
        self.emit('tool_start',dict(tool=name,read_only=True,**{'query'if name=='web_search'else'url':query}))
        if current():current().tool(True,name)
        try:
            if name=='web_search':
                payload=self.search(query)if self.search else search_sources(query,on_status=lambda status:self.emit('status',status))
                if isinstance(payload,str):
                    result=payload;ledger=[dict(url=url,title=url,text=payload[:600],level='snippet',kind='search')for url in urls(payload)]
                    payload=dict(provider='injected-search')
                else:result=format_search(payload);ledger=search_ledger(payload)
                if not ledger:raise ValueError('搜索未返回可追溯来源')
                evidence=dict(kind='search',query=query,text=result[:18000],sources=ledger,provider=payload.get('provider'),
                    fallback_reason=payload.get('fallback_reason',''),attempts=payload.get('attempts',[]),primary_failure=payload.get('primary_failure'))
            else:
                evidence=self.fetch(query)if self.fetch else self.page_reader.read(query,**{k:arguments[k]for k in('snapshot_id','offset','query','section')if k in arguments})
                if not evidence.get('text')or evidence.get('level')!='body':raise ValueError('未取得可用正文')
                ledger=[evidence];result=json.dumps(evidence,ensure_ascii=False)
            ledger=[source for source in ledger if isinstance(source,dict)and is_http_url(source.get('url'))]
            if not ledger:raise ValueError('未取得有效来源地址')
            if name=='web_search':evidence['sources']=ledger
        except Exception as exc:
            reason=(type(exc).__name__+': '+str(exc))
            if self.redact:reason=reason.replace(self.redact,'[密钥已隐藏]')
            reason=reason[:400]
            self.emit('tool_end',dict(tool=name,status='failed',error=reason,
                diagnostic=exc.diagnostic()if callable(getattr(exc,'diagnostic',None))else None,attempts=getattr(exc,'attempts',[])))
            self.errors.append(reason)
            return ToolOutcome(False,'来源工具失败，没有取得该来源。'+reason+'。可在剩余预算内重试或读取已提供的公开链接；不能据此编造结论。')
        self.emit('tool_end',dict(tool=name,status='success',provider=evidence.get('provider'),url=evidence.get('url'),truncated=evidence.get('truncated',False)))
        self.successes+=1;self.sources.extend(ledger)
        if current():
            for source in ledger:
                if source.get('level')=='body'and source.get('text'):current().progress('body',source['url'],hashlib.sha256(source['text'].encode()).hexdigest())
        self.discovered.update(url_key(s['url'])for s in ledger)
        self.discovered.update(url_key(url)for url in evidence.get('links',[])if is_http_url(url))
        self.emit('evidence',evidence)
        return ToolOutcome(True,str(result))
