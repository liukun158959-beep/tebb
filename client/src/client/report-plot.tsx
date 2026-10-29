import { useId, type ReactNode } from 'react';

export type ChartSpec={title:string;type:string;unit?:string;x_label?:string;comparability?:string;calculation?:string;labels?:string[];series?:{name:string;values:(number|null)[]}[];items?:{label:string;start:string;end?:string}[];nodes?:string[];edges?:[number,number][];edge_labels?:string[];node_lanes?:string[]};
export type FlowLayout={style:string;nodes:{index:number;x:number;y:number;width:number;height:number;kind:string;lines:string[]}[];depth:number[];lanes:string[]};
const number=(n:number)=>Math.abs(n)>=1e7||n!==0&&Math.abs(n)<.0001?n.toExponential(2):new Intl.NumberFormat('zh-CN',{maximumSignificantDigits:6}).format(n);
const scale=(value:number,min:number,max:number,start:number,end:number)=>start+(value-min)/(max-min||1)*(end-start);
const tone=(i:number)=>`var(--report-series-${i%4})`;

function Legend({spec}:{spec:ChartSpec}){return spec.series&&spec.series.length>1?<div className="report-legend">{spec.series.map((s,i)=><span key={i}><i style={{background:tone(i)}}/>{s.name}</span>)}</div>:null;}
function Bars({spec:s}:{spec:ChartSpec}){
 const values=s.series!.flatMap(r=>r.values.filter((v):v is number=>v!==null)),min=Math.min(0,...values),max=Math.max(0,...values),zero=scale(0,min,max,0,100);
 return <div className="report-bars"><Legend spec={s}/>{s.labels!.map((label,i)=><div className="report-bar-group" key={i}><span className="report-bar-label">{label}</span><div>{s.series!.map((r,j)=>{const v=r.values[i],x=v===null?zero:scale(v,min,max,0,100);return <div className="report-bar-row" key={j} title={`${label} · ${r.name}：${v===null?'缺失':number(v)+' '+s.unit}`}><div className="report-bar-track"><i className="report-zero" style={{left:`${zero}%`}}/>{v!==null&&v!==0&&<span className="report-bar-fill" style={{left:`${Math.min(zero,x)}%`,width:`${Math.abs(x-zero)}%`,background:tone(j)}}/>}</div><span className="report-bar-value">{v===null?'缺失':number(v)}</span></div>;})}</div></div>)}<p className="report-axis-note">{number(min)} → {number(max)} · {s.unit}</p></div>;
}
function SvgLabel({x,y,label,anchor='middle'}:{x:number;y:number;label:string;anchor?:'start'|'middle'|'end'}){
 const short=Array.from(label);return <text x={x} y={y} textAnchor={anchor}><title>{label}</title>{short.length>10?short.slice(0,9).join('')+'…':label}</text>;
}
function PlotSvg({width=640,height,children,label}:{width?:number;height:number;children:ReactNode;label:string}){return <div className="report-plot-scroll"><svg className="report-plot" role="img" aria-label={label} viewBox={`0 0 ${width} ${height}`} style={{minWidth:Math.min(width,520),maxWidth:width}}><title>{label}</title>{children}</svg></div>;}
function Lines({spec:s}:{spec:ChartSpec}){
 const height=300,left=62,right=610,top=24,bottom=232,values=s.series!.flatMap(r=>r.values.filter((v):v is number=>v!==null)),rawMin=Math.min(0,...values),rawMax=Math.max(0,...values),span=rawMax-rawMin||1,min=rawMin-(rawMin<0?span*.1:0),max=rawMax+span*.12;
 const x=(i:number)=>scale(i,0,Math.max(1,s.labels!.length-1),left,right),y=(v:number)=>scale(v,min,max,bottom,top);
 return <><Legend spec={s}/><PlotSvg height={height} label={s.title}>{Array.from({length:5},(_,i)=>{const v=min+(max-min)*i/4;return <g key={i}><path className="report-grid" d={`M${left} ${y(v)}H${right}`}/><text x={left-10} y={y(v)+4} textAnchor="end">{number(v)}</text></g>;})}{s.series!.map((r,j)=>{const segments:string[]=[];let path='';r.values.forEach((v,i)=>{if(v===null){if(path)segments.push(path);path='';}else path+=`${path?'L':'M'}${x(i)},${y(v)} `;});if(path)segments.push(path);return <g key={j} style={{color:tone(j)}}>{segments.map((d,k)=><path className="report-line" key={k} d={d}/>)}{r.values.map((v,i)=>v===null?null:<g key={i}><circle cx={x(i)} cy={y(v)} r="4" fill="currentColor"><title>{`${s.labels![i]} · ${r.name}：${number(v)} ${s.unit}`}</title></circle>{s.labels!.length<=8&&s.series!.length===1&&<text x={x(i)} y={y(v)-10} textAnchor="middle">{number(v)}</text>}</g>)}</g>;})}{s.labels!.map((label,i)=>i%(Math.ceil(s.labels!.length/6))===0||i===s.labels!.length-1?<SvgLabel key={i} x={x(i)} y={bottom+25} label={label}/>:null)}<text x={left} y={height-10}>{s.x_label||''}{s.x_label?' · ':''}{s.unit}</text></PlotSvg></>;
}
function Timeline({spec:s}:{spec:ChartSpec}){
 const utc=(value:string)=>Date.parse(value.length===10?value+'T00:00:00Z':/[zZ]|[+-]\d\d:\d\d$/.test(value)?value:value.replace(' ','T')+'Z');
 const items=s.items!.map(row=>({...row,startTime:utc(row.start),endTime:utc(row.end||row.start)}));
 const min=Math.min(...items.map(r=>r.startTime)),max=Math.max(...items.map(r=>r.endTime)),lo=min-(min===max?43200000:0),hi=max+(min===max?43200000:0),left=180,right=610,height=items.length*52+80,x=(v:number)=>scale(v,lo,hi,left,right),date=(v:number)=>new Date(v).toISOString().slice(0,max-min<86400000?16:10).replace('T',' ');
 return <PlotSvg height={height} label={s.title}>{[0,.5,1].map((p,i)=>{const v=lo+(hi-lo)*p;return <g key={i}><path className="report-grid" d={`M${x(v)} 10V${height-60}`}/><text x={x(v)} y={height-34} textAnchor={i===0?'start':i===2?'end':'middle'}>{date(v)}</text></g>;})}{items.map((r,i)=>{const y=30+i*52;return <g key={i}><SvgLabel x={left-14} y={y+4} label={r.label} anchor="end"/>{r.startTime===r.endTime?<circle cx={x(r.startTime)} cy={y} r="5" className="report-timeline-mark"/>:<rect x={x(r.startTime)} y={y-5} width={Math.max(2,x(r.endTime)-x(r.startTime))} height="10" rx="5" className="report-timeline-mark"/>}<title>{`${r.label}：${r.start}${r.end?' → '+r.end:''}`}</title></g>;})}<text x={left} y={height-8}>日期 / 时间（UTC）</text></PlotSvg>;
}
function Flow({spec:s,layout:l}:{spec:ChartSpec;layout:FlowLayout}){
 const arrow=useId().replace(/:/g,''),unit=80,nodes=l.nodes.map(r=>({...r,x:r.x*unit,y:-r.y*unit,w:r.width*unit*(r.kind==='decision'?1.2:1),h:r.height*unit*(r.kind==='decision'?1.3:1)}));
 let separation=1;
 for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++)separation=Math.max(separation,Math.min(((nodes[i].w+nodes[j].w)/2+28)/(Math.abs(nodes[i].x-nodes[j].x)||1e-8),((nodes[i].h+nodes[j].h)/2+30)/(Math.abs(nodes[i].y-nodes[j].y)||1e-8)));
 nodes.forEach(n=>{n.x*=separation;n.y*=separation;});
 const xMin=Math.min(...nodes.map(n=>n.x-n.w/2))-94,xMax=Math.max(...nodes.map(n=>n.x+n.w/2))+94,yMin=Math.min(...nodes.map(n=>n.y-n.h/2))-(l.lanes.length?65:44),yMax=Math.max(...nodes.map(n=>n.y+n.h/2))+54,width=xMax-xMin,height=yMax-yMin;
 function anchor(a:typeof nodes[number],b:{x:number;y:number}){const dx=b.x-a.x,dy=b.y-a.y,ratio=a.kind==='decision'?1/(Math.abs(dx)/(a.w/2)+Math.abs(dy)/(a.h/2)||1):Math.min(a.w/2/(Math.abs(dx)||1e-8),a.h/2/(Math.abs(dy)||1e-8));return {x:a.x+dx*ratio,y:a.y+dy*ratio};}
 return <PlotSvg width={width} height={height} label={s.title}><defs><marker id={arrow} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L9 5L0 9" fill="none" stroke="context-stroke" strokeWidth="1.6"/></marker></defs><g transform={`translate(${-xMin},${-yMin})`}>
 {l.lanes.map((lane,i)=><g key={lane}><text x={i*2.7*unit*separation} y={yMin+26} textAnchor="middle">{lane}</text><path className="report-grid" d={`M${(i*2.7-1.3)*unit*separation} ${yMin+42}V${yMax-10}`}/></g>)}
 {s.edges?.map(([a,b],i)=>{const start=nodes[a],end=nodes[b],back=l.depth[b]<=l.depth[a],reverse=s.edges?.some(([c,d])=>c===b&&d===a),bend=back?70:reverse?34:0,mx=(start.x+end.x)/2,my=(start.y+end.y)/2,dx=end.x-start.x,dy=end.y-start.y,length=Math.hypot(dx,dy)||1,control={x:mx+(-dy/length)*bend,y:my+(dx/length)*bend},p=anchor(start,bend?control:end),q=anchor(end,bend?control:start),label=s.edge_labels?.[i],lx=bend?(p.x+2*control.x+q.x)/4:(p.x+q.x)/2,ly=bend?(p.y+2*control.y+q.y)/4:(p.y+q.y)/2;return <g key={i}><path className="report-flow-edge" markerEnd={`url(#${arrow})`} d={bend?`M${p.x},${p.y}Q${control.x},${control.y} ${q.x},${q.y}`:`M${p.x},${p.y}L${q.x},${q.y}`}/>{label&&<text className="report-edge-label" x={lx+5} y={ly-7} textAnchor="middle"><title>{label}</title>{Array.from(label).length>14?Array.from(label).slice(0,13).join('')+'…':label}</text>}</g>;})}
 {nodes.map(n=><g key={n.index} className={`report-flow-node is-${n.kind}`}><title>{s.nodes?.[n.index]}</title>{n.kind==='decision'?<path d={`M${n.x-n.w/2},${n.y}L${n.x},${n.y-n.h/2}L${n.x+n.w/2},${n.y}L${n.x},${n.y+n.h/2}Z`}/>:<rect x={n.x-n.w/2} y={n.y-n.h/2} width={n.w} height={n.h} rx={n.kind==='terminal'?n.h/2:9}/>}<text x={n.x} y={n.y-(n.lines.length-1)*9} textAnchor="middle">{n.lines.map((line,i)=><tspan key={i} x={n.x} dy={i?18:5}>{line}</tspan>)}</text></g>)}
 </g></PlotSvg>;
}
export function ReportPlot({spec,layout}:{spec:ChartSpec;layout?:FlowLayout}){
 if(spec.type==='bar')return <Bars spec={spec}/>;
 if(spec.type==='line')return <Lines spec={spec}/>;
 if(spec.type==='timeline')return <Timeline spec={spec}/>;
 return layout?<Flow spec={spec} layout={layout}/>:null;
}
