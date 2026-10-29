"""Bounded declarative report figures. No model code or arbitrary file access."""
import base64
from datetime import datetime,timezone
import hashlib
import io
import json
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


class ChartBudgetError(ValueError):
    def __init__(self):
        super().__init__('同一主任务链最多生成4张图表；失败渲染也计入预算。')
        self.details=dict(stage='budget',repairable=False)
SPEC=dict(type='function',function=dict(name='build_chart',description='从结构化数据生成报告静态图。bar/line须有单位、可比口径与已读正文来源编号；timeline日期也须已读正文。flow是关系示意，可无来源或引用实际证据编号，摘要来源会标明未读正文，不得冒充原文验证或数值排名。每条主任务链最多4次渲染，参数校验失败不占渲染额度。最多20条数据、4系列、8流程节点。只提交数据，不提供代码。返回的markdown必须原样放入报告，读者可展开数据及来源。',parameters=dict(type='object',properties={
    'type':{'type':'string','enum':['bar','line','timeline','flow']},'title':{'type':'string'},'unit':{'type':'string'},'x_label':{'type':'string'},
    'comparability':{'type':'string','description':'数值指标的评测/统计口径；不同口径不得同轴排名'},'source_ids':{'type':'array','items':{'type':'string'}},
    'labels':{'type':'array','items':{'type':'string'}},'series':{'type':'array','items':{'type':'object','properties':{'name':{'type':'string'},'values':{'type':'array','items':{'type':['number','null']}}},'required':['name','values'],'additionalProperties':False}},
    'items':{'type':'array','items':{'type':'object','properties':{'label':{'type':'string'},'start':{'type':'string'},'end':{'type':'string'}},'required':['label','start'],'additionalProperties':False}},
    'nodes':{'type':'array','items':{'type':'string'}},'edges':{'type':'array','items':{'type':'array','items':{'type':'integer'},'minItems':2,'maxItems':2}},
    'flow_style':{'type':'string','enum':['auto','steps','decision','parallel','swimlane','cycle'],'description':'按内容选择：线性步骤、条件分支、并行汇合、职责泳道、反馈循环；auto自动布局。反馈环必须明确cycle/decision。'},
    'node_kinds':{'type':'array','items':{'type':'string','enum':['process','decision','terminal']}},
    'edge_labels':{'type':'array','items':{'type':'string'},'description':'与edges一一对应；条件分支必须写明是/否或具体条件'},
    'node_lanes':{'type':'array','items':{'type':'string'},'description':'与nodes一一对应的负责人或职责，swimlane必填'},
    'calculation':{'type':'string','description':'派生数值的可核对计算方法，原始值也保留在数据说明中'}},required=['type','title','source_ids'],additionalProperties=False)))


from .chart_schema import ChartInputError, configure, preflight
configure(SPEC['function']['parameters'])


def validate(spec,sources):
    from .web_sources import source_catalog
    catalog={s['id']:s for s in source_catalog(sources)}
    problems=preflight(spec,catalog,SPEC['function']['parameters'])
    if problems:raise ChartInputError(problems)
    return [dict(id=i,title=catalog[i].get('title',catalog[i]['url']),url=catalog[i]['url'],level=catalog[i]['level'],truncated=bool(catalog[i].get('truncated')),
        **{key:catalog[i].get(key) for key in ('read_ranges','snapshot_id','sha256','fetched_at')}) for i in spec['source_ids']]


