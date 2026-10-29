import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { backendInfo, rpc, type BackendInfo } from './api';
import { ChartColumn, ChartLine, GanttChart, Workflow, Table2, Braces, Expand } from 'lucide-react';
import { InspectorDisclosure } from './inspector-disclosure';
import { ReportPlot, type ChartSpec, type FlowLayout } from './report-plot';
import { WorkspaceDialog } from './workspace-dialog';
import './report-charts.css';

const Context=createContext<BackendInfo|null>(null);
export function ReportAssetProvider({info,children}:{info:BackendInfo|null;children:ReactNode}){return <Context.Provider value={info}>{children}</Context.Provider>;}
type Chart={ok:boolean;image:string;render_version?:number;spec:ChartSpec;flow_layout?:FlowLayout;sources:{id:string;title:string;url:string;level?:string;truncated:boolean}[]};

export function ReportChart({id,pending=false}:{id:string;pending?:boolean}){
  const info=useContext(Context),[data,setData]=useState<Chart|null>(null),[error,setError]=useState(''),[zoom,setZoom]=useState(false);
  useEffect(()=>{let alive=true;setData(null);setError('');
    if(pending)return;
    if(!/^[a-f0-9]{32}$/.test(id)){setError('图表标识无效，未读取本机文件。');return;}
    void(async()=>{try{const endpoint=info||await backendInfo();const value=await rpc<Chart>(endpoint,'get_report_chart',{chart_id:id});if(!value.ok||!value.image.startsWith('data:image/png;base64,'))throw new Error('图表载荷未确认。');if(alive)setData(value);}catch(e){if(alive)setError(String(e));}})();
    return()=>{alive=false;};},[id,pending,info?.port,info?.token,info?.scope]);
  if(pending)return <figure className="report-chart report-chart-pending" aria-busy="true"><Workflow size={22} aria-hidden="true"/><p role="status">图表内容正在生成…</p></figure>;
  if(error)return <figure className="report-chart"><p role="alert">图表暂不可读：{error}</p><code>{id}</code></figure>;
  if(!data)return <figure className="report-chart report-chart-pending" aria-busy="true"><Workflow size={22} aria-hidden="true"/><p role="status">正在读取已保存图表…</p></figure>;
  const s=data.spec;
  const Icon=({bar:ChartColumn,line:ChartLine,timeline:GanttChart,flow:Workflow})[s.type]||Workflow;
  const native=s.type!=='flow'||!!data.flow_layout;
  const plot=<ReportPlot spec={s} layout={data.flow_layout}/>;
  return <figure className="report-chart" data-presentation={native?'inline':'original'}><figcaption><span><Icon size={17}/>{s.title}</span><span className="report-caption-actions"><small>{s.type==='flow'?'关系示意':String(s.unit||'')}</small><button type="button" aria-label="放大图表" onClick={()=>setZoom(true)}><Expand size={16}/></button></span></figcaption>{native?plot:<img className="report-chart-original" src={data.image} alt={s.title}/>}
    {zoom&&<WorkspaceDialog title={s.title} onClose={()=>setZoom(false)}><div className="report-chart report-chart-expanded">{native?plot:<img className="report-chart-original" src={data.image} alt={s.title}/>}</div></WorkspaceDialog>}
    {data.sources.some(source=>source.level==='snippet')&&<small>含搜索摘要依据，结构示意未按原文核实。</small>}
    <InspectorDisclosure label={s.type==='flow'?'结构与依据':'数据与来源'} icon={<Table2 size={16}/>}><p>{String(s.unit||'')}{s.comparability?' · '+String(s.comparability):''}</p>
    {s.labels&&s.series?<div className="report-chart-table"><table><thead><tr><th>项目</th>{s.series.map((r,i)=><th key={i}>{r.name}</th>)}</tr></thead><tbody>{s.labels.map((label,i)=><tr key={i}><th>{label}</th>{s.series!.map((r,j)=><td key={j}>{r.values[i]===null?'缺失':r.values[i]}</td>)}</tr>)}</tbody></table></div>:s.items?<ul>{s.items.map((row,i)=><li key={i}>{row.label}：{row.start}{row.end?' → '+row.end:''}</li>)}</ul>:<><ul>{s.edges?.map(([a,b],i)=><li key={i}>{s.nodes?.[a]} {s.edge_labels?.[i]?`— ${s.edge_labels[i]} →`:'→'} {s.nodes?.[b]}</li>)}</ul>{s.node_lanes&&<ul>{s.nodes?.map((node,i)=><li key={i}>{node} · {s.node_lanes?.[i]}</li>)}</ul>}</>}
    {!!s.calculation&&<p>计算说明：{String(s.calculation)}</p>}<ul>{data.sources.map(source=><li key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>{source.level==='snippet'?'（搜索摘要，未读取正文）':source.truncated?'（正文节选）':''}</li>)}</ul><small>保留生成时的数据与来源；来源匹配不自动证明结论成立。</small><InspectorDisclosure label="结构化数据" icon={<Braces size={16}/>}><pre>{JSON.stringify(s,null,2)}</pre></InspectorDisclosure></InspectorDisclosure></figure>;
}
