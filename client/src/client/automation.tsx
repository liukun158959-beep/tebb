import { usePageActive } from "./page-stack";
import { useEffect, useState } from "react";
import { rpc, type BackendInfo } from "./api";
import { Markdown } from "./Markdown";

type Settings = { profile: string; wiki_url: string; chat_id: string; base_url: string; base_token: string; table_id: string;
  topics: string[]; lookback_days: number; highlights: number };
type Report = { lead: string; candidate_count: number; items: { title: string; title_zh?: string; company?: string; url: string; category: string; summary: string; detail?: string; key_points?: string[]; value: string; caution: string; published: string; date_verified: boolean }[] };
type Run = { id: string; day: string; status: string; error: string; doc_url?: string; report?: Report; warnings?: string[]; cover_ready?: boolean };
type Job = { id?: string; name: string; action: string; enabled: boolean; cadence: string; weekdays: number[]; hour: number; minute: number; doc: string;
  schedule_text?: string; last_result?: string; last_error?: string; last_task_id?: string; active?: boolean };
type Event = { seq: number; kind: string; ts: number; data: string | { tool?: string; status?: string; duration_ms?: number } };
type Task = { id: string; state: string; answer: string; error: string; source: { run_id?: string }; events?: Event[] };
const labels: Record<string, string> = { ai_news: "AI/Agent 资讯日报", retro_gen: "生成本周复盘", retro_write: "覆盖写入复盘文档" };
const states: Record<string, string> = { collecting: "采集核实中", draft: "待发布预览", publishing: "正在归档和推送", published: "已发布", archived: "已归档，未推送群", failed: "未完成",
  queued: "排队中", running: "执行中", succeeded: "完成", timed_out: "超时", interrupted: "已中断", cancelled: "已停止", ok: "完成", fail: "失败", missed: "错过时段" };
const blankJob = (): Job => ({ name: "每日 AI/Agent 工程资讯", action: "ai_news", enabled: false, cadence: "daily", weekdays: [], hour: 9, minute: 0, doc: "" });
const active = (task?: Task) => task && ["queued", "running", "cancelling"].includes(task.state);
const jobActive = (job: Job) => job.active || ['queued', 'running'].includes(job.last_result || '');

