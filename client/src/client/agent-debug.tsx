import { useEffect, useRef, useState } from "react";
import { rpc, type BackendInfo } from "./api";
import { Markdown } from "./Markdown";
import { TaskBudgetView,type TaskBudget } from './task-budget';
import { PerformanceView, PerformanceStats, duration, type PerformanceSummary, type CallTiming, type TaskTiming } from './performance-view';

type Obj = Record<string, unknown>;
export type DebugCall = { id: string; created: number; updated: number; task_id: string; session: string; channel: string;
  model: string; endpoint: string; state: string; status: number; duration_ms: number; first_byte_ms: number; request_bytes: number; response_bytes?: number; error: string };
export type DebugDetail = { call: DebugCall; request: Obj; request_raw: string; response: Obj; response_raw: string; task?:Task|null;timing?:CallTiming|null;chain?:TaskTiming|null;task_budget?:TaskBudget|null };
type Task = { id: string; state: string; text: string; answer: string; error: string; task_budget?:TaskBudget|null; events?: { seq: number; ts: number; kind: string; data: unknown }[] };
const STATE: Record<string, string> = { sending: "发送中", receiving: "接收中", complete: "已返回", failed: "失败", timed_out:"超时",cancelled:"已取消", interrupted: "中断", recording_failed: "记录失败" };
const CHANNEL: Record<string, string> = { collaboration:'伙伴聊天', desktop: "桌面对话", feishu: "飞书", video: "视频", automation: "定时任务", debug: "调用解读", notebook: "笔记问答", backend: "其它调用" };
const pretty = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2) || "";
const active = (state: string) => ["sending", "receiving", "running", "queued", "cancelling"].includes(state);
const messages = (request: Obj): Obj[] => Array.isArray(request.messages) ? request.messages as Obj[] : [];

export function requestDiff(before: Obj, after: Obj) {
  const a = messages(before), b = messages(after);
  const changes = Array.from({ length: Math.max(a.length, b.length) }, (_, i) => ({ index: i, before: a[i], after: b[i] }))
    .filter(row => JSON.stringify(row.before) !== JSON.stringify(row.after));
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(k => k !== "messages" && JSON.stringify(before[k]) !== JSON.stringify(after[k]));
  return { changes, keys, beforeCount: a.length, afterCount: b.length };
}

export const DEBUG_SAMPLE: DebugDetail = {
  call: { id: "sample-call", task_id: "sample-task", session: "sample-session", channel: "desktop", model: "示例模型", created: 1791532800,
    updated: 1791532803, endpoint: "https://example.invalid/v1/chat/completions", state: "complete", status: 200, duration_ms: 3100, first_byte_ms: 420, request_bytes: 1800, error: "" },
  request: { model: "示例模型", stream: true, temperature: .5, messages: [
    { role: "system", content: "你是 tebb。按工具结果回答，不能把预测当作事实。" },
    { role: "user", content: "帮我看看今天的日程" },
    { role: "assistant", content: null, tool_calls: [{ id: "call_1", type: "function", function: { name: "get_today_agenda", arguments: "{}" } }] },
    { role: "tool", tool_call_id: "call_1", content: "14:00 项目复盘；16:30 技术分享" }],
    tools: [{ type: "function", function: { name: "get_today_agenda", description: "读取今日日程", parameters: { type: "object", properties: {} } } }] },
  request_raw: "", response: { choices: [{ message: { role: "assistant", content: "今天 14:00 项目复盘，16:30 技术分享。", reasoning_content: "根据日程工具结果整理时间。", tool_calls: [] } }], usage: { prompt_tokens: 520, completion_tokens: 42 } },
  response_raw: "data: {\"choices\":[{\"delta\":{\"content\":\"今天 14:00 项目复盘，16:30 技术分享。\"}}]}\n\ndata: [DONE]\n\n",
};

function Json({ value }: { value: unknown }) {
  return <pre className="max-h-[52vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-background/60 p-3 font-mono text-xs leading-6" tabIndex={0}>{pretty(value)}</pre>;
}

