"""Small bounded flow layouts on the existing static Matplotlib renderer."""
from graphlib import TopologicalSorter, CycleError
import math
import textwrap

STYLES=('auto','steps','decision','parallel','swimlane','cycle')


def graph(spec):
    result={i:set() for i in range(len(spec['nodes']))}
    for a,b in spec.get('edges',[]):result[b].add(a)
    return result


def validate(spec,text):
    nodes=spec.get('nodes');edges=spec.get('edges',[])
    if not isinstance(nodes,list) or not 1<=len(nodes)<=8:raise ValueError('流程图需要1至8个节点。')
    for node in nodes:text(node,80)
    if not isinstance(edges,list) or len(edges)>16:raise ValueError('最多16条流程连接。')
    seen=set()
    for edge in edges:
        if not isinstance(edge,list) or len(edge)!=2 or any(type(i) is not int or not 0<=i<len(nodes) for i in edge) or edge[0]==edge[1]:raise ValueError('流程连接无效。')
        if tuple(edge) in seen:raise ValueError('流程连接不能重复。')
        seen.add(tuple(edge))
    style=spec.get('flow_style','auto')
    if style not in STYLES:raise ValueError('流程布局无效。')
    kinds=spec.get('node_kinds')
    if kinds is not None and (not isinstance(kinds,list) or len(kinds)!=len(nodes) or any(k not in {'process','decision','terminal'} for k in kinds)):raise ValueError('节点类型须与节点一一对应。')
    labels=spec.get('edge_labels')
    if labels is not None:
        if not isinstance(labels,list) or len(labels)!=len(edges):raise ValueError('连接说明须与连接一一对应。')
        for value in labels:
            if not isinstance(value,str) or len(value)>30:raise ValueError('连接说明最多30字。')
    lanes=spec.get('node_lanes')
    if lanes is not None:
        if not isinstance(lanes,list) or len(lanes)!=len(nodes):raise ValueError('泳道须与节点一一对应。')
        for lane in lanes:text(lane,24)
        if len(set(lanes))>4:raise ValueError('最多4条泳道。')
    if style=='swimlane' and not lanes:raise ValueError('泳道流程需提供每个节点的负责人。')
    if style=='decision' or kinds and 'decision' in kinds:
        for i in range(len(nodes)):
            outgoing=[j for j,(a,b) in enumerate(edges) if a==i]
            if (kinds and kinds[i]=='decision') or style=='decision' and len(outgoing)>1:
                if len(outgoing)<2:raise ValueError('判断节点“'+nodes[i]+'”须至少两个出口；单出口动作用 process 类型。')
                if not labels or any(not labels[j].strip() for j in outgoing):raise ValueError('判断节点“'+nodes[i]+'”的每条分支必须标明条件。')
    try:list(TopologicalSorter(graph(spec)).static_order())
    except CycleError:
        if style not in {'cycle','decision'}:raise ValueError('含反馈的关系请明确使用 cycle 或 decision 布局。')


def style_for(spec):
    style=spec.get('flow_style','auto')
    if style!='auto':return style
    if spec.get('node_lanes'):return 'swimlane'
    if 'decision' in spec.get('node_kinds',[]):return 'decision'
    branches=any(sum(a==i for a,b in spec.get('edges',[]))>1 for i in range(len(spec['nodes'])))
    return 'decision' if branches and spec.get('edge_labels') else 'parallel' if branches else 'steps'


