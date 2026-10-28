import { ExecutionTree } from './run-progress';
import { usePageActive } from "./page-stack";
import { useEffect, useState } from "react";
import { rpc, type BackendInfo } from "./api";
import { RUN_LABELS, runActive, type DemoState } from "./chat-prototype-state";

export function WorkspaceRuns({ info, onOpen }: { info: BackendInfo | null; onOpen: (conversation: string) => void }) {
  const pageActive = usePageActive();
  const [state, setState] = useState<DemoState | null>(null), [error, setError] = useState("");
  useEffect(() => {
    if (!pageActive || !info) return; let live = true, loading = false;
    const load = async () => { if (loading) return; loading=true;
      try { const result = await rpc<{state:DemoState}>(info,'load_collaboration'); if(live) setState(result.state); }
      catch(e) { if(live) setError(String(e)); } finally { loading=false; }
    };
    void load(); const timer=window.setInterval(()=>void load(),1200);
    return()=>{live=false;window.clearInterval(timer);};
  },[pageActive,info?.port,info?.token]);
  async function cancel(id:string) {
    if(!info) return;
    try { const result=await rpc<{state:DemoState}>(info,'collaboration_action',{action:{type:'cancel',id,request_id:crypto.randomUUID()}}); setState(result.state); }
    catch(e){setError(String(e));}
  }
  const runs=(state?.runs || []).filter(r=>!r.parent&&(r.require_execution||r.state==='needs_input'||r.chain?.children.length||!['route','chat','compress'].includes(r.kind || ''))).slice().reverse();
  return <section className="tebb-run-overview"><header><h2>执行任务</h2><p>伙伴聊天中的任务、交接、文件改动与实际命令结果。</p></header>{error && <p role="alert">{error}</p>}{!state && !error && <div className="tebb-loading-grid" aria-busy="true"><section><div className="tebb-skeleton"/><div className="tebb-skeleton"/></section></div>}{state && !runs.length && <p>当前没有执行任务。直接在聊天中描述想完成的事。</p>}
    {runs.map(run=><article className="cp-task" key={run.id}><header><strong>{run.title}</strong><span>{({running:'执行中',succeeded:'已完成',cancelled:'已停止'} as Record<string,string>)[run.chain?.state||run.state] || RUN_LABELS[run.chain?.state||run.state]}</span></header><p>{state?.agents.find(a=>a.id===run.agent)?.name} · {run.model} · {run.chain?.calls ?? run.calls ?? 0} 次模型调用（整链）</p><p>{run.clarification || run.chain?.error || run.error || run.status}</p><div><button onClick={()=>onOpen(run.conversation)}>回到对话</button>{(runActive(run)||run.state==='needs_input') && <button onClick={()=>void cancel(run.id)}>停止</button>}</div>
    <ExecutionTree run={run} runs={state!.runs} agents={state!.agents}/>{!!run.changes?.length && <details><summary>改动文件 · {run.changes.length}</summary>{run.changes.map((c,i)=><div key={i}><strong>{c.path}</strong><pre>{c.diff}</pre></div>)}</details>}
    {!!run.checks?.length && <details><summary>实际命令回执</summary>{run.checks.map((c,i)=><div key={i}><code>{c.command}</code><p>{c.state} · 退出码 {c.exit_code ?? '无'}{c.stale?' · 此后文件发生修改':''}</p><pre>{c.output}</pre></div>)}</details>}</article>)}
  </section>;
}
