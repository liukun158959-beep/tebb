import { Workflow } from 'lucide-react';
import type { ChartDraftView } from './chat-prototype-state';
import './chart-building.css';

const stages:Record<string,string>={built:'构建完成',reused:'复用成功图表',validated:'预检通过，开始构建',preflight_failed:'预检未通过',render_failed:'渲染失败',unconfirmed:'保存未确认',exhausted:'修正次数已用完'};
export function ChartBuildProgress({builds,active}:{builds?:Record<string,ChartDraftView>;active:boolean}){
 return <>{Object.values(builds||{}).map(draft=><details key={draft.draft_id} className="tebb-chart-build">
  <summary><Workflow size={15} aria-hidden="true"/>{draft.title} · {draft.stage==='validated'&&!active?'构建结果未确认':stages[draft.stage]||draft.stage}</summary>
  <ol>{draft.history.map((entry,i)=><li key={i}>第 {entry.attempt} 次提交 · {stages[entry.stage]||entry.stage}
   {entry.issues?.map((issue,j)=><p key={j}><code>{issue.path}</code> {issue.message}{issue.hint?' '+issue.hint:''}</p>)}
   {entry.error&&<p>{entry.error}</p>}
  </li>)}</ol>
  {draft.asset_id&&<p>图表编号：<code>{draft.asset_id}</code></p>}
  <details><summary>图稿数据</summary><pre className="whitespace-pre-wrap break-words">{JSON.stringify(draft.spec,null,2)}</pre></details>
 </details>)}</>;
}
