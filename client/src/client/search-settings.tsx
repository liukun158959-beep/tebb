import { useEffect, useState } from 'react';
import { rpc, type BackendInfo } from './api';
type Settings={provider:'exa'|'model-api';fallback:string;timeout_s:number};
type Probe={ok:boolean;provider?:string;error?:string;elapsed_ms?:number;results?:{title:string;link:string}[];attempts?:{provider:string;ok:boolean;category?:string;elapsed_ms:number}[]};
export function SearchSettings({info,debug=false}:{info:BackendInfo|null;debug?:boolean}){
  const [settings,setSettings]=useState<Settings|null>(null),[choice,setChoice]=useState('exa'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState<Probe|null>(null);
  useEffect(()=>{let live=true;if(info&&!debug)void rpc<Settings>(info,'load_search_settings').then(value=>{if(live){setSettings(value);setChoice(value.provider);}}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[info?.port,info?.token,debug]);
  async function act(test:boolean){
    if(!info||busy||!settings)return;setBusy(true);setError('');setResult(null);
    try{if(test)setResult(await rpc<Probe>(info,'test_search'));else setSettings(await rpc<Settings>(info,'save_search_settings',{payload:{provider:choice}}));}
    catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <section className="mb-6 border border-border bg-card/80 p-5" aria-label="联网搜索设置"><h2 className="text-lg font-semibold">联网搜索</h2><p className="mt-1 text-xs leading-6 text-muted-foreground">独立于聊天模型，普通对话、伙伴、视频与资讯共用。默认 Exa 公开搜索，Bing RSS 备用；公开站点不接收模型密钥。沿用上方网络配置，总时限30秒，公开服务可能限流。结果仍需核对相关性和日期。</p>
    <fieldset disabled={busy||!settings||debug} className="mt-3 space-y-3"><label className="text-sm">主搜索渠道<select aria-label="主搜索渠道" className="ml-3 rounded border border-border bg-background p-2" value={choice} onChange={e=>setChoice(e.target.value)}><option value="exa">Exa 公开 MCP（推荐）</option><option value="model-api">当前模型私有 /tool/search（仅明确支持时）</option></select></label>{choice==='model-api'&&<p className="text-xs">仅向当前已保存的模型地址发送该模型密钥；聊天兼容不代表支持私有搜索。404会冷却并使用备用渠道。</p>}<div className="flex gap-2"><button className="desk-btn" onClick={()=>void act(false)}>保存搜索渠道</button><button className="desk-btn" disabled={choice!==settings?.provider} onClick={()=>void act(true)}>测试已保存渠道</button></div></fieldset>
    {busy&&<p role="status">正在处理…</p>}{error&&<p role="alert">{error}</p>}{result&&<div role="status" className="mt-3 text-xs"><p>{result.ok?`已取得来源 · ${result.provider} · ${result.elapsed_ms}ms`:result.error}</p>{result.attempts?.map((a,i)=><p key={i}>{a.provider} · {a.ok?'成功':a.category} · {a.elapsed_ms}ms</p>)}{result.results?.map(row=><p key={row.link}><a href={row.link} target="_blank" rel="noreferrer">{row.title}</a></p>)}</div>}
  </section>;
}