function MessageCards({ rows, empty = "没有 messages 字段，完整载荷见原始 JSON。" }: { rows: Obj[]; empty?: string }) {
  const [expanded, setExpanded] = useState<number | null>(0);
  return <div className="space-y-2">{rows.map((message, i) => <section key={i} className="rounded-lg border border-border bg-card/40">
    <button className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left text-xs" aria-expanded={expanded === i}
      onClick={() => setExpanded(expanded === i ? null : i)}>
      <span className="font-mono text-primary">{String(message.role || "message")} <span className="text-muted-foreground">#{i + 1}</span></span>
      <span className="text-muted-foreground">{pretty(message).length.toLocaleString()} 字符 · {expanded === i ? "收起" : "展开全量"}</span>
    </button>
    {expanded === i && <div className="space-y-3 px-3 pb-3">
      {typeof message.content === "string" ? <div><p className="mb-2 text-[10px] text-muted-foreground">{message.role === "tool" ? "执行结果" : "消息正文"}</p><Json value={message.content} /></div> :
        message.content != null ? <Json value={message.content} /> : <p className="text-xs text-muted-foreground">没有文本正文。</p>}
      {typeof message.reasoning_content === "string" && message.reasoning_content && <details><summary className="cursor-pointer text-xs text-primary">服务返回的 reasoning_content</summary><div className="mt-2"><Json value={message.reasoning_content} /></div></details>}
      {Array.isArray(message.tool_calls) && (message.tool_calls as Obj[]).map((tool, n) => {
        const fn = tool.function as Obj | undefined;
        let args = fn?.arguments;
        try { if (typeof args === "string") args = JSON.parse(args); } catch { /* Preserve incomplete arguments. */ }
        return <div key={n} className="rounded-lg border border-primary/25 bg-primary/5 p-3"><p className="mb-2 text-xs text-primary">工具请求 · {String(fn?.name || tool.type || "未知工具")}</p><Json value={args ?? tool} /></div>;
      })}
      <details><summary className="cursor-pointer text-xs text-muted-foreground">本消息的完整 JSON（含所有字段）</summary><div className="mt-2"><Json value={message} /></div></details>
    </div>}
  </section>)}{!rows.length && <p className="text-xs text-muted-foreground">{empty}</p>}</div>;
}

