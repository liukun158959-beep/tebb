"""Shared, declarative input contract and actionable chart preflight."""
from datetime import datetime
from difflib import get_close_matches
import math
from jsonschema import Draft202012Validator


class ChartInputError(ValueError):
    def __init__(self,issues,**details):
        super().__init__('图稿预检未通过：'+'；'.join(i['path']+' '+i['message'] for i in issues[:4]))
        self.details={'stage':'preflight','issues':issues,'repairable':True,**details}


def configure(schema):
    """Tool schema and service use the same shape bounds."""
    p=schema['properties']
    for name,limit in [('title',120),('unit',50),('comparability',600),('calculation',1000),('x_label',120)]:p[name].update(minLength=1,maxLength=limit,pattern=r'\S')
    p['source_ids'].update(maxItems=12,uniqueItems=True)
    for name,low,high in [('labels',1,20),('series',1,4),('items',1,15),('nodes',1,8),('edges',0,16),('node_kinds',1,8),('edge_labels',0,16),('node_lanes',1,8)]:p[name].update(minItems=low,maxItems=high)
    for name,limit in [('labels',60),('nodes',80),('node_lanes',24)]:p[name]['items'].update(minLength=1,maxLength=limit,pattern=r'\S')
    p['labels']['uniqueItems']=True;p['edges']['uniqueItems']=True
    p['edges']['items']['items'].update(minimum=0,maximum=7)
    p['edge_labels']['items'].update(maxLength=30)
    row=p['series']['items']['properties'];row['name'].update(minLength=1,maxLength=60,pattern=r'\S');row['values'].update(minItems=1,maxItems=20);row['values']['items'].update(minimum=-1e15,maximum=1e15)
    row=p['items']['items']['properties'];row['label'].update(minLength=1,maxLength=80,pattern=r'\S')
    for key in ('start','end'):row[key].update(minLength=1,maxLength=40)
    p['series']['description']='bar/line必填，values与labels数量完全相同；缺失值用null，不填0。'
    p['edge_labels']['description']='与edges数量完全相同且顺序对应；没有说明用空字符串，判断分支必须有实际条件。'


def preflight(spec,catalog,schema):
    if not isinstance(spec,dict):return [dict(path='/',message='图稿须为对象。')]
    kind=spec.get('type') if isinstance(spec.get('type'),str) else ''
    required={'bar':['unit','comparability','labels','series'],'line':['unit','comparability','labels','series'],'timeline':['items'],'flow':['nodes']}.get(kind,[])
    problems=[]
    for error in Draft202012Validator({**schema,'required':schema['required']+required}).iter_errors(spec):
        path='/'+('/'.join(str(p) for p in error.absolute_path))
        if error.validator=='additionalProperties':
            for key in sorted(set(error.instance)-set(error.schema.get('properties',{}))):
                choices=get_close_matches(key,error.schema.get('properties',{}),n=1,cutoff=.6)
                problems.append(dict(path=path.rstrip('/')+'/'+key,message='不支持的字段。',hint='是否要使用 '+choices[0]+'？请明确修正，不会自动替换。' if choices else '请使用工具已声明的字段；内容不会被静默删除。'))
        else:problems.append(dict(path=path,message=error.message))
    ids=spec.get('source_ids')
    if isinstance(ids,list) and all(isinstance(i,str) for i in ids):
        if kind in {'bar','line','timeline'} and not ids:problems.append(dict(path='/source_ids',message='数值图与时间线必须提供实际已读正文来源。'))
        for index,ident in enumerate(ids):
            if ident not in catalog:problems.append(dict(path=f'/source_ids/{index}',message='编号不在本任务实际证据中。',hint='复制当前证据清单的完整编号，不猜测。'))
            elif catalog[ident].get('level') not in ({'body','snippet'} if kind=='flow' else {'body'}):problems.append(dict(path=f'/source_ids/{index}',message='数值图与时间线要求实际已读正文，摘要只能用于明确标注的流程示意。'))
    if kind!='flow':
        for key in ('flow_style','node_kinds','node_lanes','edge_labels'):
            if key in spec:problems.append(dict(path='/'+key,message='此字段只适用于流程图。'))
    if kind in {'bar','line'} and isinstance(spec.get('labels'),list) and isinstance(spec.get('series'),list):
        for i,row in enumerate(spec['series']):
            if not isinstance(row,dict) or not isinstance(row.get('values'),list):continue
            if len(row['values'])!=len(spec['labels']):problems.append(dict(path=f'/series/{i}/values',message=f"需要{len(spec['labels'])}个数值，实际{len(row['values'])}个。"))
            if not any(v is not None for v in row['values']):problems.append(dict(path=f'/series/{i}/values',message='系列至少需要一个实际数值，不能全为缺失。'))
            for j,v in enumerate(row['values']):
                if isinstance(v,float) and not math.isfinite(v):problems.append(dict(path=f'/series/{i}/values/{j}',message='数值须有限。'))
    if kind=='timeline' and isinstance(spec.get('items'),list):
        for i,row in enumerate(spec['items']):
            if not isinstance(row,dict) or not isinstance(row.get('start'),str) or not isinstance(row.get('end',row['start']),str):continue
            try:
                start=datetime.fromisoformat(row['start']);end=datetime.fromisoformat(row.get('end',row['start']))
                if end<start:problems.append(dict(path=f'/items/{i}/end',message='结束时间不能早于开始时间。'))
            except (ValueError,TypeError):problems.append(dict(path=f'/items/{i}',message='须使用有效ISO日期/时间，起止时间使用一致的时区格式。'))
    if kind=='flow':
        from .report_flow import issues
        problems.extend(issues(spec))
    return problems[:24]
