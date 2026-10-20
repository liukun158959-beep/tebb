import { useState, type ReactNode } from "react";
import { Button, TacticalPanel } from "reend-components";
import { StatusGrid, type StatusView } from "./status";
import { DateTimeField } from "./datetime-field";

// 把 load_board 的今日快照收成看板视图。缺字段或失败就露出错误，不当成「今天没有安排」。

export type AgendaItem = { summary: string; start: string; end?: string; event_id?: string };
export type TaskItem = { summary: string; due_at?: string; url?: string; guid?: string };

export type BoardSection<T> = {
  ok: boolean;
  items?: T[];
  error?: string;
};

export type BoardPayload = {
  ok?: boolean;
  error?: string;
  date?: string;
  agenda?: BoardSection<AgendaItem>;
  tasks?: BoardSection<TaskItem>;
  summary?: string;
};

export type BoardEvent = { time: string; title: string; end: string; eventId: string };
export type BoardTask = { title: string; when: string; overdue: boolean; guid: string };

export type BoardModel = {
  fatal: string;
  date: string;
  events: BoardEvent[];
  agendaError: string;
  agendaEmpty: boolean;
  tasks: BoardTask[];
  taskError: string;
  taskEmpty: boolean;
  summary: string;
};

const EMPTY: BoardModel = {
  fatal: "",
  date: "",
  events: [],
  agendaError: "",
  agendaEmpty: false,
  tasks: [],
  taskError: "",
  taskEmpty: false,
  summary: "",
};

function timeKey(start: string): string {
  if (start.includes("全天")) return `0-${start}`;
  const hit = start.match(/\d{2}:\d{2}/);
  if (hit) return `1-${hit[0]}`;
  return `2-${start}`;
}

function clock(due: string, now: Date): { when: string; overdue: boolean } {
  const text = due.trim();
  if (!text) return { when: "未设截止", overdue: false };
  const dt = new Date(text);
  if (Number.isNaN(dt.getTime())) return { when: text, overdue: false };
  const when = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(dt);
  return { when, overdue: dt.getTime() < now.getTime() };
}

export function boardModel(data: BoardPayload, now: Date): BoardModel {
  if (data.ok === false) {
    return { ...EMPTY, fatal: data.error || "看板读取失败。" };
  }
  if (!data.agenda || !data.tasks) {
    return { ...EMPTY, fatal: "看板数据缺 agenda 或 tasks。" };
  }
  const model: BoardModel = {
    ...EMPTY,
    date: data.date || "",
    summary: data.summary || "",
  };
  if (!data.agenda.ok) {
    model.agendaError = data.agenda.error || "读取日程失败。";
  } else {
    const items = data.agenda.items || [];
    model.agendaEmpty = items.length === 0;
    model.events = items
      .map((item) => ({
        time: item.start,
        title: item.summary,
        end: item.end || "",
        eventId: item.event_id?.trim() || "",
      }))
      .sort((a, b) => timeKey(a.time).localeCompare(timeKey(b.time)));
  }
  if (!data.tasks.ok) {
    model.taskError = data.tasks.error || "读取待办失败。";
  } else {
    const items = data.tasks.items || [];
    model.taskEmpty = items.length === 0;
    model.tasks = items.map((item) => {
      const due = clock(item.due_at || "", now);
      return { title: item.summary, when: due.when, overdue: due.overdue, guid: item.guid?.trim() || "" };
    });
  }
  return model;
}

// 调试样本不请求。刷新总是重查。这一轮已经打开过看板就只切换页面。
export function shouldReloadBoard(opts: { refresh: boolean; seen: boolean; debug: boolean }): boolean {
  if (opts.debug) return false;
  if (opts.refresh) return true;
  return !opts.seen;
}

export function eventRange(
  summary: string,
  startLocal: string,
  endLocal: string,
): { summary: string; start: string; end: string } {
  const title = summary.trim();
  if (!title) throw new Error("要填写日程标题。");
  if (title.includes("\n") || title.includes("\r")) throw new Error("标题不能换行。");
  const start = localToOffset(startLocal, "开始时间");
  const end = localToOffset(endLocal, "结束时间");
  if (Date.parse(start) >= Date.parse(end)) throw new Error("开始时间必须早于结束时间。");
  return { summary: title, start, end };
}

