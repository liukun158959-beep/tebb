/// <reference types="vite/client" />
import type { ChatItem } from "./api";
import type { BoardPayload } from "./board";
import type { ContextView, MemoryPayload } from "./memory";
import type { FeishuSnap } from "./feishu";
import { statusFromPayload, type StatusKind, type StatusView } from "./status";

// 仅开发态。浏览器打开 /client.html?debug=1 不连 Tauri、不打模型，直接铺一条样本回复。
// 代理用 window.__deskDebug.snapshot() 读气泡里的标签，确认列表和代码块真的排出来了。

export const COMPOSER_FIXTURE = {
  ok: true,
  skills: [
    { id: "feishu-doc-writing", label: "feishu-doc-writing", description: "写今日工作总结" },
    { id: "weekly-retro", label: "weekly-retro", description: "写本周复盘" },
    { id: "github-repo-summary", label: "github-repo-summary", description: "总结仓库近况" },
    { id: "skill-5", label: "技能五", description: "用来把菜单撑过一屏" },
    { id: "skill-6", label: "技能六", description: "用来把菜单撑过一屏" },
    { id: "skill-7", label: "技能七", description: "用来把菜单撑过一屏" },
    { id: "skill-8", label: "技能八", description: "用来把菜单撑过一屏" },
  ],
  cli: [
    { id: "get_today_agenda", label: "今日日程" },
    { id: "github_recent", label: "GitHub 近况" },
  ],
  github: {
    ok: true,
    items: [{ id: "liukun158959-beep/tebb", label: "liukun158959-beep/tebb" }],
  },
};

export const DEBUG_FIXTURE: ChatItem[] = [
  { role: "user", text: "先看今天有没有空" },
  { role: "pet", text: "看过了。" },
  {
    role: "user",
    text: "用列表写三步，并给一段 Python。**不要解析我**\n飞书文档《[本周复盘](https://example.feishu.cn/docx/doc-token)》",
  },
  {
    role: "pet",
    text: [
      "三步：",
      "",
      "1. 打开看板",
      "2. 看日程",
      "3. 再问我",
      "",
      "```python",
      'print("hello")',
      "```",
      "",
      "<script>alert(1)</script>",
      "",
      "见 [说明](https://example.com/help)",
    ].join("\n"),
    notes: ["在看今天的日程…", "在看未完成的待办…"],
    thinking: "先看今天有没有空，再排这三步。",
    elapsed_s: 4.2,
    react_loops: 2,
    input_tokens: 120,
    output_tokens: 40,
    total_tokens: 160,
    model: "example-model",
    reasoning_effort: "high",
    temperature: 1,
    top_p: 0.95,
    knowledge: {
      subagent: "知识库检索",
      question: "注意力怎么算",
      candidates: [{ doc: "注意力机制", title: "缩放点积", score: 0.82, text: "查询和键做点积。" }],
      kept: [{ doc: "注意力机制", title: "缩放点积", score: 1.4, text: "查询和键做点积。" }],
    },
  },
];

export type BubbleSnap = {
  role: string;
  tags: string[];
  text: string;
  thinking: string;
  thinkingLive: string;
  thinkingDots: number;
  thinkingEdge: string;
  thinkWait: string;
  meta: string;
  knowledgeSteps: { step: string; open: string; label: string }[];
};

export const BOARD_NOW = "2026-10-05T15:00:00+08:00";

// 日程故意打乱。待办一条在样本时刻之前，一条在之后。
export const BOARD_FIXTURE: BoardPayload = {
  ok: true,
  date: "2026-10-05",
  summary: "样本总结，不是飞书原文。",
  agenda: {
    ok: true,
    items: [
      { summary: "晚间复盘", start: "18:00", end: "18:30", event_id: "evt-evening" },
      { summary: "全天专注", start: "2026-10-05 全天" },
      { summary: "早会", start: "09:10", end: "09:40", event_id: "evt-morning" },
      { summary: "跨天占用", start: "10月5日 09:00", end: "10月6日 10:00", event_id: "evt-span" },
    ],
  },
  tasks: {
    ok: true,
    items: [
      { summary: "交周报", due_at: "2026-10-05T10:00:00+08:00", guid: "task-report" },
      { summary: "看看板", due_at: "2026-10-05T20:00:00+08:00" },
    ],
  },
};

export const STATUS_RAW: Record<StatusKind, unknown> = {
  feishu: { ok: true, logged_in: true, user_name: "博士" },
  github: { ok: false, error: "gh auth login" },
};

export const STATUS_FIXTURE: StatusView[] = (Object.keys(STATUS_RAW) as StatusKind[]).map((kind) =>
  statusFromPayload(kind, STATUS_RAW[kind]),
);

export const FEISHU_LOGGED_OUT: FeishuSnap = {
  ok: true,
  installed: true,
  logged_in: false,
  login_busy: false,
  hint: "请在看板「飞书」页登录。需要日历、待办和云文档权限：lark-cli auth login --domain calendar,task,docs",
};

export const FEISHU_WAITING: FeishuSnap = {
  ok: true,
  installed: true,
  logged_in: false,
  login_busy: true,
};