def _render(spec):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import rc_context
    from matplotlib.font_manager import FontProperties
    font=next((Path(p) for p in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simhei.ttf'] if Path(p).exists()),None)
    settings={'text.usetex':False,'text.parse_math':False,'font.size':12,'axes.unicode_minus':False,
        'figure.facecolor':'#fafbf9','axes.facecolor':'#fafbf9','axes.edgecolor':'#dce4dc',
        'text.color':'#293a2f','axes.labelcolor':'#526a59','xtick.color':'#526a59','ytick.color':'#526a59',
        'axes.spines.top':False,'axes.spines.right':False,'grid.color':'#dde5dd','grid.linewidth':.7}
    if font:settings['font.family']=FontProperties(fname=font).get_name()
    kind=spec['type'];height=max(3.2,min(10,.45*len(spec.get('labels',spec.get('items',[])))+1.8))
    if kind=='flow':height=max(4,min(12,len(spec['nodes'])*.75))
    with rc_context(settings):
        fig=Figure(figsize=(8,height),layout='constrained');FigureCanvasAgg(fig);ax=fig.subplots()
        if kind in {'bar','line'}:
            labels=spec['labels'];series=spec['series'];positions=list(range(len(labels)))
            for index,row in enumerate(series):
                values=[float('nan') if v is None else v for v in row['values']]
                colors=['#607b66','#758baf','#ad825f','#947f9f'];color=colors[index]
                if kind=='bar':
                    bars=ax.barh([p+index*.8/len(series) for p in positions],values,height=.64/len(series),label=row['name'],color=color)
                    ax.bar_label(bars,labels=['' if v is None else f'{v:g}' for v in row['values']],padding=5,fontsize=11,color='#293a2f')
                else:
                    ax.plot(positions,values,marker='o',markersize=5,linewidth=1.7,color=color,label=row['name'])
                    if len(labels)<=8 and len(series)==1:
                        for x,y in enumerate(row['values']):
                            if y is not None:ax.annotate(f'{y:g}',(x,y),xytext=(0,9),textcoords='offset points',ha='center',fontsize=11,color='#293a2f')
            if kind=='bar':ax.set_yticks([p+(len(series)-1)*.4/len(series) for p in positions],labels=[textwrap.fill(s,18) for s in labels]);ax.invert_yaxis();ax.set_xlabel(spec['unit']);ax.set_axisbelow(True);ax.grid(axis='x',alpha=.2)
            else:ax.set_xticks(positions,labels=[textwrap.fill(s,10) for s in labels],rotation=30 if len(labels)>8 else 0);ax.set_ylabel(spec['unit']);ax.set_xlabel(spec.get('x_label',''));ax.grid(alpha=.2)
            ax.margins(x=.16,y=.18)
            ax.tick_params(length=0,pad=8)
            if len(series)>1:ax.legend(frameon=False,fontsize=11)
        elif kind=='timeline':
            import matplotlib.dates as dates
            items=spec['items']
            for i,row in enumerate(items):
                start=dates.date2num(datetime.fromisoformat(row['start']));end=dates.date2num(datetime.fromisoformat(row.get('end',row['start'])))
                if end>start:ax.barh(i,end-start,left=start,height=.38,color='#607b66')
                else:ax.scatter(start,i,s=40,color='#607b66')
            ax.set_yticks(range(len(items)),labels=[textwrap.fill(row['label'],22) for row in items]);ax.invert_yaxis();ax.xaxis_date();ax.xaxis.set_major_formatter(dates.ConciseDateFormatter(ax.xaxis.get_major_locator(),tz=timezone.utc));ax.set_xlabel('日期 / 时间（UTC）');ax.grid(axis='x',alpha=.2)
        else:
            from .report_flow import render as render_flow
            render_flow(ax,spec)
        out={}
        for ext in ('png','svg'):
            stream=io.BytesIO();fig.savefig(stream,format=ext,dpi=130,metadata={'Creator':'tebb'} if ext=='svg' else None);out[ext]=stream.getvalue()
        fig.clear();return out


def folder():return data_root()/'memory'/'report_charts'


def valid_id(ident):
    if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('图表标识无效。')
    return ident


def change_budget(run_id,change):
    """Single owner for durable rendering and draft receipts."""
    from .resource_lock import ResourceLock
    from .video import atomic_write
    valid_id(run_id);path=folder()/'limits'/(run_id+'.json')
    with ResourceLock('report-charts-'+run_id):
        value=json.loads(path.read_text('utf-8')) if path.exists() else dict(attempts=0)
        result=change(value)
        path.parent.mkdir(parents=True,exist_ok=True);atomic_write(path,value)
        return result


def create(spec,sources,run_id,*,ident=None):
    references=validate(spec,sources)
    valid_id(run_id)
    from .video import atomic_write
    root=folder();root.mkdir(parents=True,exist_ok=True)
    def reserve(value):
        if value['attempts']>=4:raise ChartBudgetError()
        value['attempts']+=1
    change_budget(run_id,reserve)
    ident=valid_id(ident) if ident is not None else uuid.uuid4().hex;stage='rendering';confirmed=[]
    try:
        images=_render(spec)
        for ext,raw in images.items():
            stage='save_'+ext
            if len(raw)>2_000_000:raise ValueError('图表超过大小限制，未登记成果。')
            path=root/(ident+'.'+ext);tmp=path.with_suffix('.'+ext+'.tmp');tmp.write_bytes(raw);tmp.replace(path);confirmed.append(ext)
        stage='registration'
        metadata=dict(id=ident,run_id=run_id,spec=spec,sources=references,render_version=2,sha256=hashlib.sha256(images['png']).hexdigest())
        atomic_write(root/(ident+'.json'),metadata)
    except Exception as exc:
        raise ChartChangeError(stage,ident,confirmed,spec) from exc
    return dict(ok=True,id=ident,markdown='```tebb-chart\n'+ident+'\n```',title=spec['title'],sources=references,
                note='图表及数据已登记；来源范围不等于自动证明每个数值或可比口径。请原样插入markdown。'+('含搜索摘要依据，未读取其正文，不能声称已按原文核实。' if any(s['level']=='snippet' for s in references) else ''))


def load(ident):
    ident=valid_id(ident);value=json.loads((folder()/(ident+'.json')).read_text('utf-8'))
    if value['id']!=ident:raise ValueError('图表登记不一致。')
    raw=(folder()/(ident+'.png')).read_bytes()
    if len(raw)>2_000_000 or hashlib.sha256(raw).hexdigest()!=value['sha256']:raise ValueError('图表文件核对失败。')
    return value,raw


def public(ident):
    value,raw=load(ident)
    extra={}
    if value['spec']['type']=='flow':
        from .report_flow import layout
        extra['flow_layout']=layout(value['spec'])
    return dict(ok=True,**value,**extra,image='data:image/png;base64,'+base64.b64encode(raw).decode())


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
    else:
        labels=spec.get('edge_labels') or ['']*len(spec.get('edges',[]))
        lanes=spec.get('node_lanes') or ['']*len(spec['nodes'])
        lines+=['关系示意：'+'；'.join(safe(spec['nodes'][a])+(' —'+safe(labels[i])+'→ ' if labels[i] else ' → ')+safe(spec['nodes'][b]) for i,(a,b) in enumerate(spec.get('edges',[])))]
        if any(lanes):lines+=['职责：'+'；'.join(safe(node)+'（'+safe(lane)+'）' for node,lane in zip(spec['nodes'],lanes))]
    if spec.get('calculation'):lines+=['计算说明：'+safe(spec['calculation'])]
    lines+=['来源：'+('；'.join('['+safe(s['title'])+'](<'+s['url']+'>)'+('（搜索摘要，未读取正文）' if s.get('level')=='snippet' else '（正文节选）' if s['truncated'] else '') for s in value['sources']) or '关系示意，无数值结论')]
    return '\n\n'.join(lines)


def export(text,image_name=None):
    def replace(match):
        ident=valid_id(match[1].strip());meta,raw=load(ident)
        image='!['+meta['spec']['title'].replace(']','')+']('+image_name(ident,raw)+')\n\n' if image_name else ''
        return image+data_markdown(meta)
    return FENCE.sub(replace,text)
