import { useEffect, useRef, useState, type ReactNode } from 'react';
import { rpc, type BackendInfo } from './api';
import './context-debug.css';

type Call={id:string;created:number;model:string;state:string;run_id:string;parent?:string;agent:string;phase:string};
type Budget={input_tokens:number;input_source:string;window:number|null;output_reserve:number;remaining:number|null;over_by:number;media_estimate:boolean;capacity_source:string;draft_reference?:{input_tokens:number;note:string}};
type Inspection={ok:boolean;items:Call[];has_more:boolean;selected:string;detail:null|{request:Record<string,unknown>;request_raw:string;call:{state:string};meta?:Record<string,unknown>};budget:Budget|null;model_id:string;agents:{id:string;name:string;memory:string}[];recording:boolean;note:string};
const count=(value:number)=>value.toLocaleString();

export function ContextDebug({info,conversation,draft='',children}:{info?:BackendInfo|null;conversation:string;draft?:string;children?:ReactNode}){
  const [open,setOpen]=useState(false),[tab,setTab]=useState('request'),[data,setData]=useState<Inspection|null>(null);
  const [calls,setCalls]=useState<Call[]>([]),[more,setMore]=useState(false),[selected,setSelected]=useState('');
  const [loading,setLoading]=useState(false),[error,setError]=useState(''),[config,setConfig]=useState(false),[windowSize,setWindowSize]=useState(''),[reserve,setReserve]=useState('0');
  const [facts,setFacts]=useState<{text:string}[]|null>(null),[saving,setSaving]=useState(false);
  const latest=useRef(0),draftRef=useRef(draft);draftRef.current=draft;
  async function read(call_id=selected,before?:number){
    if(!info)return;const seq=++latest.current;setLoading(true);setError('');
    try{
      const next=await rpc<Inspection>(info,'load_context_debug',{conversation,call_id,before,draft:draftRef.current});
      if(seq!==latest.current)return;
      if(!next.ok)throw new Error('上下文读取未确认。');
      setCalls(old=>Array.from(new Map([...old,...next.items].map(c=>[c.id,c])).values()).sort((a,b)=>b.created-a.created));if(before||!calls.length)setMore(next.has_more);
      if(!before){setData(next);setWindowSize(String(next.budget?.window||''));setReserve(String(next.budget?.output_reserve||0));}
    }catch(e){if(seq===latest.current)setError(String(e));}
    finally{if(seq===latest.current)setLoading(false);}
  }
  useEffect(()=>{
    if(!open||!info||info.scope==='collaboration')return;
    void read();const timer=window.setInterval(()=>void read(),4000);
    return()=>{++latest.current;window.clearInterval(timer);};
  },[open,info,conversation,selected]);
  const budget=data?.budget,request=data?.detail?.request;
  const percent=budget?.window?Math.min(100,100*budget.input_tokens/budget.window):0;
  const reserved=budget?.window?Math.min(100-percent,100*budget.output_reserve/budget.window):0;
  async function save(){
    if(!info||!data?.model_id||saving)return;setSaving(true);setError('');
    try{await rpc(info,'save_context_capacity',{model_id:data.model_id,window:windowSize===''?null:Number(windowSize),output_reserve:Number(reserve)});setConfig(false);await read();}
    catch(e){setError(String(e)+'；保存结果请重新读取核对，不自动重发。');}finally{setSaving(false);}
  }
  async function legacy(){
    if(!info)return;try{const result=await rpc<{ok?:boolean;error?:string;facts:{text:string}[]}>(info,'load_memory');if(result.ok===false)throw new Error(result.error||'旧助手事实读取失败。');setFacts(result.facts||[]);}catch(e){setError(String(e));}
  }
  return <details className="context-debug" open={open} onToggle={e=>setOpen(e.currentTarget.open)}><summary>参数与上下文{budget&&<span> · {count(budget.input_tokens)} tokens{budget.input_source==='estimate'?'（估算）':''}</span>}</summary>
    <div className="context-debug-body">
    {(!info||info.scope==='collaboration')?<p>实际请求仅在本机客户端查看；此处未读取调用记录。</p>:<>
      <div className="context-debug-row"><label>模型调用<select aria-label="选择模型调用" value={selected||data?.selected||''} onChange={e=>{setSelected(e.target.value);setData(null);setConfig(false);}}><option value="">最新主调用</option>{calls.map(c=><option key={c.id} value={c.id}>{c.parent?'子伙伴':'主任务'} · {c.agent} · {c.phase} · {new Date(c.created*1000).toLocaleTimeString()} · {c.model}</option>)}</select></label><button type="button" onClick={()=>void read()} disabled={loading}>刷新</button>{more&&<button type="button" disabled={loading} onClick={()=>void read(selected,calls[calls.length-1]?.created)}>更早调用</button>}</div>
      {loading&&<p role="status">正在读取本机记录…</p>}{error&&<p role="alert">{error}</p>}
      {budget?<section aria-label="上下文容量"><div className="context-debug-row"><strong>{budget.window?`剩余参考空间 ${count(budget.remaining||0)} tokens`:'模型窗口容量待配置'}</strong><button type="button" onClick={()=>setConfig(!config)}>配置模型容量</button></div>
        <div className="context-meter" role="progressbar" aria-label="上下文窗口占用" aria-valuemin={0} aria-valuemax={100} aria-valuenow={budget.window?Math.round(percent+reserved):undefined}><span className="context-meter-input" style={{width:`${percent}%`}}/><span className="context-meter-reserved" style={{width:`${reserved}%`}}/></div>
        <p className="context-debug-muted">输入 {count(budget.input_tokens)}（{budget.input_source==='provider'?'服务回报':'字符估算'}） · 输出预留 {count(budget.output_reserve)} · 总量 {budget.window?count(budget.window):'未知'}{budget.over_by>0?` · 超出 ${count(budget.over_by)}`:''}</p>
        <p className="context-debug-muted">窗口属于所选的一次模型调用，各伙伴不累计共用。{budget.capacity_source==='captured_config'?'容量依据调用时保存的用户配置。':'容量依据当前匹配的模型配置。'}输出预留仅用于提示，不改写模型 API 参数。{budget.media_estimate?'含非文本内容，估算未覆盖媒体成本。':''}</p>
        {budget.draft_reference&&<p className="context-debug-muted">下一次参考输入约 {count(budget.draft_reference.input_tokens)} tokens。{budget.draft_reference.note}</p>}
        {config&&<div className="context-debug-row" onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();e.stopPropagation();void save();}}}><label>窗口总 tokens<input aria-label="模型窗口总量" type="number" min="1024" max="10000000" value={windowSize} placeholder="未知时留空" onChange={e=>setWindowSize(e.target.value)}/></label><label>输出预留<input aria-label="输出预留tokens" type="number" min="0" value={reserve} onChange={e=>setReserve(e.target.value)}/></label><button type="button" disabled={saving} onClick={()=>void save()}>保存容量配置</button><small>已记录调用的容量快照保持原值，新配置用于后续调用。</small></div>}
      </section>:<p>{data?.recording===false?'调用记录已暂停，不能还原未记录的请求。':data?'当前会话尚无可关联的调用记录。':'展开后读取真实模型请求。'}</p>}
      <nav aria-label="上下文调试分类">{[['request','实际请求'],['parameters','调用参数'],['memory','伙伴记忆']].map(([id,label])=><button type="button" key={id} aria-pressed={tab===id} onClick={()=>setTab(id)}>{label}</button>)}</nav>
      {tab==='request'&&request&&<><p className="context-debug-muted">已发送快照 · {data?.detail?.call.state} · 折叠仅影响显示，正文不截断；其中可能包含你在对话中填写的私人信息。</p>{Array.isArray(request.messages)&&request.messages.map((message,index)=><details key={index}><summary>{index+1}. {String((message as {role?:string}).role||'message')}</summary><pre>{JSON.stringify(message,null,2)}</pre></details>)}<details><summary>工具定义</summary><pre>{JSON.stringify(request.tools||[],null,2)}</pre></details><details><summary>完整请求 JSON</summary><pre>{data?.detail?.request_raw}</pre></details></>}
      {tab==='parameters'&&<>{request&&<pre>{JSON.stringify(Object.fromEntries(Object.entries(request).filter(([key])=>!['messages','tools'].includes(key))),null,2)}</pre>}{children&&<details><summary>新任务参数与手动整理</summary>{children}</details>}<small>以上为本次实际有效参数。修改新任务参数不会改写历史调用。</small></>}
      {tab==='memory'&&<>{data?.agents.map(a=><section key={a.id}><strong>{a.name}</strong><p className="context-memory-text">{a.memory||'未设置伙伴私有偏好。'}</p></section>)}<p className="context-debug-muted">这里是当前配置；本次是否注入，以实际请求为准。旧助手全局事实与伙伴记忆分别管理。</p><button type="button" onClick={()=>void legacy()}>查看旧助手全局事实（兼容）</button>{facts&&<ul>{facts.map((f,i)=><li key={i}>{f.text}</li>)}</ul>}</>}
    </>}
    </div></details>;
}