export const FEISHU_LOGGED_IN: FeishuSnap = {
  ok: true,
  installed: true,
  logged_in: true,
  login_busy: false,
  user_name: "博士",
  has_calendar: true,
  has_task: true,
  has_docs: false,
};

export const DEBUG_CONTEXT: ContextView = {
  budget: null,
  tool_reserve: null,
  before: 180000,
  estimate: 140000,
  saved: 40000,
  over: null,
  summary: "博士要先看今天的空档，再排三步。",
  covered: [
    { role: "user", text: "先看今天有没有空" },
    { role: "pet", text: "看过了。" },
  ],
};

export const MEMORY_FIXTURE: MemoryPayload = {
  ok: true,
  total: 5,
  fact_limit: 30,
  context: DEBUG_CONTEXT,
  facts: [
    {
      id: "user-fact",
      text: "银灰要精二",
      writer: "user",
      quote: "",
      updated: "2026-10-06T21:00:00+08:00",
    },
    {
      id: "model-fact",
      text: "用中文短句回答",
      writer: "model",
      quote: "用中文短句回我",
      updated: "2026-10-06T21:10:00+08:00",
    },
  ],
  items: [
    { role: "user", text: "用中文短句回我" },
    { role: "pet", text: "好。" },
  ],
};

export const KNOWLEDGE_FIXTURE = {
  ok: true,
  subagent: "知识库检索",
  embed_repo: "BAAI/bge-small-zh-v1.5",
  rerank_repo: "BAAI/bge-reranker-base",
  embed_ready: true,
  rerank_ready: false,
  endpoint: "https://modelscope.cn",
  model_dir: "desk-companion/models",
  chunk_size: 800,
  retrieve_k: 20,
  rerank_n: 4,
  chunk_strategy: "structure",
  chunk_unit: "chars",
  overlap: 200,
  indexed_chunk_size: 800,
  indexed_strategy: "structure",
  indexed_unit: "chars",
  indexed_overlap: 200,
  indexed_embed: "BAAI/bge-small-zh-v1.5",
  models: [
    { role: "embed", repo: "BAAI/bge-small-zh-v1.5", blurb: "中文，体积小，适合先跑通。", ready: true, present: true, selected: true },
    { role: "embed", repo: "BAAI/bge-base-zh-v1.5", blurb: "中文，比 small 更大，CPU 上更慢。", ready: false, present: false, selected: false },
    { role: "embed", repo: "BAAI/bge-m3", blurb: "中英混合时用，体积更大。", ready: false, present: false, selected: false },
    { role: "rerank", repo: "BAAI/bge-reranker-base", blurb: "中英重排，体积适中。", ready: false, present: true, selected: true },
    { role: "rerank", repo: "BAAI/bge-reranker-v2-m3", blurb: "中英都要时用，比 base 更大。", ready: false, present: false, selected: false },
  ],
  strategies: [
    { id: "fixed", label: "固定长度切片", blurb: "按字数或 token 数切开。最简单，容易切断语义。" },
    { id: "structure", label: "按结构切片", blurb: "按标题、段落、句子切开，句子尽量保持完整。" },
    { id: "semantic", label: "语义切片", blurb: "相邻句子的向量相似度低于 0.5 就切开，相似的留在一块。" },
    { id: "overlap", label: "重叠切片", blurb: "按字数切开，相邻片段有重叠，减少边界信息丢掉。" },
    { id: "multi", label: "多级切片", blurb: "整节和段内片段都检索。命中段内片段时，把整节交给模型。" },
    { id: "parent", label: "父子切片", blurb: "用句子定位，把所在段落的完整内容交给模型。" },
  ],
  downloads: [{
    active: true,
    repo: "BAAI/bge-small-zh-v1.5",
    phase: "downloading",
    bytes: 40000000,
    total: 95000000,
    percent: 42,
    endpoint: "https://modelscope.cn",
    error: "",
    seq: 3,
  }],
  download_seq: 3,
  docs: [{ id: "https://example.feishu.cn/docx/doc-token", title: "注意力机制", url: "https://example.feishu.cn/docx/doc-token", chars: 12, chunks: 1 }],
  chunks: [{ doc_id: "https://example.feishu.cn/docx/doc-token", doc: "注意力机制", title: "缩放点积", text: "查询和键做点积。", level: "chunk", context: "" }],
  trace: {
    subagent: "知识库检索",
    question: "注意力怎么算",
    candidates: [{ doc: "注意力机制", title: "缩放点积", score: 0.82, text: "查询和键做点积。" }],
    kept: [{ doc: "注意力机制", title: "缩放点积", score: 1.4, text: "查询和键做点积。" }],
    answer: "文档里把查询和键做点积。",
  },
};

