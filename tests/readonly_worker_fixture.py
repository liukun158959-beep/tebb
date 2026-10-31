"""Production task/Atlas loops with bounded offline model/source blocking points."""
import importlib.abc
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

class NoProvider(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='openai'or fullname.startswith('openai.'):raise AssertionError('offline readonly fixture cannot load provider SDK')
sys.meta_path.insert(0,NoProvider())

from desk_companion import collaboration_worker
from desk_companion.task_worker import main
from desk_companion.task_budgets import current
from desk_companion.web_sources import source_catalog
import psutil

original=collaboration_worker.execute
SOURCE=dict(kind='page',level='body',url='https://example.org/read',text='Actual offline source statement.',truncated=False)

def execute(request,emit):
    case=request['fixture_case'];turns=[0]
    def block(stage):
        Path(request['fixture_marker']).write_text(json.dumps(dict(pid=os.getpid(),created=psutil.Process().create_time(),stage=stage)),encoding='utf-8')
        time.sleep(120)
    class Model:
        def chat(self,messages,tools=None,on_delta=None):
            scope=current();call,_=scope.ledger.reserve_call(scope.task,10,phase=scope.phase())
            if messages[0]['content'].startswith('反馈审阅协议'):
                rows=json.loads(messages[1]['content'])['materials'];body=next(r for r in rows if r['level']=='body')
                requirement=dict(requirement='report reflects actual source',dimension='claim_support',status='supported',
                    reason='controlled quote',evidence=[dict(source_id=body['id'],quote=SOURCE['text'])])
                message=dict(content=json.dumps(dict(requirements=[requirement])))
            else:
                turns[0]+=1
                if case=='block_model':block('model')
                if turns[0]==1:message=dict(tool_calls=[dict(id='actual-read',function=dict(name='web_fetch',arguments=json.dumps(dict(url=SOURCE['url']))))])
                else:message=dict(content=json.dumps(dict(ok=True,citation='['+source_catalog([SOURCE])[0]['id']+']')))
            usage=dict(prompt_tokens=10,completion_tokens=3);scope.ledger.settle(scope.task,call,usage)
            return dict(message=message,usage=usage)
    def fetch(url):
        if case=='block_read':block('read')
        if case=='failed_read':raise TimeoutError('controlled read failure')
        return SOURCE
    return original(request,emit,llm=Model(),fetch=fetch)

with patch('desk_companion.collaboration_worker.execute',execute),patch('desk_companion.model_catalog.require_item',
    return_value=dict(id='offline',model='offline',api_key='fixture-only',base_url='https://example.invalid/v1')),\
    patch('desk_companion.collaboration_worker.updates',return_value=[]),\
    patch('desk_companion.collaboration_worker.selected_materials',return_value={}):
    main()