export function AgentDebugDialog({ info, onClose, debug = false }: { info: BackendInfo | null; onClose: () => void; debug?: boolean }) {
  const dialog = useRef<HTMLElement>(null);
  const [calls, setCalls] = useState<DebugCall[]>(debug ? [DEBUG_SAMPLE.call] : []);
  const [selected, select] = useState(debug ? DEBUG_SAMPLE.call.id : "");
  const [detail, setDetail] = useState<DebugDetail | null>(debug ? DEBUG_SAMPLE : null);
  const [channel, setChannel] = useState("");
  const [filter, setFilter] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [more, setMore] = useState(false);
  const [older, setOlder] = useState<DebugCall[]>([]);
  const [tab, setTab] = useState<"messages" | "raw" | "diff" | "tools" | "explain" | "performance">("messages");
  const [performance,setPerformance]=useState<PerformanceSummary|null>(null);
  const [previous, setPrevious] = useState<DebugDetail | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [explanation, setExplanation] = useState<Task | null>(null);
  const [explainId, setExplainId] = useState("");
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const olderRef = useRef(older); olderRef.current = older;

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    return () => previousFocus?.focus?.();
  }, []);
  useEffect(() => {
    if (!info || debug) return;
    let live = true, loading = false;
    async function refresh() {
      if (loading) return;
      loading = true;
      try {
        const page = await rpc<{ items: DebugCall[]; has_more: boolean; settings: { enabled: boolean };performance?:PerformanceSummary }>(info!, "list_debug_calls", { channel });
        if (!live) return;
        setCalls(page.items); if (!olderRef.current.length) setMore(page.has_more); setEnabled(page.settings.enabled);
        setPerformance(page.performance || null);
        if (!selectedRef.current && page.items[0]) select(page.items[0].id);
      } catch (err) { if (live) setError(String(err)); }
      finally { loading = false; }
    }
    void refresh(); const timer = window.setInterval(() => void refresh(), 2000);
    return () => { live = false; window.clearInterval(timer); };
  }, [info, debug, channel]);
  useEffect(() => {
    if (!info || debug || !selected) return;
    let live = true, loading = false, terminal = false;
    async function refresh() {
      if (loading || terminal) return;
      loading = true;
      try {
        const found = await rpc<DebugDetail>(info!, "get_debug_call", { call_id: selected });
        if (!live) return;
        setDetail(found);
        if ('task' in found) {setTask(found.task || null);terminal=!active(found.call.state)&&(!found.task || !active(found.task.state));}
        else if (found.call.task_id && found.call.channel!=='collaboration') {
          const page = await rpc<{ task: Task }>(info!, "get_agent_task", { task_id: found.call.task_id, focus: false });
          if (live) setTask(page.task);
          terminal = !active(found.call.state) && !active(page.task.state);
        } else { setTask(null); terminal = !active(found.call.state); }
      } catch (err) { if (live) setError(String(err)); }
      finally { loading = false; }
    }
    void refresh(); const timer = window.setInterval(() => void refresh(), 2500);
    return () => { live = false; window.clearInterval(timer); };
  }, [info, debug, selected]);
  const all = [...calls, ...older.filter(row => !calls.some(c => c.id === row.id))];
  const previousCall = detail ? all.find(c => c.id !== detail.call.id && c.created < detail.call.created && (
    detail.call.task_id ? c.task_id === detail.call.task_id : !!c.session && c.session === detail.call.session)) : undefined;
  useEffect(() => {
    setPrevious(null);
    if (!info || !previousCall || tab !== "diff") return;
    let live = true;
    void rpc<DebugDetail>(info, "get_debug_call", { call_id: previousCall.id }).then(p => { if (live) setPrevious(p); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [info, previousCall?.id, tab, selected]);
  useEffect(() => {
    if (!info || !explainId || debug) return;
    let live = true, loading = false;
    async function refresh() {
      if (loading) return; loading = true;
      try { const found = await rpc<{ task: Task }>(info!, "get_agent_task", { task_id: explainId, focus: false }); if (live) setExplanation(found.task); }
      catch (e) { if (live) setError(String(e)); } finally { loading = false; }
    }
    void refresh(); const timer = window.setInterval(() => void refresh(), 2000);
    return () => { live = false; window.clearInterval(timer); };
  }, [info, explainId, debug]);

  async function loadOlder() {
    if (!info || !all.length || pending) return;
    setPending(true);
    try { const page = await rpc<{ items: DebugCall[]; has_more: boolean }>(info, "list_debug_calls", { channel, before: all[all.length - 1].created }); setOlder([...older, ...page.items]); setMore(page.has_more); }
    catch (e) { setError(String(e)); } finally { setPending(false); }
  }
  async function explain() {
    if (!info || !detail || pending || debug || (explanation && active(explanation.state))) return;
    setPending(true); setError("");
    try { const value = await rpc<{ task_id: string }>(info, "explain_debug_call", { call_id: detail.call.id, question }); setExplainId(value.task_id); setExplanation(null); }
    catch (e) { setError(String(e)); } finally { setPending(false); }
  }
  async function recording() {
    if (!info || pending || debug) return;
    setPending(true);
    try { const r = await rpc<{ enabled: boolean }>(info, "set_debug_recording", { enabled: !enabled }); setEnabled(r.enabled); }
    catch (e) { setError(String(e)); } finally { setPending(false); }
  }
  function exportCall() {
    if (!detail) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(detail, null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = `agent-call-${detail.call.id}.json`; a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const delta = previous && detail ? requestDiff(previous.request, detail.request) : null;
  const usage = detail?.response.usage as Obj | undefined;
  const responses = (detail?.response.choices as { message?: Obj }[] | undefined)?.map(c => c.message || {}) || [];
  const tabs = { messages: "消息", performance:"耗时分析", raw: "原始载荷", diff: "请求差异", tools: "工具与时间线", explain: "协助解读" } as const;
  return <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-3 backdrop-blur-sm" data-agent-debug
    onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
    <section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="agent-debug-title"
      className="flex h-[94vh] w-full max-w-[1440px] flex-col overflow-hidden rounded-2xl border border-border bg-background text-foreground shadow-2xl"
      onKeyDown={e => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
        if (e.key !== "Tab") return;
        const nodes = [...e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input, select, textarea, a[href], [tabindex="0"]')];
        if (e.shiftKey && document.activeElement === nodes[0]) { e.preventDefault(); nodes.at(-1)?.focus(); }
        else if (!e.shiftKey && document.activeElement === nodes.at(-1)) { e.preventDefault(); nodes[0]?.focus(); }
      }}>
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div><p className="text-[10px] tracking-[.2em] text-primary">AGENT INSPECTOR</p><h1 id="agent-debug-title" className="mt-1 text-lg font-semibold">Agent 调试</h1></div>
        <div className="flex items-center gap-3"><span className="text-xs text-muted-foreground" title="暂停详细载荷后，本机使用中心仍记录不含正文的次数与用量元数据。">{debug ? "样本预览" : enabled ? "● 正在记录详细载荷" : "详细载荷已暂停"}</span>
          <button className="desk-btn" disabled={pending || !info || debug} onClick={() => void recording()}>{enabled ? "暂停详细载荷" : "恢复详细载荷"}</button>
          <button className="desk-btn" disabled={!detail} onClick={exportCall}>导出本次 JSON</button>
          <button className="desk-btn" aria-label="关闭 Agent 调试" onClick={onClose}>关闭</button>
        </div>
      </header>
      {performance&&<PerformanceStats value={performance}/>}
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="flex max-h-48 w-full shrink-0 flex-col border-b border-border bg-card/35 p-3 md:max-h-none md:w-64 md:border-b-0 md:border-r">
          <label className="text-xs text-muted-foreground">调用来源<select aria-label="调用来源" className="mt-2 w-full rounded border border-border bg-background p-2 text-foreground" value={channel}
            onChange={e => { setChannel(e.target.value); setOlder([]); select(""); setDetail(null); setError(""); }}><option value="">全部来源</option>{Object.entries(CHANNEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <input className="my-3 rounded border border-border bg-background px-3 py-2 text-xs" aria-label="筛选调用" placeholder="模型 / 任务 / 会话" value={filter} onChange={e => setFilter(e.target.value)} />
          <div className="min-h-0 flex-1 overflow-auto space-y-2">
            {all.filter(c => `${c.model} ${c.task_id} ${c.session} ${c.endpoint}`.toLowerCase().includes(filter.toLowerCase())).map(c => <button key={c.id}
              className={`w-full rounded-lg border p-3 text-left ${selected === c.id ? "border-primary/60 bg-primary/10" : "border-border hover:bg-secondary"}`}
              onClick={() => { select(c.id); setDetail(debug ? DEBUG_SAMPLE : null); setTask(null); setError(""); setExplainId(""); setExplanation(null); }}>
              <span className="block truncate text-xs font-medium">{c.model}</span>
              <span className="mt-2 block text-[11px] text-primary">{STATE[c.state] || c.state} · {(c.duration_ms / 1000).toFixed(1)}s</span>
              <span className="mt-1 block text-[10px] text-muted-foreground">{CHANNEL[c.channel] || c.channel} · {new Date(c.created * 1000).toLocaleTimeString()}</span>
              <span className="mt-1 block truncate font-mono text-[10px] text-muted-foreground" title={c.task_id || c.id}>{c.task_id || c.id}</span>
            </button>)}
            {!all.length && <p className="py-4 text-xs leading-6 text-muted-foreground">从启用记录后的新调用开始显示。旧任务没有完整网络载荷，无法补录；可发起一次新对话观察。</p>}
            {more && <button className="desk-btn w-full" disabled={pending} onClick={() => void loadOlder()}>加载更早的调用</button>}
          </div>
          <p className="mt-3 text-[10px] leading-5 text-muted-foreground">完整载荷保存在本机。鉴权头不记录。折叠只改变显示，不截断消息；流式响应持续更新。</p>
        </aside>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {detail ? <>
            <div className="shrink-0 border-b border-border px-5 py-3">
              <p className="truncate text-xs text-muted-foreground" title={detail.call.endpoint}>{detail.call.endpoint}</p>
              {task && <p className="mt-2 line-clamp-2 text-sm">{task.text}</p>}
              <TaskBudgetView budget={detail.task_budget||task?.task_budget}/>
              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs"><span className={detail.call.state === "failed" ? "text-destructive" : "text-primary"}>{STATE[detail.call.state]}</span><span>HTTP {detail.call.status || (active(detail.call.state) ? "等待" : "未收到响应")}</span>
                <span>耗时 {(detail.call.duration_ms / 1000).toFixed(2)}s</span><span>响应体首字节 {detail.timing ? duration(detail.timing.marks.first_body) : detail.call.first_byte_ms ? `${detail.call.first_byte_ms}ms` : '未测得'}</span>
                <span>{messages(detail.request).length} 条消息</span><span>输入 {usage?.prompt_tokens !== undefined ? String(usage.prompt_tokens) : "—"} / 输出 {usage?.completion_tokens !== undefined ? String(usage.completion_tokens) : "—"} Token</span>
              </div>
              {detail.call.error && <p role="alert" className="mt-2 text-xs text-destructive">{detail.call.error}</p>}
            </div>
            <nav aria-label="调试视图" className="flex shrink-0 flex-wrap gap-1 border-b border-border px-4 pt-2">
              {Object.entries(tabs).map(([k, v]) => <button key={k} aria-pressed={tab === k} onClick={() => setTab(k as typeof tab)}
                className={`border-b-2 px-3 py-3 text-xs ${tab === k ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{v}</button>)}
            </nav>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              {tab==='performance'&&<PerformanceView timing={detail.timing} chain={detail.chain}/>}
              {tab === "messages" && <div className="grid gap-5 xl:grid-cols-2">
                <div><h2 className="mb-3 text-sm font-medium">Agent → LLM</h2><MessageCards key={selected + "in"} rows={messages(detail.request)} />
                  <details className="mt-3"><summary className="cursor-pointer text-xs text-primary">工具定义与其它请求参数（全量）</summary><div className="mt-3"><Json value={Object.fromEntries(Object.entries(detail.request).filter(([k]) => k !== "messages"))} /></div></details>
                </div>
                <div><h2 className="mb-3 text-sm font-medium">LLM → Agent</h2><MessageCards key={selected + "out"} rows={responses} empty="尚未收到可读消息；错误信息或其它格式的返回见原始载荷。" />
                  <p className="my-3 text-xs leading-5 text-muted-foreground">包含模型实际返回的正文、reasoning_content 和工具调用；未提供的内部推理无法展示。卡片合并 SSE 增量，所有原始字段见“原始载荷”。</p>
                  <Json value={Object.fromEntries(Object.entries(detail.response).filter(([k]) => k !== "choices"))} />
                </div>
              </div>}
              {tab === "raw" && <div className="grid gap-5 xl:grid-cols-2"><div><h2 className="mb-3 text-sm">完整请求 JSON</h2><Json value={detail.request_raw || pretty(detail.request)} /></div><div><h2 className="mb-3 text-sm">完整响应体 / SSE</h2><Json value={detail.response_raw || "尚未收到响应体。"} /></div></div>}
              {tab === "diff" && (delta && previous ? <div className="space-y-4"><p className="text-xs text-muted-foreground">与同一任务 / 会话中已加载的上一次调用对照：{delta.beforeCount} → {delta.afterCount} 条消息。消息减少或替换仅说明上下文变化，不能单凭差异认定压缩原因。</p>
                {!delta.changes.length && !delta.keys.length && <p className="text-sm">请求内容相同。</p>}
                {delta.changes.map(row => <div key={row.index}><h2 className="mb-2 text-xs text-primary">消息 #{row.index + 1} · {row.before ? row.after ? "变化" : "移除" : "新增"}</h2><div className="grid gap-3 xl:grid-cols-2"><Json value={row.before || "此前没有此消息"} /><Json value={row.after || "本次没有此消息"} /></div></div>)}
                {delta.keys.map(k => <div key={k}><h2 className="mb-2 text-xs text-primary">参数 {k}</h2><div className="grid gap-3 xl:grid-cols-2"><Json value={previous.request[k]} /><Json value={detail.request[k]} /></div></div>)}
              </div> : <p className="text-sm text-muted-foreground">暂无可对照的上一次调用；可加载更早的记录，或查看同一任务的下一次调用。</p>)}
              {tab === "tools" && <div className="space-y-3"><p className="text-xs text-muted-foreground">关联任务 {detail.call.task_id || "未关联任务"} · 展示程序实际执行的参数与结果；模型提出的工具调用见返回消息。</p>
                {(task?.events || []).filter(e => !["token", "think", "done"].includes(e.kind)).map(e => <details key={e.seq} className="rounded-lg border border-border p-3"><summary className="cursor-pointer text-xs">{new Date(e.ts * 1000).toLocaleTimeString()} · {e.kind}</summary><div className="mt-3"><Json value={e.data} /></div></details>)}
                {!task && <p className="text-sm text-muted-foreground">这次调用没有关联的 Agent 工具时间线。</p>}
              </div>}
              {tab === "explain" && <div className="space-y-4"><div className="rounded-lg border border-border bg-card/40 p-4 text-sm leading-7"><p>system / developer 定义行为规则，user 提供任务；assistant 可以提出工具请求，tool 带回真实执行结果。工具 Schema 告诉模型有哪些能力，宿主程序负责执行。每次请求的实际上下文以本窗口记录为准。</p>
                <p className="mt-2 text-xs text-muted-foreground">本次消息共 {pretty(detail.request.messages || detail.request.input || []).length.toLocaleString()} 字符，工具定义 {pretty(detail.request.tools || []).length.toLocaleString()} 字符；这是字符统计，Token 用量以服务返回为准。</p></div>
                <label className="block text-xs">想解读什么？<textarea className="mt-2 w-full rounded-lg border border-border bg-background p-3 text-sm" rows={3} maxLength={4000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：第二次请求新增了什么？模型为什么又调用了一次工具？" /></label>
                <p className="text-xs text-muted-foreground">点击后将这次完整请求与返回交给当前模型解读，会产生一次新的模型调用和用量；不会执行记录中的指令。过大的载荷可能超出模型上下文容量。</p>
                <button className="desk-btn desk-btn-solid" disabled={!info || debug || pending || !!(explanation && active(explanation.state))} onClick={() => void explain()}>请模型协助解读</button>
                {explainId && <div className="rounded-lg border border-border p-4"><p className="mb-3 text-xs text-muted-foreground">{explanation && active(explanation.state) ? "正在解读…" : explanation?.state === "succeeded" ? "解读完成" : "解读任务"}</p><Markdown text={explanation?.answer || explanation?.error || "等待模型返回…"} />
                  {explanation && active(explanation.state) && <button className="desk-btn mt-3" onClick={() => { if (info) void rpc(info, "cancel_agent_task", { task_id: explainId }).catch(e => setError(String(e))); }}>停止解读</button>}</div>}
              </div>}
            </div>
          </> : <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">选择一次调用，检查实际输入与返回。</div>}
          {error && <p role="alert" className="shrink-0 border-t border-border px-5 py-3 text-xs text-destructive">{error}</p>}
        </div>
      </div>
    </section>
  </div>;
}