export const NOTE_FIXTURE = {
  docs: [{ id: "https://example.feishu.cn/docx/doc-token", title: "注意力机制", chars: 12, chunks: 1 }],
  sessions: [{
    id: "note-1",
    title: "注意力怎么算",
    updated: "2026-10-07T16:00:00+08:00",
    turns: [
      { role: "user", text: "注意力怎么算" },
      {
        role: "pet",
        text: "查询和键做**点积**。[1]\n\n治理思路是**\"检测 → 纠偏 → 停止兜底\u201d三层**。",
        cites: [{ n: 1, doc: "注意力机制", title: "缩放点积", text: "查询和键做点积。", doc_id: "https://example.feishu.cn/docx/doc-token" }],
      },
    ],
    notes: [
      {
        id: "n1",
        question: "注意力怎么算",
        answer: "查询和键做**点积**。[1]",
        cites: [{ n: 1, doc: "注意力机制", title: "缩放点积", text: "查询和键做点积。", doc_id: "https://example.feishu.cn/docx/doc-token" }],
        saved: "2026-10-07T16:00:00+08:00",
        files: [{
          name: "注意力怎么算-n1.md",
          path: "E:/Learn_Project/desk-companion/memory/notes/注意力怎么算-n1.md",
          saved: "2026-10-07T16:10:00+08:00",
        }],
      },
      {
        id: "n2",
        question: "治理怎么停",
        answer: "检测之后要纠偏，然后停止兜底。",
        cites: [{ n: 1, doc: "注意力机制", title: "停止兜底", text: "检测之后要停止兜底。", doc_id: "https://example.feishu.cn/docx/doc-token" }],
        saved: "2026-10-07T16:05:00+08:00",
        files: [],
      },
    ],
  }],
};

export const KNOWLEDGE_CATALOG = [
  { title: "注意力机制", url: "https://example.feishu.cn/docx/doc-token", token: "doc-token", source: "wiki", space_id: "tech", space_name: "前沿技术" },
  { title: "面试提纲", url: "https://example.feishu.cn/docx/interview", token: "interview", source: "wiki", space_id: "learning", space_name: "学习与实践" },
];

function knowledgeStepSnap(root: ParentNode): { step: string; open: string; label: string }[] {
  return [...root.querySelectorAll("[data-knowledge-step]")].map((el) => ({
    step: el.getAttribute("data-knowledge-step") || "",
    open: (el as HTMLDetailsElement).open ? "1" : "0",
    label: (el.querySelector("summary") as HTMLElement | null)?.innerText || "",
  }));
}

export function debugPane(): "work" | "chat" | "board" | "feishu" | "settings" | "usage" | "memory" | "knowledge" | "note" | "automation" | "monitor" | "video" | null {
  if (!import.meta.env.DEV) return null;
  const value = new URLSearchParams(location.search).get("debug");
  if (value === "1" || value === "chat") return "chat";
  if (value === "note") return "note";
  if (value === "work") return "work";
  if (value === "board") return "board";
  if (value === "feishu") return "feishu";
  if (value === "automation") return "automation";
  if (value === "monitor") return "monitor";
  if (value === "video") return "video";
  if (value === "settings") return "settings";
  if (value === "usage") return "usage";
  if (value === "memory") return "memory";
  if (value === "knowledge") return "knowledge";
  return null;
}

export function debugRequested(): boolean {
  return debugPane() === "chat";
}

export type BoardSnap = {
  fatal: string;
  times: string[];
  titles: string[];
  tasks: { title: string; when: string; overdue: string; id: string; nodelete: string; error: string; deleteDisabled: string }[];
  events: { title: string; time: string; end: string; id: string; nodelete: string; error: string; deleteDisabled: string }[];
  agendaError: string;
  taskError: string;
  create: { summary: string; start: string; end: string; error: string; notice: string; disabled: string };
};

export type StatusSnap = { kind: string; state: string; line: string }[];

