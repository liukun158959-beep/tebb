"""Bounded declarative report figures. No model code or arbitrary file access."""
import base64
from datetime import datetime,timezone
import hashlib
import io
import json
import math
import re
from pathlib import Path
import textwrap
import uuid

from .paths import data_root

FENCE=re.compile(r'```tebb-chart\s*\n([\s\S]*?)\n```')


class ChartChangeError(RuntimeError):
    def __init__(self,stage,ident,confirmed,spec):
        super().__init__(f'图表在{stage}阶段未完成，已有文件阶段为{confirmed}；正文与数据保留，不能声称图表已交付。')
        self.details=dict(stage=stage,chart_id=ident,confirmed_formats=confirmed,data=spec)
SPEC=dict(type='function',function=dict(name='build_chart',description='从结构化数据生成报告静态图。bar/line须有单位、可比口径与已读正文来源编号；timeline的日期须有来源；flow是关系示意，不得用主观打分制造排名。最多20条数据、4系列、8流程节点。只提交数据，不提供代码。返回的markdown必须原样放入报告，读者可展开数据及来源。',parameters=dict(type='object',properties={
    'type':{'type':'string','enum':['bar','line','timeline','flow']},'title':{'type':'string'},'unit':{'type':'string'},'x_label':{'type':'string'},
    'comparability':{'type':'string','description':'数值指标的评测/统计口径；不同口径不得同轴排名'},'source_ids':{'type':'array','items':{'type':'string'}},
    'labels':{'type':'array','items':{'type':'string'}},'series':{'type':'array','items':{'type':'object','properties':{'name':{'type':'string'},'values':{'type':'array','items':{'type':['number','null']}}},'required':['name','values'],'additionalProperties':False}},
    'items':{'type':'array','items':{'type':'object','properties':{'label':{'type':'string'},'start':{'type':'string'},'end':{'type':'string'}},'required':['label','start'],'additionalProperties':False}},
    'nodes':{'type':'array','items':{'type':'string'}},'edges':{'type':'array','items':{'type':'array','items':{'type':'integer'},'minItems':2,'maxItems':2}},
    'calculation':{'type':'string','description':'派生数值的可核对计算方法，原始值也保留在数据说明中'}},required=['type','title','source_ids'],additionalProperties=False)))


def _text(value,limit=120):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('图表文本缺失或超过长度限制。')
    return value.strip()


def validate(spec,sources):
    from .web_sources import source_catalog
    if not isinstance(spec,dict) or set(spec)-set(SPEC['function']['parameters']['properties']):raise ValueError('图表结构或字段无效。')
    kind=spec.get('type');_text(spec.get('title'))
    if kind not in {'bar','line','timeline','flow'}:raise ValueError('不支持该图表类型。')
    ids=spec.get('source_ids')
    if not isinstance(ids,list) or len(ids)>12 or any(not isinstance(i,str) for i in ids):raise ValueError('来源编号无效。')
    catalog={s['id']:s for s in source_catalog(sources)}
    if any(i not in catalog or catalog[i].get('level')!='body' for i in ids):raise ValueError('数值来源须为本任务实际读取的正文，不接受猜测编号或仅搜索摘要。')
    if kind!='flow' and not ids:raise ValueError('数值图与时间线必须提供已读正文来源。')
    if 'calculation' in spec:_text(spec['calculation'],1000)
    if kind in {'bar','line'}:
        _text(spec.get('unit'),50);_text(spec.get('comparability'),600)
        labels=spec.get('labels');series=spec.get('series')
        if not isinstance(labels,list) or not 1<=len(labels)<=20:raise ValueError('图表需要1至20个不重复标签。')
        for label in labels:_text(label,60)
        if len(set(labels))!=len(labels):raise ValueError('图表标签不能重复。')
        if not isinstance(series,list) or not 1<=len(series)<=4:raise ValueError('最多4个数据系列。')
        for row in series:
            if not isinstance(row,dict) or set(row)!={'name','values'}:raise ValueError('数据系列无效。')
            _text(row.get('name'),60);values=row.get('values')
            if not isinstance(values,list) or len(values)!=len(labels) or not any(v is not None for v in values):raise ValueError('数据数量须与标签一致；缺值保留为空。')
            if any(v is not None and (type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1e15) for v in values):raise ValueError('数据须为有限数值，不能用布尔值或文本冒充。')
    elif kind=='timeline':
        items=spec.get('items')
        if not isinstance(items,list) or not 1<=len(items)<=15:raise ValueError('时间线需要1至15项。')
        for row in items:
            _text(row['label'],80)
            start=datetime.fromisoformat(_text(row['start'],40));end=datetime.fromisoformat(row.get('end',row['start']))
            if end<start:raise ValueError('结束时间不能早于开始时间。')
    else:
        from graphlib import TopologicalSorter
        nodes=spec.get('nodes');edges=spec.get('edges',[])
        if not isinstance(nodes,list) or not 1<=len(nodes)<=8:raise ValueError('流程图需要1至8个节点。')
        for node in nodes:_text(node,80)
        if not isinstance(edges,list) or len(edges)>16:raise ValueError('最多16条流程连接。')
        graph={i:set() for i in range(len(nodes))}
        for edge in edges:
            if not isinstance(edge,list) or len(edge)!=2 or any(type(i) is not int or not 0<=i<len(nodes) for i in edge):raise ValueError('流程连接无效。')
            graph[edge[1]].add(edge[0])
        list(TopologicalSorter(graph).static_order())
    return [dict(id=i,title=catalog[i].get('title',catalog[i]['url']),url=catalog[i]['url'],truncated=bool(catalog[i].get('truncated')),
        **{key:catalog[i].get(key) for key in ('read_ranges','snapshot_id','sha256','fetched_at')}) for i in ids]


