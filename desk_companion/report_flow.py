"""Small bounded flow layouts on the existing static Matplotlib renderer."""
from graphlib import TopologicalSorter, CycleError
import math
import textwrap

STYLES=('auto','steps','decision','parallel','swimlane','cycle')


def graph(spec):
    result={i:set() for i in range(len(spec['nodes']))}
    for a,b in spec.get('edges',[]):result[b].add(a)
    return result


def issues(spec):
    """Collect semantic problems without guessing or editing graph relationships."""
    nodes=spec.get('nodes');edges=spec.get('edges',[]);out=[]
    if not isinstance(nodes,list) or not 1<=len(nodes)<=8 or not all(isinstance(n,str) for n in nodes):return out
    if not isinstance(edges,list):return out
    valid=[]
    for i,edge in enumerate(edges):
        if not isinstance(edge,list) or len(edge)!=2 or any(type(v) is not int for v in edge):continue
        if any(v<0 or v>=len(nodes) for v in edge):out.append(dict(path=f'/edges/{i}',message='连接索引超出实际节点范围。'));continue
        if edge[0]==edge[1]:out.append(dict(path=f'/edges/{i}',message='节点不能连接自身。'));continue
        valid.append(edge)
    style=spec.get('flow_style','auto') if isinstance(spec.get('flow_style','auto'),str) else 'auto';kinds=spec.get('node_kinds');labels=spec.get('edge_labels');lanes=spec.get('node_lanes')
    for name,value,length in [('node_kinds',kinds,len(nodes)),('node_lanes',lanes,len(nodes)),('edge_labels',labels,len(edges))]:
        if value is not None and isinstance(value,list) and len(value)!=length:out.append(dict(path='/'+name,message=f'需要{length}项，实际{len(value)}项；请逐一对应，不自动补条件或删除关系。'))
    if lanes is not None and isinstance(lanes,list) and all(isinstance(v,str) for v in lanes) and len(set(lanes))>4:out.append(dict(path='/node_lanes',message='最多4条职责泳道。'))
    if style=='swimlane' and not lanes:out.append(dict(path='/node_lanes',message='泳道流程须提供每个节点的负责人。'))
    for i in range(len(nodes)):
        decision=(isinstance(kinds,list) and len(kinds)==len(nodes) and kinds[i]=='decision') or style=='decision' and sum(a==i for a,b in valid)>1
        if not decision:continue
        outgoing=[j for j,edge in enumerate(edges) if isinstance(edge,list) and len(edge)==2 and edge[0]==i]
        if len(outgoing)<2:out.append(dict(path=f'/node_kinds/{i}',message='判断节点“'+nodes[i]+'”须至少两个出口；单出口动作用 process 类型。'))
        elif not isinstance(labels,list) or any(j>=len(labels) or not isinstance(labels[j],str) or not labels[j].strip() for j in outgoing):out.append(dict(path='/edge_labels',message='判断节点“'+nodes[i]+'”的每条分支须提供实际条件。'))
    if len(valid)==len(edges):
        try:list(TopologicalSorter(graph(spec)).static_order())
        except CycleError as exc:
            if style not in {'cycle','decision'}:out.append(dict(path='/flow_style',message='检测到反馈环，含反馈的关系请明确使用 cycle 或 decision 布局。',nodes=exc.args[1],hint='保留实际反馈关系，选择合适布局；不要为通过校验删除回边。'))
    return out


def style_for(spec):
    style=spec.get('flow_style','auto')
    if style!='auto':return style
    if spec.get('node_lanes'):return 'swimlane'
    if 'decision' in spec.get('node_kinds',[]):return 'decision'
    branches=any(sum(a==i for a,b in spec.get('edges',[]))>1 for i in range(len(spec['nodes'])))
    return 'decision' if branches and spec.get('edge_labels') else 'parallel' if branches else 'steps'


def layout(spec):
    """Theme-independent positions, shared by screen and static renderers."""
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
    elif style=='steps' and n<=4:positions={i:(depth[i]*2.6,(len(rows[depth[i]])-1)/2-rows[depth[i]].index(i)) for i in range(n)}
    else:positions={i:((rows[d].index(i)-(len(rows[d])-1)/2)*2.8,-d*gap) for i,d in depth.items()}
    kinds=spec.get('node_kinds',[]);items=[]
    for i,label in enumerate(nodes):
        x,y=positions[i];kind=kinds[i] if kinds else 'decision' if style=='decision' and sum(a==i for a,b in edges)>1 else 'terminal' if not any(a==i for a,b in edges) or not any(b==i for a,b in edges) else 'process'
        wrapped=textwrap.fill(label,10);height=max(.62,.23*(wrapped.count('\n')+1)+.2);width=2.05
        if kind=='decision':height=max(1.0,height*1.5)
        items.append(dict(index=i,x=x,y=y,width=width,height=height,kind=kind,lines=wrapped.splitlines()))
    return dict(style=style,nodes=items,depth=[depth[i] for i in range(n)],lanes=list(dict.fromkeys(spec.get('node_lanes',[]))) if style=='swimlane' else [])


def render(ax,spec):
    from matplotlib.patches import FancyBboxPatch,Polygon,FancyArrowPatch
    result=layout(spec);style=result['style'];positions={r['index']:(r['x'],r['y']) for r in result['nodes']};depth=dict(enumerate(result['depth']));edges=spec.get('edges',[]);patches={}
    for j,lane in enumerate(result['lanes']):
        ax.text(j*2.7,1.05,lane,ha='center',va='center',fontsize=12,color='#627568')
        ax.axvline(j*2.7-1.3,color='#e1e8e0',lw=.8,zorder=0)
    for row in result['nodes']:
        i,x,y,width,height,kind=(row[k] for k in ('index','x','y','width','height','kind'));wrapped='\n'.join(row['lines'])
        if kind=='decision':patch=Polygon([(x-width*.6,y),(x,y+height*.65),(x+width*.6,y),(x,y-height*.65)],closed=True)
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
