import { useEffect, useState } from 'react';
import { rpc, type BackendInfo } from './api';
import { WorkspaceDialog } from './workspace-dialog';
import type { FeishuDoc } from './knowledge';
import type { NoteDoc } from './note';

export function NotebookSources({info,docs,checked,busy,debug,onCheck,onAdd,onLocal,onClose}:{info:BackendInfo|null;docs:NoteDoc[];checked:string[];busy:boolean;debug:boolean;
  onCheck:(id:string,on:boolean)=>void;onAdd:(id:string,label:string,source?:FeishuDoc)=>Promise<{ok:boolean;error:string}>;onLocal:()=>void;onClose:()=>void}){
  const [tab,setTab]=useState('existing'),[catalog,setCatalog]=useState<FeishuDoc[]>([]),[url,setUrl]=useState(''),[error,setError]=useState(''),[saving,setSaving]=useState(false),[loading,setLoading]=useState(false);
  useEffect(()=>{if(tab!=='feishu'||!info||debug)return;let live=true;setLoading(true);setError('');
    void rpc<{ok:boolean;error?:string;items?:FeishuDoc[]}>(info,'list_feishu_docs').then(page=>{if(live){if(!page.ok)throw Error(page.error||'飞书文档读取失败。');setCatalog(page.items||[]);}}).catch(e=>{if(live)setError(String(e));}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};
  },[tab,info?.port,info?.token,debug]);
  async function add(id:string,label:string,source?:FeishuDoc){if(busy||saving||!id.trim())return;setSaving(true);setError('');
    try{const result=await onAdd(id.trim(),label,source);if(!result.ok)setError(result.error);else onClose();}catch(e){setError(String(e));}finally{setSaving(false);}}
  return <WorkspaceDialog title="添加笔记来源" onClose={()=>{if(!saving)onClose();}}><div className="workspace-tabs" aria-label="来源类型">{[['existing','已有知识库'],['feishu','飞书文档']].map(([id,label])=><button type="button" key={id} aria-pressed={tab===id} disabled={saving} onClick={()=>setTab(id)}>{label}</button>)}<button type="button" disabled={saving||busy} onClick={onLocal}>本地文件 / 文件夹</button></div>
    <p className="text-xs text-muted-foreground my-3">资料存入共享知识库，在当前笔记会话勾选使用；取消勾选保留索引和原文。</p>
    {tab==='existing'?<div className="space-y-3">{!docs.length&&<p>知识库还没有资料，可以就地添加飞书或本地资料。</p>}{docs.map(doc=><label key={doc.id} className="flex gap-2"><input type="checkbox" disabled={busy||saving} checked={checked.includes(doc.id)} onChange={e=>onCheck(doc.id,e.target.checked)}/>{doc.title}</label>)}</div>:<><form className="workspace-source-form" onSubmit={e=>{e.preventDefault();void add(url,'');}}><label>飞书文档链接<input aria-label="飞书文档链接" value={url} onChange={e=>setUrl(e.target.value)} placeholder="粘贴有权读取的飞书文档链接" disabled={saving||busy}/></label><button type="submit" disabled={saving||busy||!url.trim()}>{saving?'加入中…':'加入并选中'}</button></form>{loading&&<p role="status">正在读取飞书文档…</p>}{catalog.map(doc=><div className="workspace-source-row" key={doc.url||doc.token}><span>{doc.title}</span><button type="button" disabled={busy||saving} onClick={()=>void add(doc.url||doc.token,doc.title,doc)}>加入并选中</button></div>)}</>}
    {error&&<p role="alert" className="text-destructive mt-3">{error}</p>}
  </WorkspaceDialog>;
}