export function installDeskDebug(api: {
  seed: (items: ChatItem[]) => void;
  seedBoard: (payload: BoardPayload, nowIso: string) => void;
  seedStatus: (raw: Record<string, unknown>) => void;
  openBoard: (refresh?: boolean) => void;
  requestToday: () => void;
  openFeishu: () => void;
  seedFeishu: (snap: FeishuSnap) => void;
  seedMemory: (payload: MemoryPayload) => void;
  shouldReloadBoard: (opts: { refresh: boolean; seen: boolean; debug: boolean }) => boolean;
  setSampling: (effort: string, temperature: number, topP: number) => void;
  pulseThinking: (on: boolean) => void;
  setGenerating: (on: boolean) => void;
  fillDraft: (text: string) => void;
  openSettings: () => void;
  setBackground: (url: string) => void;
  clearBackground: () => void;
  openBackgroundCrop: (url: string) => void;
}): void {
  if (!import.meta.env.DEV) return;
  window.__deskDebug = {
    seed: api.seed,
    seedBoard: api.seedBoard,
    seedStatus: api.seedStatus,
    openBoard: api.openBoard,
    requestToday: api.requestToday,
    openFeishu: api.openFeishu,
    seedFeishu: api.seedFeishu,
    seedMemory: api.seedMemory,
    shouldReloadBoard: api.shouldReloadBoard,
    setSampling: api.setSampling,
    pulseThinking: api.pulseThinking,
    setGenerating: api.setGenerating,
    fillDraft: api.fillDraft,
    openSettings: api.openSettings,
    setBackground: api.setBackground,
    clearBackground: api.clearBackground,
    openBackgroundCrop: api.openBackgroundCrop,
    samplingSnapshot(): { effort: string; temperature: string; topP: string } {
      const row = document.querySelector("[data-sampling]");
      return {
        effort: row?.getAttribute("data-sampling-effort") || "",
        temperature: row?.getAttribute("data-sampling-temperature") || "",
        topP: row?.getAttribute("data-sampling-top-p") || "",
      };
    },
    chatError(): string {
      return (document.querySelector("[data-chat-error]") as HTMLElement | null)?.innerText || "";
    },
    sessionSnapshot(): { id: string; generating: string; title: string; dots: number; animation: string }[] {
      return [...document.querySelectorAll("[data-session]")].map((el) => {
        const dot = el.querySelector("[data-thinking-dot]");
        return {
          id: el.getAttribute("data-session") || "",
          generating: el.getAttribute("data-session-generating") || "",
          title: (el.querySelector("div") as HTMLElement | null)?.innerText || "",
          dots: el.querySelectorAll("[data-thinking-dot]").length,
          animation: dot ? getComputedStyle(dot).animationName : "",
        };
      });
    },
    chatDraft(): string {
      return document.querySelector("[data-chat-draft]")?.getAttribute("data-chat-draft") || "";
    },
    slashSnapshot(): {
      open: string;
      repo: string;
      radius: string;
      scroll: string;
      parents: { id: string; selected: string }[];
      items: { id: string; selected: string; label: string }[];
      chips: { kind: string; id: string }[];
      error: string;
    } {
      const menu = document.querySelector("[data-slash-menu]");
      const list = document.querySelector("[data-slash-list]");
      return {
        open: menu ? "1" : "0",
        repo: menu?.getAttribute("data-slash-repo") || "",
        radius: menu ? getComputedStyle(menu).borderRadius : "",
        scroll: list ? String((list as HTMLElement).scrollTop) : "",
        parents: [...document.querySelectorAll("[data-slash-parent]")].map((el) => ({
          id: el.getAttribute("data-slash-parent") || "",
          selected: el.getAttribute("data-selected") || "",
        })),
        items: [...document.querySelectorAll("[data-slash-item]")].map((el) => ({
          id: el.getAttribute("data-slash-id") || "",
          selected: el.getAttribute("data-selected") || "",
          label: (el as HTMLElement).innerText,
        })),
        chips: [...document.querySelectorAll("[data-chip]")].map((el) => ({
          kind: el.getAttribute("data-chip-kind") || "",
          id: el.getAttribute("data-chip-id") || "",
        })),
        error: (document.querySelector("[data-slash-error]") as HTMLElement | null)?.innerText || "",
      };
    },
    snapshot(): BubbleSnap[] {
      return [...document.querySelectorAll("[data-bubble]")].map((el) => ({
        role: el.getAttribute("data-role") || "",
        tags: [...el.querySelectorAll("*")].map((node) => node.tagName.toLowerCase()),
        text: (el as HTMLElement).innerText,
        thinking: el.querySelector("[data-thinking]")?.getAttribute("data-thinking-body") || "",
        thinkingLive: el.querySelector("[data-thinking]")?.getAttribute("data-thinking-live") || "",
        thinkingDots: el.querySelectorAll("[data-thinking-dot]").length,
        thinkingEdge: el.querySelector("[data-thinking-edge]")?.getAttribute("data-thinking-edge") || "",
        thinkWait: el.querySelector("[data-think-wait='1']") ? "1" : "0",
        meta: (el.querySelector("[data-turn-meta]") as HTMLElement | null)?.innerText || "",
        knowledgeSteps: knowledgeStepSnap(el),
      }));
    },
    boardSnapshot(): BoardSnap {
      const fatal = document.querySelector("[data-board-error]");
      return {
        fatal: fatal ? (fatal as HTMLElement).innerText : "",
        times: [...document.querySelectorAll("[data-time]")].map((el) => (el as HTMLElement).innerText),
        titles: [...document.querySelectorAll("[data-title]")].map((el) => (el as HTMLElement).innerText),
        tasks: [...document.querySelectorAll("[data-task]")].map((el) => ({
          title: (el.querySelector("[data-task-title]") as HTMLElement | null)?.innerText || "",
          when: (el.querySelector("[data-task-when]") as HTMLElement | null)?.innerText || "",
          overdue: el.getAttribute("data-overdue") || "",
          id: el.getAttribute("data-task-id") || "",
          nodelete: (el.querySelector("[data-task-nodelete]") as HTMLElement | null)?.innerText || "",
          error: (el.querySelector("[data-task-error]") as HTMLElement | null)?.innerText || "",
          deleteDisabled: el.querySelector("[data-task-delete]")?.getAttribute("data-task-delete-disabled") || "",
        })),
        events: [...document.querySelectorAll("[data-event]")].map((el) => ({
          title: (el.querySelector("[data-title]") as HTMLElement | null)?.innerText || "",
          time: (el.querySelector("[data-time]") as HTMLElement | null)?.innerText || "",
          end: (el.querySelector("[data-event-end]") as HTMLElement | null)?.innerText || "",
          id: el.getAttribute("data-event-id") || "",
          nodelete: (el.querySelector("[data-event-nodelete]") as HTMLElement | null)?.innerText || "",
          error: (el.querySelector("[data-event-error]") as HTMLElement | null)?.innerText || "",
          deleteDisabled: el.querySelector("[data-event-delete]")?.getAttribute("data-event-delete-disabled") || "",
        })),
        agendaError: (document.querySelector("[data-agenda-error]") as HTMLElement | null)?.innerText || "",
        taskError: (document.querySelector("[data-task-error]") as HTMLElement | null)?.innerText || "",
        create: {
          summary: (document.querySelector("[data-event-summary]") as HTMLInputElement | null)?.value || "",
          start: document.querySelector("[data-event-time='start']")?.getAttribute("data-value") || "",
          end: document.querySelector("[data-event-time='end']")?.getAttribute("data-value") || "",
          error: (document.querySelector("[data-event-create-error]") as HTMLElement | null)?.innerText || "",
          notice: (document.querySelector("[data-event-create-notice]") as HTMLElement | null)?.innerText || "",
          disabled: document.querySelector("[data-event-create]")?.getAttribute("data-event-create-disabled") || "",
        },
      };
    },
    statusSnapshot(): StatusSnap {
      return [...document.querySelectorAll("[data-status]")].map((el) => ({
        kind: el.getAttribute("data-status") || "",
        state: el.getAttribute("data-state") || "",
        line: (el.querySelector("[data-status-line]") as HTMLElement | null)?.innerText || "",
      }));
    },
    memorySnapshot(): {
      pane: string;
      face: string;
      ok: string;
      error: string;
      formError: string;
      note: string;
      limit: string;
      facts: { id: string; writer: string; text: string; quote: string; updated: string; tone: string; color: string }[];
      windows: { role: string; text: string }[];
      contextBefore: string;
      contextEstimate: string;
      contextSaved: string;
      contextSummary: string;
      contextCovered: number;
      contextVerbatim: number;
      contextProgress: string;
      contextCap: string;
      contextSplit: string;
      picks: { kind: string; key: string; checked: string }[];
      deleteLabel: string;
      deleteDisabled: string;
    } {
      const root = document.querySelector("[data-memory]");
      return {
        pane: document.querySelector("[data-pane]")?.getAttribute("data-pane") || "",
        face: document.querySelector("[data-chat-face]")?.getAttribute("data-chat-face") || "",
        ok: root?.getAttribute("data-memory-ok") || "",
        error: (document.querySelector("[data-memory-error]") as HTMLElement | null)?.innerText || "",
        formError: (document.querySelector("[data-memory-form-error]") as HTMLElement | null)?.innerText || "",
        note: (document.querySelector("[data-memory-window-note]") as HTMLElement | null)?.innerText || "",
        limit: root?.getAttribute("data-memory-limit") || "",
        facts: [...document.querySelectorAll("[data-memory-fact]")].map((el) => {
          const input = el.querySelector("[data-memory-edit]") as HTMLInputElement | null;
          return {
            id: el.getAttribute("data-id") || "",
            writer: el.getAttribute("data-writer") || "",
            text: el.getAttribute("data-text") || "",
            quote: el.getAttribute("data-quote") || "",
            updated: el.getAttribute("data-updated") || "",
            tone: input?.getAttribute("data-memory-tone") || "",
            color: input ? getComputedStyle(input).color : "",
          };
        }),
        windows: [...document.querySelectorAll("[data-memory-window]")].map((el) => ({
          role: el.getAttribute("data-role") || "",
          text: (el.querySelector("[data-memory-line]") as HTMLElement | null)?.innerText || (el as HTMLElement).innerText,
        })),
        contextBefore: document.querySelector("[data-context]")?.getAttribute("data-context-before") || "",
        contextEstimate: document.querySelector("[data-context]")?.getAttribute("data-context-estimate") || "",
        contextSaved: document.querySelector("[data-context]")?.getAttribute("data-context-saved") || "",
        contextSummary: (document.querySelector("[data-context-summary]") as HTMLElement | null)?.innerText || "",
        contextCovered: document.querySelectorAll("[data-context-covered]").length,
        contextVerbatim: document.querySelectorAll("[data-context-verbatim]").length,
        contextProgress: document.querySelector("[data-context-meter]")?.getAttribute("data-context-progress") || "",
        contextCap: document.querySelector("[data-context-meter]")?.getAttribute("data-context-cap") || "",
        contextSplit: (document.querySelector("[data-context-split]") as HTMLElement | null)?.innerText || "",
        picks: [...document.querySelectorAll("[data-memory-pick]")].map((el) => ({
          kind: el.getAttribute("data-memory-pick") || "",
          key: el.getAttribute("data-key") || "",
          checked: (el as HTMLInputElement).checked ? "1" : "0",
        })),
        deleteLabel: (document.querySelector("[data-memory-delete-selected]") as HTMLElement | null)?.innerText || "",
        deleteDisabled: (document.querySelector("[data-memory-delete-selected]") as HTMLButtonElement | null)?.disabled ? "1" : "0",
      };
    },
    navSnapshot(): { mode: string; sub: string; subs: string[]; sides: string[] } {
      const nav = document.querySelector("[data-nav]");
      return {
        mode: nav?.getAttribute("data-nav") || "",
        sub: document.querySelector("[data-sub][data-selected='1']")?.getAttribute("data-sub") || "",
        subs: [...document.querySelectorAll("[data-sub]")].map((el) => el.getAttribute("data-sub") || ""),
        sides: [...document.querySelectorAll("[data-side-item]")].map((el) => el.getAttribute("data-side-item") || ""),
      };
    },
    feishuSnapshot(): {
      pane: string;
      logged: string;
      busy: string;
      message: string;
      missing: string[];
      loginDisabled: string;
      hasLogout: boolean;
    } {
      const root = document.querySelector("[data-feishu]");
      return {
        pane: document.querySelector("[data-pane]")?.getAttribute("data-pane") || "",
        logged: root?.getAttribute("data-feishu-logged") || "",
        busy: root?.getAttribute("data-feishu-busy") || "",
        message: (document.querySelector("[data-feishu-message]") as HTMLElement | null)?.innerText || "",
        missing: [...document.querySelectorAll("[data-feishu-missing]")].map((el) => (el as HTMLElement).innerText),
        loginDisabled: document.querySelector("[data-feishu-login]")?.getAttribute("data-feishu-login-disabled") || "",
        hasLogout: !!document.querySelector("[data-feishu-logout]"),
      };
    },
    settingsSnapshot(): {
      pane: string;
      photo: string;
      scrim: string;
      base: string;
      model: string;
      hasKey: string;
      keyFilled: string;
      error: string;
      notice: string;
      bgError: string;
      theme: string;
      clear: string;
      persona: string;
      personaError: string;
      personaNotice: string;
      entries: { id: string; active: string; selected: string; label: string }[];
      pick: string;
      pickText: string;
      crop: string;
    } {
      const key = document.querySelector("[data-model-key]") as HTMLInputElement | null;
      const pick = document.querySelector("[data-model-pick]") as HTMLSelectElement | null;
      return {
        pane: document.querySelector("[data-pane]")?.getAttribute("data-pane") || "",
        photo: document.querySelector("[data-bg-photo]")?.getAttribute("data-bg-photo") || "",
        scrim: document.querySelector("[data-bg-scrim]") ? "1" : "0",
        base: (document.querySelector("[data-model-base]") as HTMLInputElement | null)?.value || "",
        model: (document.querySelector("[data-model-name]") as HTMLInputElement | null)?.value || "",
        hasKey: key?.getAttribute("data-model-has-key") || "",
        keyFilled: key?.getAttribute("data-model-key-filled") || "",
        error: (document.querySelector("[data-settings-error]") as HTMLElement | null)?.innerText || "",
        notice: (document.querySelector("[data-settings-notice]") as HTMLElement | null)?.innerText || "",
        bgError: (document.querySelector("[data-bg-error]") as HTMLElement | null)?.innerText || "",
        theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
        clear: document.querySelector("[data-bg-clear]") ? "1" : "0",
        persona: (document.querySelector("[data-persona]") as HTMLTextAreaElement | null)?.value || "",
        personaError: (document.querySelector("[data-persona-error]") as HTMLElement | null)?.innerText || "",
        personaNotice: (document.querySelector("[data-persona-notice]") as HTMLElement | null)?.innerText || "",
        entries: [...document.querySelectorAll("[data-model-entry]")].map((el) => ({
          id: el.getAttribute("data-model-entry") || "",
          active: el.getAttribute("data-active") || "",
          selected: el.getAttribute("data-selected") || "",
          label: (el as HTMLElement).innerText,
        })),
        pick: pick?.value || "",
        pickText: pick?.selectedOptions[0]?.text || "",
        crop: document.querySelector("[data-bg-crop]") ? "1" : "0",
      };
    },
    knowledgeSnapshot(): {
      face: string;
      ok: string;
      error: string;
      dir: string;
      embed: string;
      rerank: string;
      docs: string[];
      chunks: number;
      candidates: number;
      kept: number;
      question: string;
      answer: string;
      toggle: string;
      endpoint: string;
      phase: string;
      percent: string;
      progress: string;
      deletes: number;
      blurb: string;
      strategy: string;
      strategyBlurb: string;
      pickAtStart: string;
      removes: number;
      removeStatus: string;
      steps: { step: string; open: string; label: string }[];
    } {
      const root = document.querySelector("[data-knowledge]");
      const bar = document.querySelector("[data-knowledge-download-progress]");
      const strategy = document.querySelector("[data-knowledge-strategy]") as HTMLSelectElement | null;
      const catalog = document.querySelector("[data-knowledge-catalog]");
      return {
        face: document.querySelector("[data-chat-face]")?.getAttribute("data-chat-face") || "",
        ok: root?.getAttribute("data-knowledge-ok") || "",
        error: (document.querySelector("[data-knowledge-error]") as HTMLElement | null)?.innerText || "",
        dir: (document.querySelector("[data-knowledge-dir]") as HTMLElement | null)?.innerText || "",
        embed: document.querySelector("[data-knowledge-model='BAAI/bge-small-zh-v1.5']") ? "1" : "0",
        rerank: document.querySelector("[data-knowledge-model='BAAI/bge-reranker-base']") ? "1" : "0",
        docs: [...document.querySelectorAll("[data-knowledge-doc]")].map((el) => el.getAttribute("data-title") || ""),
        chunks: document.querySelectorAll("[data-knowledge-chunk]").length,
        candidates: document.querySelectorAll("[data-knowledge-hit='candidate']").length,
        kept: document.querySelectorAll("[data-knowledge-hit='kept']").length,
        question: document.querySelector("[data-knowledge-question]")?.getAttribute("data-knowledge-question") || "",
        answer: (document.querySelector("[data-knowledge-answer]") as HTMLElement | null)?.innerText || "",
        toggle: document.querySelector("[data-knowledge-toggle]")?.getAttribute("aria-pressed") || "",
        endpoint: (document.querySelector("[data-knowledge-endpoint]") as HTMLElement | null)?.innerText || "",
        phase: bar?.getAttribute("data-knowledge-phase") || "",
        percent: bar?.getAttribute("data-knowledge-percent") || "",
        progress: (document.querySelector("[data-knowledge-download-label]") as HTMLElement | null)?.innerText || "",
        deletes: document.querySelectorAll("[data-knowledge-model-delete]").length,
        blurb: (document.querySelector("[data-knowledge-model-blurb]") as HTMLElement | null)?.innerText || "",
        strategy: strategy?.value || "",
        strategyBlurb: (document.querySelector("[data-knowledge-strategy-blurb]") as HTMLElement | null)?.innerText || "",
        pickAtStart: catalog?.querySelector("[data-knowledge-pick]")?.parentElement?.firstElementChild?.getAttribute("data-knowledge-pick") === "" ? "1" : "0",
        removes: document.querySelectorAll("[data-knowledge-remove]").length,
        removeStatus: (document.querySelector("[data-knowledge-remove-status]") as HTMLElement | null)?.innerText || "",
        steps: knowledgeStepSnap(document),
      };
    },
    noteSnapshot(): {
      session: string;
      sessions: { id: string; title: string; active: string }[];
      sources: { id: string; checked: string; title: string }[];
      turns: { role: string; text: string }[];
      cites: number;
      quote: string;
      notes: string[];
      markdown: number;
      feishu: number;
      strong: number;
      sourceWidth: string;
      notesWidth: string;
      resizers: number;
      files: { name: string; path: string }[];
      answers: { id: string; theme: string; open: string; picked: string }[];
      summarize: string;
      buttons: { radius: string; fill: string; danger: string };
      status: string;
      error: string;
      saveStatus: string;
      docUrl: string;
      toggle: string;
    } {
      const board = document.querySelector("[data-note-board]");
      return {
        session: document.querySelector("[data-note-session]")?.getAttribute("data-note-session") || "",
        sessions: [...document.querySelectorAll("[data-notebook]")].map((el) => ({
          id: el.getAttribute("data-notebook") || "",
          title: (el as HTMLElement).innerText,
          active: el.getAttribute("data-active") || "",
        })),
        sources: [...document.querySelectorAll("[data-note-source]")].map((el) => ({
          id: el.getAttribute("data-note-source") || "",
          checked: el.getAttribute("data-checked") || "",
          title: (el as HTMLElement).innerText,
        })),
        turns: [...document.querySelectorAll("[data-note-turn]")].map((el) => ({
          role: el.getAttribute("data-role") || "",
          text: (el as HTMLElement).innerText,
        })),
        cites: document.querySelectorAll("[data-note-cite]").length,
        quote: (document.querySelector("[data-note-quote]") as HTMLElement | null)?.innerText || "",
        notes: [...document.querySelectorAll("[data-note-item]")].map((el) => el.getAttribute("data-note-item") || ""),
        markdown: document.querySelectorAll("[data-note-markdown]").length,
        feishu: document.querySelectorAll("[data-note-feishu]").length,
        strong: board?.querySelectorAll("[data-note-markdown-body] strong").length || 0,
        sourceWidth: document.querySelector("[data-note-sources]")?.getAttribute("data-note-source-width") || "",
        notesWidth: document.querySelector("[data-note-notes]")?.getAttribute("data-note-notes-width") || "",
        resizers: document.querySelectorAll("[data-note-resize]").length,
        files: [...document.querySelectorAll("[data-note-file]")].map((el) => ({
          name: el.getAttribute("data-note-file") || "",
          path: el.getAttribute("data-note-file-path") || "",
        })),
        answers: [...document.querySelectorAll("[data-note-answer]")].map((el) => ({
          id: el.getAttribute("data-note-answer") || "",
          theme: (el.querySelector("[data-note-theme]") as HTMLElement | null)?.innerText || "",
          open: el.getAttribute("data-open") || "",
          picked: (el.querySelector("[data-note-answer-pick]") as HTMLInputElement | null)?.checked ? "1" : "0",
        })),
        summarize: document.querySelector("[data-note-summarize]")?.getAttribute("data-note-summarize-count") || "",
        buttons: (() => {
          const action = document.querySelector("[data-note-markdown]");
          const danger = document.querySelector("[data-note-delete]");
          const actionStyle = action ? getComputedStyle(action) : null;
          const dangerStyle = danger ? getComputedStyle(danger) : null;
          return {
            radius: actionStyle?.borderRadius || "",
            fill: actionStyle?.backgroundColor || "",
            danger: dangerStyle?.color || "",
          };
        })(),
        status: (document.querySelector("[data-note-status]") as HTMLElement | null)?.innerText || "",
        error: (document.querySelector("[data-note-error]") as HTMLElement | null)?.innerText || "",
        saveStatus: (document.querySelector("[data-note-save-status]") as HTMLElement | null)?.innerText || "",
        docUrl: document.querySelector("[data-note-doc-url]")?.getAttribute("data-note-doc-url") || "",
        toggle: document.querySelector("[data-note-toggle]") ? "1" : "0",
      };
    },
    bgMotion(): { grid: string; particles: string } {
      const layer = document.querySelector("[data-bg-motion]");
      if (!layer) return { grid: "", particles: "" };
      const grid = getComputedStyle(layer, "::before");
      const particles = getComputedStyle(layer, "::after");
      return { grid: grid.animationName, particles: particles.animationName };
    },
  };
}

