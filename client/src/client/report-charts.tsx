import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { backendInfo, rpc, type BackendInfo } from './api';
import { ChartColumn, ChartLine, GanttChart, Workflow, Table2, Braces } from 'lucide-react';
import { InspectorDisclosure } from './inspector-disclosure';
import './report-charts.css';

const Context=createContext<BackendInfo|null>(null);
export function ReportAssetProvider({info,children}:{info:BackendInfo|null;children:ReactNode}){return <Context.Provider value={info}>{children}</Context.Provider>;}
type Chart={ok:boolean;image:string;render_version?:number;spec:Record<string,unknown>&{title:string;type:string;labels?:string[];series?:{name:string;values:(number|null)[]}[];items?:{label:string;start:string;end?:string}[];nodes?:string[];edges?:[number,number][];edge_labels?:string[];node_lanes?:string[]};sources:{id:string;title:string;url:string;truncated:boolean}[]};

export function ReportChart({id}:{id:string}){
  const info=useContext(Context),[data,setData]=useState<Chart|null>(null),[error,setError]=useState(''),[zoom,setZoom]=useState(false);
  useEffect(()=>{let alive=true;setData(null);setError('');
    if(!/^[a-f0-9]{32}$/.test(id)){setError('图表标识无效，未读取本机文件。');return;}
    void(async()=>{try{const endpoint=info||await backendInfo();const value=await rpc<Chart>(endpoint,'get_report_chart',{chart_id:id});if(!value.ok||!value.image.startsWith('data:image/png;base64,'))throw new Error('图表载荷未确认。');if(alive)setData(value);}catch(e){if(alive)setError(String(e));}})();
    return()=>{alive=false;};},[id,info]);
  if(error)return <figure className="report-chart"><p role="alert">图表暂不可读：{error}</p><code>{id}</code></figure>;
  if(!data)return <figure className="report-chart" aria-busy="true">正在读取已保存图表…</figure>;
  const s=data.spec;
  const Icon=({bar:ChartColumn,line:ChartLine,timeline:GanttChart,flow:Workflow})[s.type]||Workflow;
  return <figure className="report-chart"><figcaption className={data.render_version===2?'':'report-chart-caption-legacy'}><span><Icon size={17}/>{s.title}</span><small>{s.type==='flow'?'关系示意':String(s.unit||'')}</small></figcaption><button type="button" className="report-chart-image" aria-label={zoom?'收起图表原图':'放大图表'} onClick={()=>setZoom(!zoom)}><img src={data.image} alt={s.title} style={zoom?{minWidth:900}:undefined}/></button>
    <InspectorDisclosure label={s.type==='flow'?'结构与依据':'数据与来源'} icon={<Table2 size={16}/>}><p>{String(s.unit||'')}{s.comparability?' · '+String(s.comparability):''}</p>
    {s.labels&&s.series?<div className="report-chart-table"><table><thead><tr><th>项目</th>{s.series.map((r,i)=><th key={i}>{r.name}</th>)}</tr></thead><tbody>{s.labels.map((label,i)=><tr key={i}><th>{label}</th>{s.series!.map((r,j)=><td key={j}>{r.values[i]===null?'缺失':r.values[i]}</td>)}</tr>)}</tbody></table></div>:s.items?<ul>{s.items.map((row,i)=><li key={i}>{row.label}：{row.start}{row.end?' → '+row.end:''}</li>)}</ul>:<><ul>{s.edges?.map(([a,b],i)=><li key={i}>{s.nodes?.[a]} {s.edge_labels?.[i]?`— ${s.edge_labels[i]} →`:'→'} {s.nodes?.[b]}</li>)}</ul>{s.node_lanes&&<ul>{s.nodes?.map((node,i)=><li key={i}>{node} · {s.node_lanes?.[i]}</li>)}</ul>}</>}
    {!!s.calculation&&<p>计算说明：{String(s.calculation)}</p>}<ul>{data.sources.map(source=><li key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>{source.truncated?'（正文节选）':''}</li>)}</ul><small>保留生成时的数据与来源；来源匹配不自动证明结论成立。</small><InspectorDisclosure label="结构化数据" icon={<Braces size={16}/>}><pre>{JSON.stringify(s,null,2)}</pre></InspectorDisclosure></InspectorDisclosure></figure>;
}
