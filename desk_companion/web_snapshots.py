"""Immutable public-page snapshots scoped to one root execution."""
import hashlib
import json
import re
import uuid

from .paths import data_root
from .web_sources import fetch_page,MAX_TEXT,url_key


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('网页快照标识无效。')
    return value


class PageReader:
    def __init__(self,scope,evidence=()):
        self.scope=identifier(scope)
        self.known={url_key(s['url']):s['snapshot_id'] for s in evidence if s.get('snapshot_id') and s.get('url')}

    def path(self,ident):
        return data_root()/'memory'/'web_snapshots'/self.scope/(identifier(ident)+'.json')

    def load(self,ident):
        path=self.path(ident)
        if not path.is_file():raise ValueError('网页快照不属于本任务或已不可用，请重新核对来源。')
        value=json.loads(path.read_text('utf-8'))
        if (value.get('scope')!=self.scope or value.get('snapshot_id')!=ident or value.get('characters')!=len(value['text'])
                or hashlib.sha256(value['text'].encode()).hexdigest()!=value['sha256']):
            raise ValueError('网页快照完整性核对失败，不能继续引用。')
        return value

    def read(self,url,snapshot_id='',offset=0,query='',section=''):
        if type(offset) is not int or offset<0:raise ValueError('正文位置须为非负整数。')
        if any(not isinstance(v,str) or len(v)>200 for v in (query,section)):raise ValueError('章节或关键词须为200字以内。')
        if query and section:raise ValueError('一次选择章节或关键词之一。')
        key=url_key(url);ident=snapshot_id or self.known.get(key,'')
        if not ident:
            ident=uuid.uuid4().hex
            def save(full):
                from .video import atomic_write
                path=self.path(ident);path.parent.mkdir(parents=True,exist_ok=True)
                atomic_write(path,{**full,'scope':self.scope,'snapshot_id':ident})
            fetch_page(url,on_body=save)
            self.known[key]=ident
        value=self.load(ident)
        if key not in {url_key(value['url']),url_key(value['requested_url'])}:raise ValueError('快照与请求来源不一致。')
        text=value['text']
        if offset>=len(text):raise ValueError('正文位置超出范围。')
        term=section or query
        if term:
            pattern=(r'^.*'+re.escape(term)+r'.*$') if section else re.escape(term)
            match=re.search(pattern,text[offset:],re.I|re.M)
            if not match:raise ValueError('快照中未找到该章节或关键词；没有取得新的正文范围。')
            offset+=max(0,match.start()-(0 if section else 300))
        end=min(len(text),offset+MAX_TEXT)
        return {k:v for k,v in value.items() if k not in {'scope','text'}}|dict(text=text[offset:end],
            read_start=offset,read_end=end,read_ranges=[[offset,end]],next_offset=end if end<len(text) else None,
            complete=offset==0 and end==len(text),truncated=offset>0 or end<len(text))
