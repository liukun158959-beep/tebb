import { runActive, type DemoRun, type DemoMessage } from './chat-prototype-state';

const toolNames: Record<string,string> = {web_search:'联网搜索',web_fetch:'网页读取',github_repository:'GitHub 仓库',workspace_read:'读取文件',workspace_list:'浏览目录',workspace_tree:'浏览目录',workspace_write:'编辑文件',workspace_run:'运行命令',read_local_source:'读取附件',project_chat_search:'检索项目对话'};
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
  // Covers old histories as well as newly marked handoffs, without deleting them.
  return !(run?.kind === 'route' && run.state === 'succeeded' && runs.some(child => child.parent === run.id));
}
export function RunProgress({run}: {run?: DemoRun}) {
  if (!run) return null;
  const active = runActive(run), events = run.events || [];
  const status = active ? progressText(run.status || events.at(-1) || '正在准备…') : run.error || `已结束 · ${events.length} 条执行记录`;
  return <details className={`tebb-run-progress ${active ? 'is-running' : ''}`}>
    <summary>{active && <i className="tebb-stream-cursor" />}<span>{status}</span></summary>
    <ol>{events.map((event,index) => <li key={index}>{progressText(event)}</li>)}</ol>
  </details>;
}
