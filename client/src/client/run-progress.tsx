import { useEffect, useState } from 'react';
import { runActive, RUN_LABELS, type DemoRun, type DemoMessage } from './chat-prototype-state';
import { ChartBuildProgress } from './chart-building';

const toolNames: Record<string,string> = {web_search:'联网搜索',web_fetch:'网页读取',build_chart:'构建图表',github_repository:'GitHub 仓库',workspace_read:'读取文件',workspace_list:'浏览目录',workspace_tree:'浏览目录',workspace_write:'编辑文件',workspace_run:'运行命令',read_local_source:'读取附件',read_local_calendar:'本地日程读取',project_chat_search:'检索项目对话'};
export function hasCalendarContext(run?:DemoRun){return !!run?.events?.some(event=>/^tool_(?:start|end):\s*get_today_agenda(?:\s|$)/.test(event));}
export function progressText(event: string) {
  const match = /^(tool_start|tool_end):\s*(\S+)\s*(.*)$/.exec(event);
  if (!match) return event.replace(/^(正在调用：|调用结束：)(\S+)$/,(_,prefix,name)=>prefix+(toolNames[name]||name));
  const [,kind,tool,detail] = match;
  return `${toolNames[tool] || tool} · ${kind === 'tool_start' ? '开始' : /\b(failed|error)\b/.test(detail) ? '失败' : '结束'}${detail.trim() ? ` · ${detail.trim()}` : ''}`;
}
export function visibleChatMessage(message: DemoMessage, runs: DemoRun[]) {
  if (message.sender === 'user' || !message.run) return true;
  const run = runs.find(r => r.id === message.run);
  if(run?.parent && runs.some(parent=>parent.id===run.parent))return false;
  return true;
}

export function executionMessage(message:DemoMessage,runs:DemoRun[]){
  if(message.sender==='user'||!message.run)return message.text;
  const run=runs.find(r=>r.id===message.run);
  if(run?.kind==='route'&&runs.some(child=>child.parent===run.id))return run.chain?.answer||runs.filter(child=>child.parent===run.id).at(-1)?.answer||'';
  return run?.chain?.answer||message.text;
}

export function DraftNotice({run}:{run?:DemoRun}) {
  return run?.answer_pending && runActive({...run,state:run.chain?.state||run.state})
    ? <small role="status" className="block text-xs text-muted-foreground">上一段草稿 · 正在继续处理</small> : null;
}

export function ExecutionTree({run,runs,agents}:{run:DemoRun;runs:DemoRun[];agents:{id:string;name:string}[]}){
  const [now,setNow]=useState(Date.now());
  const active=runActive({...run,state:run.chain?.state||run.state});
  useEffect(()=>{if(!active||!run.created)return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>window.clearInterval(timer);},[active,run.created]);
  const elapsed=run.chain?.elapsed_ms??(active&&run.created?Math.max(0,now-run.created*1000):null);
  const children=run.chain?.children.map(id=>runs.find(child=>child.id===id)).filter((row):row is DemoRun=>!!row)||runs.filter(child=>child.parent===run.id);
  return <details className="tebb-run-progress"><summary>执行过程 · {run.chain?.calls??run.calls??0} 次模型调用{run.chain?` · ${run.chain.tool_calls} 次工具`:''}{elapsed!=null?` · ${Math.round(elapsed/1000)}秒（含等待）`:''}</summary>
    {!!run.phases?.length&&<ol>{run.phases.map((phase,i)=><li key={i}>{({route:'核对目标',plan:'职责分工',clarify_options:'补全选项',task:'执行／汇总'} as Record<string,string>)[phase.kind]||phase.kind} · {phase.calls} 次调用 · {Math.round((phase.ended-phase.started)*1000)}ms</li>)}</ol>}
    <RunProgress run={run}/>{children.map(child=><section key={child.id}><strong>{agents.find(a=>a.id===child.agent)?.name||child.agent} · {child.title}</strong><p>{({running:'执行中',succeeded:'已完成',cancelled:'已停止'} as Record<string,string>)[child.state]||RUN_LABELS[child.state]} · {child.calls||0} 次调用{child.finished&&child.created?` · ${Math.round(child.finished-child.created)}秒`:''}</p><RunProgress run={child}/>{(child.answer||child.error)&&<pre className="whitespace-pre-wrap">{child.error||child.answer}</pre>}</section>)}
  </details>;
}
export function RunProgress({run}: {run?: DemoRun}) {
  if (!run) return null;
  const state=run.chain?.state||run.state;
  const active = runActive({...run,state}), events = run.events || [];
  const status = state==='needs_input'?'待补充 · 尚未开始执行':active ? progressText(run.status || events.at(-1) || '正在准备…') : run.chain?.error || run.error || `已结束 · ${events.length} 条执行记录`;
  return <details className={`tebb-run-progress ${active ? 'is-running' : ''}`}>
    <summary>{active && <i className="tebb-stream-cursor" />}<span>{active && run.answer_pending ? '保留上一段草稿 · ' : ''}{status}</span></summary>
    <ol>{events.map((event,index) => <li key={index}>{progressText(event)}</li>)}</ol>
    <ChartBuildProgress builds={run.chart_builds} active={active}/>
  </details>;
}