export function AutomationPane({ info, debug = false }: { info: BackendInfo | null; debug?: boolean }) {
  const pageActive = usePageActive();
  const [form, setForm] = useState<Settings | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [job, setJob] = useState<Job>(blankJob);
  const [selected, select] = useState("");
  const [task, setTask] = useState<Task>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const run = runs.find(r => r.id === selected);
  useEffect(() => {
    if (!pageActive || !info || debug) return;
    let live = true, fetching = false;
    const refresh = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const [news, schedules, tasks] = await Promise.all([
          rpc<{ settings: Settings; runs: Run[] }>(info, "load_news"),
          rpc<{ ok: boolean; error?: string; items: Job[] }>(info, "list_automation_jobs"),
          rpc<{ items: Task[] }>(info, "list_agent_tasks", { channel: "automation" }),
        ]);
        if (!live) return;
        if (schedules.ok === false) throw new Error(schedules.error || "定时任务读取失败");
        setForm(f => f || news.settings); setRuns(news.runs); setJobs(schedules.items);
        const rid = selected || news.runs[0]?.id || tasks.items[0]?.source.run_id;
        if (rid) {
          select(s => s || rid);
          const current = tasks.items.find(t => t.source.run_id === rid);
          if (current) {
            const detail = await rpc<{ task: Task }>(info, "get_agent_task", { task_id: current.id, focus: false });
            if (live) setTask(detail.task);
          } else setTask(undefined);
        }
      } catch (err) { if (live) setError(String(err)); }
      finally { fetching = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => { live = false; window.clearInterval(timer); };
  }, [pageActive, info, debug, selected]);

  async function call(method: string, args: Record<string, unknown> = {}) {
    if (!info || debug || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (method === "save_automation_job" && job.action === "ai_news" && job.enabled) {
        await rpc(info, "check_news_targets");
      }
      const result = await rpc<{ ok: boolean; error?: string; settings?: Settings; items?: Job[]; run_id?: string;
        targets?: { wiki_title: string; chat_name: string }; message?: string }>(info, method, args);
      if (result.ok === false) throw new Error(result.error || "操作未成功");
      if (result.settings) setForm(result.settings);
      if (result.items) setJobs(result.items);
      if (result.run_id) select(result.run_id);
      if (method === "check_news_targets" && result.targets) setNotice(`位置检查通过：知识库「${result.targets.wiki_title}」，群「${result.targets.chat_name}」。`);
      else if (method === "run_news") setNotice(args.publish ? "发布任务已登记，会复用已完成阶段。" : "预览任务已登记，采集和总结不会推送群消息。");
      else setNotice(result.message || "设置已保存。");
    } catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  }
  const inputClass = "mt-1 block w-full rounded-lg bg-secondary px-3 py-2 text-sm text-foreground";
  const buttonClass = "rounded-lg border border-border px-3 py-2 text-sm hover:bg-secondary disabled:opacity-40";
  return <section className="mt-8 rounded-xl border border-border bg-card/60 p-5" data-testid="automation-pane">
    <h2 className="text-lg font-semibold">大厂开源与技术动向</h2>
    <p className="mt-2 text-sm text-muted-foreground">每天精选 5 条国内外大厂的 AI 开源项目与技术动向。群卡片简述，知识库文档展开技术要点、实践价值和局限，英文标题附中文译名。tebb 需保持运行。</p>
    {form && <details className="mt-4" open={!form.wiki_url || !form.base_url}>
      <summary className="cursor-pointer text-sm font-medium">采集主题和飞书位置</summary>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {([['profile', '飞书 CLI 应用名称'], ['wiki_url', '知识库父页面链接'], ['chat_id', '推送群会话 ID（留空则只归档）'], ['base_url', '资讯多维表格链接'], ['table_id', '资讯数据表 ID']] as const).map(([key, label]) =>
          <label className="text-sm" key={key}>{label}<input className={inputClass} value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} /></label>)}
        <label className="text-sm">来源时间范围（天）<input className={inputClass} type="number" min={1} max={14} value={form.lookback_days} onChange={e => setForm({ ...form, lookback_days: Number(e.target.value) })} /></label>
        <p className="text-sm self-center">每日重点：固定 5 条，国内与海外兼顾。</p>
        <label className="text-sm md:col-span-2">采集主题（每行一个，最多 4 个）<textarea className={inputClass} rows={4} value={form.topics.join('\n')} onChange={e => setForm({ ...form, topics: e.target.value.split('\n') })} /></label>
      </div>
      <div className="mt-3 flex gap-2"><button className={buttonClass} disabled={busy || debug} onClick={() => void call("save_news_settings", { payload: form })}>保存资讯设置</button>
        <button className={buttonClass} disabled={busy || debug} onClick={() => void call("check_news_targets")}>检查已保存的推送位置</button></div>
    </details>}
    <div className="mt-5 flex flex-wrap items-center gap-2">
      <button className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-40" disabled={busy || !!active(task) || debug} onClick={() => void call("run_news", { publish: false })}>生成今日预览</button>
      <button className={buttonClass} disabled={busy || !!active(task) || !run?.report || ['published','archived'].includes(run.status) || debug} onClick={() => void call("run_news", { publish: true, run_id: selected, send_group: false })}>仅归档文档和资讯表</button>
      <button className={buttonClass} disabled={busy || !!active(task) || !run?.report || run.status === "published" || debug} onClick={() => void call("run_news", { publish: true, run_id: selected })}>发布这份日报到飞书</button>
      {active(task) && <button className={buttonClass} disabled={busy} onClick={() => void call("cancel_agent_task", { task_id: task!.id })}>停止资讯任务</button>}
      <span className="text-xs text-muted-foreground">总执行预算 15 分钟；定时执行遇到临时失败最多重试一次。最新错过的时段补跑一次。</span>
    </div>
    {!!runs.length && <div className="mt-4"><label className="text-sm">执行记录<select className={inputClass} value={selected} onChange={e => select(e.target.value)}>
      {runs.map(r => <option key={r.id} value={r.id}>{r.day} · {states[r.status] || r.status} · {r.id.slice(0,8)}</option>)}</select></label></div>}
    {task && <p className="mt-3 text-sm">当前任务：{states[task.state] || task.state}{task.error ? ` · ${task.error}` : ""}</p>}
    {run?.report && <div className="mt-4 rounded-lg bg-secondary/60 p-4">
      {run.cover_ready && info && <img className="mb-4 w-full rounded-lg" src={`http://127.0.0.1:${info.port}/news-cover/${run.id}?token=${encodeURIComponent(info.token)}`} alt="本期大厂开源与技术动向海报" />}
      <p className="font-medium">{run.report.lead}</p><p className="mt-1 text-xs text-muted-foreground">核实 {run.report.candidate_count} 个官方来源。实践建议需要项目内验证。</p>
      {run.report.items.map((item, index) => <div key={item.url} className="mt-4 border-t border-border pt-3">
        <Markdown text={`**${index+1}. ${item.title}${item.title_zh && item.title_zh !== item.title ? `（${item.title_zh}）` : ''}**\n\n${item.summary}`} />
        <details className="mt-2 text-sm"><summary className="cursor-pointer text-primary">查看详细说明与实践建议</summary>
          <Markdown text={`${item.detail || item.summary}\n\n${(item.key_points || []).map(p => `- ${p}`).join('\n')}\n\n实践建议：${item.value}\n\n局限：${item.caution || '尚未在本项目验证。'}\n\n发布时间：${item.date_verified ? item.published : '来源未标注可核实日期'}\n\n[官方来源](${item.url})`} />
        </details>
      </div>)}
      {run.doc_url && <Markdown text={`[打开每日文档](${run.doc_url})`} />}
      {!!run.warnings?.length && <details className="mt-3 text-xs text-muted-foreground"><summary>采集缺口（{run.warnings.length}）</summary>{run.warnings.map((w,i) => <p key={i} className="mt-1">{w}</p>)}</details>}
    </div>}
    {run?.error && <p className="mt-3 text-sm text-red-500">{run.error}</p>}
    {!!task?.events?.length && <details className="mt-3"><summary className="cursor-pointer text-sm">任务时间线</summary><ol className="mt-2 max-h-64 space-y-1 overflow-auto text-xs text-muted-foreground">
      {task.events.filter(e => ["status", "tool_start", "tool_end", "llm_start", "llm_end", "error"].includes(e.kind)).map(e => <li key={e.seq}>
        {new Date(e.ts * 1000).toLocaleTimeString()} · {typeof e.data === "string" ? e.data : e.kind.startsWith("tool") ? `${e.kind === "tool_start" ? "调用" : "返回"} ${e.data.tool} ${e.data.status || ''}` : e.kind === "llm_start" ? "开始整理技术摘要" : "摘要模型返回"}
      </li>)}</ol></details>}
    <details className="mt-6"><summary className="cursor-pointer font-medium">定时任务管理（北京时间）</summary>
      <p className="mt-2 text-xs text-muted-foreground">先检查首轮预览，再开启自动推送。排队、执行和收尾期间不能修改、启停或删除该定时；正在运行的资讯任务可单独停止。</p>
      <div className="mt-3 space-y-2">{jobs.map(j => <div key={j.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-secondary/60 p-3 text-sm">
        <span className="flex-1">{j.name} · {j.schedule_text} · {j.enabled ? '已启用' : '已停用'} · {states[j.last_result || ''] || '尚未执行'}</span>
        <button className={buttonClass} disabled={busy || debug || jobActive(j)} onClick={() => setJob(j)}>修改</button>
        <button className={buttonClass} disabled={busy || debug || jobActive(j)} onClick={() => void call("run_automation_job", { job_id: j.id })}>{j.action === 'ai_news' ? '立即发布' : '立即执行'}</button>
        <button className={buttonClass} disabled={busy || debug || jobActive(j)} onClick={() => void call("delete_automation_job", { job_id: j.id })}>删除定时</button>
        {j.last_error && <p className="w-full text-xs text-red-500">{j.last_error}</p>}
      </div>)}</div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label className="text-sm">任务名称<input className={inputClass} value={job.name} onChange={e => setJob({ ...job, name: e.target.value })} /></label>
        <label className="text-sm">任务动作<select className={inputClass} value={job.action} onChange={e => setJob({ ...job, action: e.target.value, doc: e.target.value === 'retro_write' ? job.doc : '' })}>{Object.entries(labels).map(([key,value]) => <option value={key} key={key}>{value}</option>)}</select></label>
        <label className="text-sm">周期<select className={inputClass} value={job.cadence} onChange={e => setJob({ ...job, cadence: e.target.value, weekdays: e.target.value === 'daily' ? [] : [0] })}><option value="daily">每天</option><option value="weekly">每周</option></select></label>
        <label className="text-sm">执行时间<input className={inputClass} type="time" value={`${String(job.hour).padStart(2,'0')}:${String(job.minute).padStart(2,'0')}`} onChange={e => { const [hour,minute] = e.target.value.split(':').map(Number); setJob({ ...job, hour, minute }); }} /></label>
        {job.cadence === 'weekly' && <div className="flex gap-2 md:col-span-2">{'一二三四五六日'.split('').map((day,index) => <label className="text-sm" key={index}><input type="checkbox" checked={job.weekdays.includes(index)} onChange={e => setJob({ ...job, weekdays: e.target.checked ? [...job.weekdays,index] : job.weekdays.filter(d => d !== index) })} /> 周{day}</label>)}</div>}
        {job.action === 'retro_write' && <label className="text-sm md:col-span-2">复盘文档链接<input className={inputClass} value={job.doc} onChange={e => setJob({ ...job, doc: e.target.value })} /></label>}
        <label className="text-sm"><input type="checkbox" checked={job.enabled} onChange={e => setJob({ ...job, enabled: e.target.checked })} /> 启用此定时任务</label>
      </div>
      <div className="mt-3 flex gap-2"><button className={buttonClass} disabled={busy || debug || jobs.some(j => j.id === job.id && jobActive(j))} onClick={() => void call("save_automation_job", { payload: job })}>保存定时任务</button><button className={buttonClass} onClick={() => setJob(blankJob())}>新增定时任务</button></div>
    </details>
    {notice && <p className="mt-3 text-sm text-green-500">{notice}</p>}{error && <p className="mt-3 text-sm text-red-500" role="alert">{error}</p>}
  </section>;
}