function localToOffset(value: string, label: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) {
    throw new Error(`${label}要选日期和时间。`);
  }
  const withSeconds = value.length === 16 ? `${value}:00` : value;
  return `${withSeconds}+08:00`;
}

export function BoardPane(props: {
  data: BoardPayload | null;
  nowIso: string | null;
  loading: boolean;
  loadError?: string;
  debug: boolean;
  onRefresh: () => void;
  statuses: StatusView[];
  fetches: number;
  onOpenFeishu?: () => void;
  deleting?: boolean;
  deleteErrors?: Record<string, string>;
  onDeleteEvent?: (eventId: string) => void;
  taskErrors?: Record<string, string>;
  onDeleteTask?: (guid: string) => void;
  createError?: string;
  createNotice?: string;
  createNonce?: number;
  onCreateEvent?: (summary: string, start: string, end: string) => void;
}) {
  if (!props.data) {
    return (
      <div data-board data-board-fetches={props.fetches}>
        <div className="mb-6 flex items-end justify-between gap-4">
          <div><h1 className="text-xl font-semibold">今日看板</h1>
            <p className="text-sm text-muted-foreground">{props.loading ? "正在读取今日看板…" : "打开看板页时再拉今日数据。"}</p>
          </div>
          <Button type="button" variant="secondary" size="sm" disabled={props.debug || props.loading} onClick={props.onRefresh}>
            {props.loading ? "读取中…" : "刷新"}
          </Button>
        </div>
        {props.loading ? <div className="tebb-loading-grid" aria-busy="true" aria-label="正在读取看板内容">
          {["日程", "待办"].map(title => <section key={title}><h2>{title}</h2><div className="tebb-skeleton"/><div className="tebb-skeleton"/><div className="tebb-skeleton"/></section>)}
        </div> : null}
        {props.loadError ? <p role="alert" className="text-sm text-destructive">{props.loadError}</p> : null}
        <StatusGrid cards={props.statuses} onOpenFeishu={props.onOpenFeishu} />
      </div>
    );
  }
  const now = props.nowIso ? new Date(props.nowIso) : new Date();
  const model = boardModel(props.data, now);
  return (
    <div data-board data-board-fetches={props.fetches}>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">今日看板</h1>
          {model.date ? <div className="mt-1 text-sm text-muted-foreground">{model.date}</div> : null}
        </div>
        <Button type="button" variant="secondary" size="sm" disabled={props.debug || props.loading} onClick={props.onRefresh}>
          {props.loading ? "读取中…" : "刷新"}
        </Button>
      </div>
      {props.loadError ? <p role="alert" className="mb-4 text-sm text-destructive">{props.loadError}</p> : null}
      {props.debug ? <div className="mb-4 text-xs text-muted-foreground">调试样本，未连接后端</div> : null}
      {model.fatal ? (
        <div data-board-error className="bg-card p-5 text-sm text-destructive">
          {model.fatal}
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Section title="日程">
            <CreateEventBox
              key={props.createNonce || 0}
              disabled={!!props.deleting}
              error={props.createError || ""}
              notice={props.createNotice || ""}
              onCreate={(summary, start, end) => props.onCreateEvent?.(summary, start, end)}
            />
            {model.agendaError ? <p data-agenda-error className="text-sm text-destructive">{model.agendaError}</p> : null}
            {model.agendaEmpty ? <p data-agenda-empty className="text-sm text-muted-foreground">今天没有日程。</p> : null}
            <ol className="tl">
              {model.events.map((event) => (
                <li key={`${event.eventId}-${event.time}-${event.title}`} data-event data-event-id={event.eventId} className="flex items-start justify-between gap-3">
                  <div>
                    <div data-time className="text-xs text-muted-foreground">{event.time}</div>
                    <div data-title>{event.title}</div>
                    {event.end ? <div data-event-end className="text-xs text-muted-foreground">至 {event.end}</div> : null}
                    {event.eventId ? null : (
                      <p data-event-nodelete className="text-xs text-muted-foreground">没有日程 ID，不能删除。</p>
                    )}
                    {event.eventId && props.deleteErrors?.[event.eventId] ? (
                      <p data-event-error className="text-xs text-destructive">{props.deleteErrors[event.eventId]}</p>
                    ) : null}
                  </div>
                  {event.eventId ? (
                    <span data-event-delete="" data-event-delete-disabled={props.deleting ? "1" : "0"}>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="desk-btn-danger"
                        disabled={props.deleting}
                        onClick={() => props.onDeleteEvent?.(event.eventId)}
                      >
                        删除
                      </Button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
          </Section>
          <Section title="待办">
            {model.taskError ? <p data-task-error className="text-sm text-destructive">{model.taskError}</p> : null}
            {model.taskEmpty ? <p data-task-empty className="text-sm text-muted-foreground">今天没有待办。</p> : null}
            <ul className="space-y-2">
              {model.tasks.map((task) => (
                <li
                  key={`${task.guid}-${task.title}-${task.when}`}
                  data-task
                  data-task-id={task.guid}
                  data-overdue={task.overdue ? "1" : "0"}
                  className="flex items-start justify-between gap-3 border border-border bg-background px-4 py-3"
                >
                  <div>
                    <div data-task-title>{task.title}</div>
                    <div data-task-when className="text-xs text-muted-foreground">
                      {task.when}
                      {task.overdue ? " 已过期" : ""}
                    </div>
                    {task.guid ? null : (
                      <p data-task-nodelete className="text-xs text-muted-foreground">没有任务 ID，不能删除。</p>
                    )}
                    {task.guid && props.taskErrors?.[task.guid] ? (
                      <p data-task-error className="text-xs text-destructive">{props.taskErrors[task.guid]}</p>
                    ) : null}
                  </div>
                  {task.guid ? (
                    <span data-task-delete="" data-task-delete-disabled={props.deleting ? "1" : "0"}>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="desk-btn-danger"
                        disabled={props.deleting}
                        onClick={() => props.onDeleteTask?.(task.guid)}
                      >
                        删除
                      </Button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </Section>
        </div>
      )}
      {model.summary ? <p data-summary className="mt-6 text-sm leading-6 text-muted-foreground">{model.summary}</p> : null}
      <StatusGrid cards={props.statuses} onOpenFeishu={props.onOpenFeishu} />
    </div>
  );
}

function CreateEventBox(props: {
  disabled: boolean;
  error: string;
  notice: string;
  onCreate: (summary: string, start: string, end: string) => void;
}) {
  const [summary, setSummary] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [localError, setLocalError] = useState("");
  const [open, setOpen] = useState<"" | "start" | "end">("");
  const shownError = localError || props.error;
  return (
    <form
      className="mb-4 space-y-2"
      data-event-create=""
      data-event-create-disabled={props.disabled ? "1" : "0"}
      onSubmit={(ev) => {
        ev.preventDefault();
        setLocalError("");
        try {
          const fields = eventRange(summary, start, end);
          props.onCreate(fields.summary, fields.start, fields.end);
        } catch (err) {
          setLocalError(err instanceof Error ? err.message : String(err));
        }
      }}
    >
      <input
        className="w-full border border-border bg-background px-2 py-1 text-sm"
        data-event-summary=""
        placeholder="日程标题"
        value={summary}
        onChange={(ev) => setSummary(ev.target.value)}
      />
      <div className="flex gap-2">
        <DateTimeField
          marker="start"
          placeholder="选择开始"
          value={start}
          disabled={props.disabled}
          open={open === "start"}
          onOpen={() => setOpen("start")}
          onClose={() => setOpen("")}
          onChange={setStart}
        />
        <DateTimeField
          marker="end"
          placeholder="选择结束"
          value={end}
          disabled={props.disabled}
          open={open === "end"}
          onOpen={() => setOpen("end")}
          onClose={() => setOpen("")}
          onChange={setEnd}
        />
      </div>
      <Button type="submit" variant="secondary" size="sm" disabled={props.disabled}>
        创建
      </Button>
      {shownError ? <p data-event-create-error className="text-xs text-destructive">{shownError}</p> : null}
      {props.notice ? <p data-event-create-notice className="text-xs text-muted-foreground">{props.notice}</p> : null}
    </form>
  );
}

function Section(props: { title: string; children: ReactNode }) {
  return (
    <TacticalPanel title={props.title} status="online">
      {props.children}
    </TacticalPanel>
  );
}