def render(ax,spec):
    from matplotlib.patches import FancyBboxPatch,Polygon,FancyArrowPatch
    nodes=spec['nodes'];edges=spec.get('edges',[]);style=style_for(spec);n=len(nodes)
    try:
        predecessors=graph(spec);depth={}
        for i in TopologicalSorter(predecessors).static_order():depth[i]=max((depth[p]+1 for p in predecessors[i]),default=0)
    except CycleError:
        from collections import deque
        roots=[i for i in range(n) if not predecessors[i]] or [0]
        depth={i:0 for i in roots};pending=deque(roots)
        while pending:
            a=pending.popleft()
            for parent,b in edges:
                if parent==a and b not in depth:depth[b]=depth[a]+1;pending.append(b)
        for i in range(n):depth.setdefault(i,max(depth.values())+1)
    rows={}
    for i,d in depth.items():rows.setdefault(d,[]).append(i)
    gap=max(1.7,max(math.ceil(len(label)/10) for label in nodes)*.3+.9)
    if style=='cycle':positions={i:(2.7*math.sin(2*math.pi*i/n),1.9*math.cos(2*math.pi*i/n)) for i in range(n)}
    elif style=='swimlane':
        lanes=list(dict.fromkeys(spec['node_lanes']));positions={i:(lanes.index(spec['node_lanes'][i])*2.7,-index*gap) for index,i in enumerate(sorted(depth,key=lambda i:(depth[i],i)))}
        for j,lane in enumerate(lanes):
            ax.text(j*2.7,1.05,lane,ha='center',va='center',fontsize=12,color='#627568')
            ax.axvline(j*2.7-1.3,color='#e1e8e0',lw=.8,zorder=0)
    elif style=='steps' and n<=4:positions={i:(depth[i]*2.6,(len(rows[depth[i]])-1)/2-rows[depth[i]].index(i)) for i in range(n)}
    else:positions={i:((rows[d].index(i)-(len(rows[d])-1)/2)*2.8,-d*gap) for i,d in depth.items()}
    kinds=spec.get('node_kinds',[]);patches={}
    for i,label in enumerate(nodes):
        x,y=positions[i];kind=kinds[i] if kinds else 'decision' if style=='decision' and sum(a==i for a,b in edges)>1 else 'terminal' if not any(a==i for a,b in edges) or not any(b==i for a,b in edges) else 'process'
        wrapped=textwrap.fill(label,10);height=max(.62,.23*(wrapped.count('\n')+1)+.2);width=2.05
        if kind=='decision':
            height=max(1.0,height*1.5);patch=Polygon([(x-width*.6,y),(x,y+height*.65),(x+width*.6,y),(x,y-height*.65)],closed=True)
        else:patch=FancyBboxPatch((x-width/2,y-height/2),width,height,boxstyle='round,pad=.08,rounding_size='+('.3' if kind=='terminal' else '.1'))
        patch.set(facecolor='#ffffff' if kind=='terminal' else '#eaf0e9',edgecolor='#d4dfd3',linewidth=.9,zorder=3);ax.add_patch(patch);patches[i]=patch
        ax.text(x,y,wrapped,ha='center',va='center',fontsize=12,color='#293a2f',zorder=4)
    for j,(a,b) in enumerate(edges):
        start,end=positions[a],positions[b];back=depth.get(b,0)<=depth.get(a,0)
        reverse=[b,a] in edges
        rad=.22 if style=='cycle' else .55 if back else 0 if reverse else .08 if start[0]!=end[0] and start[1]!=end[1] else 0
        arrow=FancyArrowPatch(start,end,patchA=patches[a],patchB=patches[b],shrinkA=5,shrinkB=5,connectionstyle=f'arc3,rad={rad}',arrowstyle='-|>',mutation_scale=11,linewidth=1.1,color='#728578',zorder=2);ax.add_patch(arrow)
        label=(spec.get('edge_labels') or ['']*len(edges))[j]
        if label:
            lx=(start[0]+end[0])/2+(.85 if back else -.15 if reverse else .12)
            ly=(start[1]+end[1])/2+(.3 if back else -.12 if reverse else .16)
            ax.text(lx,ly,textwrap.fill(label,9),ha='center',va='center',fontsize=10.5,color='#627568',bbox=dict(facecolor='#fafbf9',edgecolor='none',pad=2),zorder=5)
    xs=[x for x,y in positions.values()];ys=[y for x,y in positions.values()]
    ax.set_xlim(min(xs)-1.65,max(xs)+1.65);ax.set_ylim(min(ys)-1.1,max(ys)+1.6);ax.axis('off')
