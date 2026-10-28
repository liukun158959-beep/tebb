import { useEffect, useState } from 'react';
import { rpc, type BackendInfo } from './api';

type Policy={mode:'system'|'direct'|'custom';proxy_url:string;username:string;has_password:boolean};
export function ModelNetworkSettings({info,debug=false}:{info:BackendInfo|null;debug?:boolean}){
  const [policy,setPolicy]=useState<Policy>({mode:'system',proxy_url:'',username:'',has_password:false});
  const [loaded,setLoaded]=useState(debug),[busy,setBusy]=useState(false),[password,setPassword]=useState(''),[clear,setClear]=useState(false);
  const [error,setError]=useState(''),[notice,setNotice]=useState('');
  useEffect(()=>{let live=true;setLoaded(debug);if(info&&!debug)void rpc<Policy>(info,'load_model_network').then(value=>{if(live){setPolicy(value);setLoaded(true);}}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[info?.port,info?.token,debug]);
  async function save(){
    if(!info || !loaded || busy)return;
    setBusy(true);setError('');setNotice('');
    try{const value=await rpc<Policy>(info,'save_model_network',{payload:{mode:policy.mode,proxy_url:policy.proxy_url,username:policy.username,password,clear_password:clear}});setPolicy(value);setNotice('已保存，后续模型和搜索请求使用此设置。');setClear(false);}
    catch(e){setError(String(e));}finally{setPassword('');setBusy(false);}
  }
  const disabled=!loaded||busy||debug;
  return <section className="mb-6 border border-border bg-card/80 p-5" aria-label="模型网络设置">
    <h2 className="text-lg font-semibold">模型与搜索网络</h2><p className="mt-1 text-xs leading-6 text-muted-foreground">用于模型调用及联网搜索。平台登录沿用各自的网络设置。</p>
    <fieldset disabled={disabled} className="mt-4 space-y-3"><legend className="sr-only">模型连接方式</legend>
      <label className="block text-sm">连接方式<select aria-label="模型连接方式" className="ml-3 rounded border border-border bg-background p-2" value={policy.mode} onChange={e=>{setPolicy(old=>({...old,mode:e.target.value as Policy['mode']}));setNotice('');}}><option value="system">沿用系统代理</option><option value="direct">直连（不走代理）</option><option value="custom">自定义代理</option></select></label>
      {policy.mode==='custom'&&<div className="grid gap-3 sm:grid-cols-2"><label className="text-xs sm:col-span-2">代理地址<input className="mt-1 w-full rounded border border-border bg-background p-2" aria-label="模型代理地址" placeholder="http://127.0.0.1:7890 或 socks5://127.0.0.1:1080" value={policy.proxy_url} onChange={e=>setPolicy(old=>({...old,proxy_url:e.target.value}))}/></label><label className="text-xs">账号（可选）<input className="mt-1 w-full rounded border border-border bg-background p-2" autoComplete="off" aria-label="模型代理账号" value={policy.username} onChange={e=>setPolicy(old=>({...old,username:e.target.value}))}/></label><label className="text-xs">密码（可选）<input className="mt-1 w-full rounded border border-border bg-background p-2" type="password" autoComplete="new-password" aria-label="模型代理密码" placeholder={policy.has_password?'已保存，留空保留':'未设置'} value={password} onChange={e=>{setPassword(e.target.value);setClear(false);}}/></label>{policy.has_password&&<label className="text-xs"><input type="checkbox" checked={clear} onChange={e=>setClear(e.target.checked)}/> 清除已保存的代理密码</label>}</div>}
      <p className="text-xs text-muted-foreground">直连和自定义代理会绕过系统代理，保留证书校验与已配置的信任根。更换代理地址或账号后需重新填写密码。不会修改电脑的全局代理。</p>
      <button type="button" className="desk-btn" disabled={!info || disabled || policy.mode==='custom'&&!policy.proxy_url.trim()} onClick={()=>void save()}>保存模型网络</button>
    </fieldset>{!loaded&&!error&&<p role="status" className="mt-2 text-xs">正在读取网络设置…</p>}{error&&<p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}{notice&&<p role="status" className="mt-2 text-xs text-primary">{notice}</p>}
  </section>;
}