declare global {
  interface Window {
    __deskDebug?: {
      seed: (items: ChatItem[]) => void;
      seedBoard: (payload: BoardPayload, nowIso: string) => void;
      seedStatus: (raw: Record<string, unknown>) => void;
      openBoard: (refresh?: boolean) => void;
      requestToday: () => void;
      openFeishu: () => void;
      seedFeishu: (snap: FeishuSnap) => void;
      seedMemory: (payload: MemoryPayload) => void;
      shouldReloadBoard: (opts: { refresh: boolean; seen: boolean; debug: boolean }) => boolean;
      setSampling: (effort: string, temperature: number, topP: number) => void;
      pulseThinking: (on: boolean) => void;
      setGenerating: (on: boolean) => void;
      fillDraft: (text: string) => void;
      openSettings: () => void;
      setBackground: (url: string) => void;
      clearBackground: () => void;
      openBackgroundCrop: (url: string) => void;
      settingsSnapshot: () => {
        pane: string;
        photo: string;
        scrim: string;
        base: string;
        model: string;
        hasKey: string;
        keyFilled: string;
        error: string;
        notice: string;
        bgError: string;
        theme: string;
        clear: string;
        persona: string;
        personaError: string;
        personaNotice: string;
        entries: { id: string; active: string; selected: string; label: string }[];
        pick: string;
        pickText: string;
        crop: string;
      };
      sessionSnapshot: () => { id: string; generating: string; title: string; dots: number; animation: string }[];
      chatDraft: () => string;
      slashSnapshot: () => {
        open: string;
        repo: string;
        radius: string;
        scroll: string;
        parents: { id: string; selected: string }[];
        items: { id: string; selected: string; label: string }[];
        chips: { kind: string; id: string }[];
        error: string;
      };
      samplingSnapshot: () => { effort: string; temperature: string; topP: string };
      chatError: () => string;
      snapshot: () => BubbleSnap[];
      boardSnapshot: () => BoardSnap;
      statusSnapshot: () => StatusSnap;
      memorySnapshot: () => {
        pane: string;
        face: string;
        ok: string;
        error: string;
        formError: string;
        note: string;
        limit: string;
        facts: { id: string; writer: string; text: string; quote: string; updated: string; tone: string; color: string }[];
        windows: { role: string; text: string }[];
        contextBefore: string;
        contextEstimate: string;
        contextSaved: string;
        contextSummary: string;
        contextCovered: number;
        contextVerbatim: number;
        contextProgress: string;
        contextCap: string;
        contextSplit: string;
        picks: { kind: string; key: string; checked: string }[];
        deleteLabel: string;
        deleteDisabled: string;
      };
      navSnapshot: () => { mode: string; sub: string; subs: string[]; sides: string[] };
      feishuSnapshot: () => {
        pane: string;
        logged: string;
        busy: string;
        message: string;
        missing: string[];
        loginDisabled: string;
        hasLogout: boolean;
      };
      knowledgeSnapshot: () => {
        face: string;
        ok: string;
        error: string;
        dir: string;
        embed: string;
        rerank: string;
        docs: string[];
        chunks: number;
        candidates: number;
        kept: number;
        question: string;
        answer: string;
        toggle: string;
        endpoint: string;
        phase: string;
        percent: string;
        progress: string;
        deletes: number;
        blurb: string;
        strategy: string;
        strategyBlurb: string;
        pickAtStart: string;
        removes: number;
        removeStatus: string;
        steps: { step: string; open: string; label: string }[];
      };
      noteSnapshot: () => {
        session: string;
        sessions: { id: string; title: string; active: string }[];
        sources: { id: string; checked: string; title: string }[];
        turns: { role: string; text: string }[];
        cites: number;
        quote: string;
        notes: string[];
        markdown: number;
        feishu: number;
        strong: number;
        sourceWidth: string;
        notesWidth: string;
        resizers: number;
        files: { name: string; path: string }[];
        answers: { id: string; theme: string; open: string; picked: string }[];
        summarize: string;
        buttons: { radius: string; fill: string; danger: string };
        status: string;
        error: string;
        saveStatus: string;
        docUrl: string;
        toggle: string;
      };
      bgMotion: () => { grid: string; particles: string };
    };
  }
}