def _render(spec):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import rc_context
    from matplotlib.font_manager import FontProperties
    font=next((Path(p) for p in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simhei.ttf'] if Path(p).exists()),None)
    settings={'text.usetex':False,'text.parse_math':False,'font.size':11,'axes.unicode_minus':False}
    if font:settings['font.family']=FontProperties(fname=font).get_name()
    kind=spec['type'];height=max(4,min(10,.45*len(spec.get('labels',spec.get('items',[])))+2))
    with rc_context(settings):
        fig=Figure(figsize=(10,height),layout='constrained');FigureCanvasAgg(fig);ax=fig.subplots()
        if kind in {'bar','line'}:
            labels=spec['labels'];series=spec['series'];positions=list(range(len(labels)))
            for index,row in enumerate(series):
                values=[float('nan') if v is None else v for v in row['values']]
                if kind=='bar':ax.barh([p+index*.8/len(series) for p in positions],values,height=.8/len(series),label=row['name'])
                else:ax.plot(positions,values,marker='o',label=row['name'])
            if kind=='bar':ax.set_yticks([p+(len(series)-1)*.4/len(series) for p in positions],labels=[textwrap.fill(s,18) for s in labels]);ax.invert_yaxis();ax.set_xlabel(spec['unit']);ax.set_axisbelow(True);ax.grid(axis='x',alpha=.2)
            else:ax.set_xticks(positions,labels=[textwrap.fill(s,10) for s in labels],rotation=30 if len(labels)>8 else 0);ax.set_ylabel(spec['unit']);ax.set_xlabel(spec.get('x_label',''));ax.grid(alpha=.2)
            if len(series)>1:ax.legend()
        elif kind=='timeline':
            import matplotlib.dates as dates
            items=spec['items']
            for i,row in enumerate(items):
                start=dates.date2num(datetime.fromisoformat(row['start']));end=dates.date2num(datetime.fromisoformat(row.get('end',row['start'])))
                if end>start:ax.barh(i,end-start,left=start,height=.5)
                else:ax.scatter(start,i,s=50)
            ax.set_yticks(range(len(items)),labels=[textwrap.fill(row['label'],22) for row in items]);ax.invert_yaxis();ax.xaxis_date();ax.xaxis.set_major_formatter(dates.ConciseDateFormatter(ax.xaxis.get_major_locator(),tz=timezone.utc));ax.set_xlabel('日期 / 时间（UTC）');ax.grid(axis='x',alpha=.2)
        else:
            from graphlib import TopologicalSorter
            nodes=spec['nodes'];edges=spec.get('edges',[]);graph={i:set() for i in range(len(nodes))}
            for a,b in edges:graph[b].add(a)
            depth={}
            for n in TopologicalSorter(graph).static_order():depth[n]=max((depth[p]+1 for p in graph[n]),default=0)
            layers={}
            for n,d in depth.items():layers.setdefault(d,[]).append(n)
            positions={n:(d,len(layer)/2-i) for d,layer in layers.items() for i,n in enumerate(layer)}
            for a,b in edges:ax.annotate('',xy=positions[b],xytext=positions[a],arrowprops=dict(arrowstyle='->',shrinkA=25,shrinkB=25,color='#667b74'))
            for n,(x,y) in positions.items():ax.text(x,y,textwrap.fill(nodes[n],12),ha='center',va='center',bbox=dict(boxstyle='round,pad=.7',facecolor='#edf3f0',edgecolor='#b8c9c1'))
            ax.set_xlim(-.6,max(depth.values())+.6);ys=[y for x,y in positions.values()];ax.set_ylim(min(ys)-.8,max(ys)+.8);ax.axis('off')
        ax.set_title(spec['title']+(' · 示意' if kind=='flow' else ''),pad=18)
        out={}
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=130,metadata={'Creator':'tebb'} if ext=='svg' else None);out[ext]=stream.getvalue()
        fig.clear();return out


def folder():return data_root()/'memory'/'report_charts'


def valid_id(ident):
    if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('图表标识无效。')
    return ident


def create(spec,sources,run_id):
    references=validate(spec,sources)
    valid_id(run_id)
    from .resource_lock import ResourceLock
    from .video import atomic_write
    root=folder();root.mkdir(parents=True,exist_ok=True)
    limit=root/'limits'/(run_id+'.json')
    with ResourceLock('report-charts-'+run_id):
        total=json.loads(limit.read_text('utf-8'))['attempts'] if limit.exists() else 0
        if total>=4:raise ValueError('同一主任务链最多生成4张图表；失败渲染也计入预算。')
        limit.parent.mkdir(parents=True,exist_ok=True);atomic_write(limit,dict(attempts=total+1))
    ident=uuid.uuid4().hex;stage='rendering';confirmed=[]
    try:
        images=_render(spec)
        for ext,raw in images.items():
            stage='save_'+ext
            if len(raw)>2_000_000:raise ValueError('图表超过大小限制，未登记成果。')
            path=root/(ident+'.'+ext);tmp=path.with_suffix('.'+ext+'.tmp');tmp.write_bytes(raw);tmp.replace(path);confirmed.append(ext)
        stage='registration'
        metadata=dict(id=ident,run_id=run_id,spec=spec,sources=references,sha256=hashlib.sha256(images['png']).hexdigest())
        atomic_write(root/(ident+'.json'),metadata)
    except Exception as exc:
        raise ChartChangeError(stage,ident,confirmed,spec) from exc
    return dict(ok=True,id=ident,markdown='```tebb-chart\n'+ident+'\n```',title=spec['title'],sources=references,
                note='图表及数据已登记；来源范围不等于自动证明每个数值或可比口径。请原样插入markdown。')


def load(ident):
    ident=valid_id(ident);value=json.loads((folder()/(ident+'.json')).read_text('utf-8'))
    if value['id']!=ident:raise ValueError('图表登记不一致。')
    raw=(folder()/(ident+'.png')).read_bytes()
    if len(raw)>2_000_000 or hashlib.sha256(raw).hexdigest()!=value['sha256']:raise ValueError('图表文件核对失败。')
    return value,raw


def public(ident):
    value,raw=load(ident)
    return dict(ok=True,**value,image='data:image/png;base64,'+base64.b64encode(raw).decode())


def referenced(text):return [valid_id(m[1].strip()) for m in FENCE.finditer(text)]


def validate_report(text,run_id=None,required=False):
    ids=referenced(text)
    if required and not ids:raise ValueError('本次明确要求图表，但没有已生成并引用的图表。')
    for ident in ids:
        meta,_=load(ident)
        if run_id and meta['run_id']!=run_id:raise ValueError('报告引用了不属于本任务链的图表。')


def data_markdown(value):
    spec=value['spec'];safe=lambda v:str(v).replace('|','\\|').replace('\n',' ')
    lines=['**'+safe(spec['title'])+'**']
    if spec['type'] in {'bar','line'}:
        lines+=['单位：'+safe(spec['unit'])+'；口径：'+safe(spec['comparability']), '|项目|'+'|'.join(safe(s['name']) for s in spec['series'])+'|', '|---|'+'---|'*len(spec['series'])]
        lines+=['|'+safe(label)+'|'+'|'.join('缺失' if s['values'][i] is None else str(s['values'][i]) for s in spec['series'])+'|' for i,label in enumerate(spec['labels'])]
    elif spec['type']=='timeline':lines+=['- '+safe(r['label'])+'：'+safe(r['start'])+' → '+safe(r.get('end',r['start'])) for r in spec['items']]
    else:lines+=['关系示意：'+'；'.join(safe(spec['nodes'][a])+' → '+safe(spec['nodes'][b]) for a,b in spec.get('edges',[]))]
    if spec.get('calculation'):lines+=['计算说明：'+safe(spec['calculation'])]
    lines+=['来源：'+('；'.join('['+safe(s['title'])+'](<'+s['url']+'>)'+('（正文节选）' if s['truncated'] else '') for s in value['sources']) or '关系示意，无数值结论')]
    return '\n\n'.join(lines)


def export(text,image_name=None):
    def replace(match):
        ident=valid_id(match[1].strip());meta,raw=load(ident)
        image='!['+meta['spec']['title'].replace(']','')+']('+image_name(ident,raw)+')\n\n' if image_name else ''
        return image+data_markdown(meta)
    return FENCE.sub(replace,text)
