import { useState, type ReactNode } from "react";
import { PlainLinks } from "./Markdown";

export type MemoryFact = {
  id: string;
  text: string;
  writer: string;
  quote: string;
  updated: string;
};

export type MemoryTurn = {
  role: string;
  text: string;
};

export type ContextView = {
  budget: number | null;
  tool_reserve: number | null;
  before: number;
  estimate: number;
  saved: number;
  summary: string;
  covered: MemoryTurn[];
  over: boolean | null;
};

export type MemoryPayload = {
  ok?: boolean;
  error?: string;
  total?: number;
  fact_limit?: number;
  facts?: MemoryFact[];
  items?: MemoryTurn[];
  context?: ContextView;
};

function turnKey(role: string, text: string): string {
  return `${role}\n${text}`;
}

/** 与 context_pack.estimate_tokens 同一把尺子：汉字和标点各 1，连续英文或数字每 4 个字符 1。 */
export function estimateTokens(text: string): number {
  let tokens = 0;
  let asciiRun = 0;
  for (const ch of text) {
    const code = ch.codePointAt(0) ?? 0;
    if (code <= 0x7f && !/\s/.test(ch)) {
      asciiRun += 1;
      continue;
    }
    if (asciiRun) {
      tokens += Math.floor((asciiRun + 3) / 4);
      asciiRun = 0;
    }
    tokens += 1;
  }
  if (asciiRun) tokens += Math.floor((asciiRun + 3) / 4);
  return tokens;
}

export function coveredStats(covered: MemoryTurn[]): { chars: number; tokens: number } {
  let chars = 0;
  let tokens = 0;
  for (const item of covered) {
    const text = item.text.trim();
    chars += [...text].length;
    tokens += 4 + estimateTokens(text);
  }
  return { chars, tokens };
}

export function splitIndex(items: { role: string; text: string }[], covered: MemoryTurn[]): number {
  if (!covered.length) return -1;
  const keys = new Set(covered.map((item) => turnKey(item.role, item.text.trim())));
  const idx = items.findIndex((item) => !keys.has(turnKey(item.role, (item.text || "").trim())));
  return idx === -1 ? items.length : idx;
}

/** 去重后还没收进摘要的句子。顺序与 session_for_model 再去掉 covered 相同。 */
export function modelVerbatim(items: { role: string; text: string }[], covered: MemoryTurn[]): MemoryTurn[] {
  const coveredKeys = new Set(covered.map((item) => turnKey(item.role, item.text.trim())));
  const kept: MemoryTurn[] = [];
  const seen = new Set<string>();
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const role = items[i].role;
    const text = (items[i].text || "").trim();
    if (role !== "user" && role !== "pet") continue;
    if (!text) continue;
    const key = turnKey(role, text);
    if (seen.has(key)) continue;
    seen.add(key);
    if (coveredKeys.has(key)) continue;
    kept.push({ role, text });
  }
  kept.reverse();
  return kept;
}

export function ContextCard(props: {
  context: ContextView | null;
  verbatim?: MemoryTurn[];
  busy: boolean;
  onCompress: () => Promise<boolean>;
  picked?: (role: string, text: string) => boolean;
  onToggle?: (role: string, text: string, on: boolean) => void;
  variant?: "panel" | "bar";
  leading?: ReactNode;
}) {
  const [opened, setOpened] = useState(false);
  const context = props.context;
  const bar = props.variant === "bar";
  if (!context) {
    return (
      <div className={bar ? "mb-2 flex items-center gap-3" : undefined}>
        {props.leading}
        <p data-context-missing className="text-sm text-destructive">
          还没有上下文估算。恢复：停掉当前客户端，在 desk-companion\client 里重新运行 pnpm tauri dev。
        </p>
      </div>
    );
  }
  const covered = context.covered || [];
  const verbatim = props.verbatim || [];
  const hint = reductionText(context);
  if (bar) {
    return (
      <div
        data-context=""
        data-context-place="bar"
        data-context-budget={context.budget}
        data-context-before={context.before}
        data-context-estimate={context.estimate}
        data-context-saved={context.saved}
        data-context-over={context.over === null ? "unknown" : context.over ? "1" : "0"}
        className="mb-2 text-sm"
      >
        <div className="composer-toolbar">
          {props.leading}
          <button
            type="button"
            data-context-toggle=""
            aria-expanded={opened}
            title={hint}
            className="desk-menu-item"
            onClick={() => setOpened((value) => !value)}
          >
            上下文 · {triggerCap(context) === null ? "容量未知" : "历史估算"} ⌄
          </button>
          {opened && <button
            type="button"
            data-context-compress=""
            disabled={props.busy}
            className="shrink-0 desk-btn"
            onClick={() => void props.onCompress()}
          >
            压缩更早的对话
          </button>}
        </div>
        {opened && <><p className="mt-2 text-xs text-muted-foreground">{hint}</p><ContextMeter context={context} /></>}
        {opened ? (
          <ContextBody
            context={context}
            covered={covered}
            verbatim={verbatim}
            busy={props.busy}
            picked={props.picked}
            onToggle={props.onToggle}
          />
        ) : null}
      </div>
    );
  }
  return (
    <div
      data-context=""
      data-context-budget={context.budget}
      data-context-before={context.before}
      data-context-estimate={context.estimate}
      data-context-saved={context.saved}
      data-context-over={context.over === null ? "unknown" : context.over ? "1" : "0"}
      className="border border-border px-3 py-3 text-sm"
    >
      <div className="flex items-center justify-between gap-3">
        <p>
          {reductionText(context)}
        </p>
        <button
          type="button"
          data-context-compress=""
          disabled={props.busy}
          className="desk-btn"
          onClick={() => void props.onCompress()}
        >
          压缩更早的对话
        </button>
      </div>
      <ContextBody
        context={context}
        covered={covered}
        verbatim={verbatim}
        busy={props.busy}
        picked={props.picked}
        onToggle={props.onToggle}
      />
    </div>
  );
}

function triggerCap(context: ContextView): number | null {
  return typeof context.budget === "number" && typeof context.tool_reserve === "number"
    ? context.budget - context.tool_reserve : null;
}

function reductionText(context: ContextView): string {
  const cap = triggerCap(context);
  if (!context.summary) {
    return cap === null ? `历史字符估算 ${context.estimate} · 模型窗口未知`
      : `历史估算 ${context.estimate} / 声明可用 ${cap}（不含工具）`;
  }
  if (typeof context.before !== "number" || typeof context.saved !== "number") {
    return "压缩前后的差额还没有返回。恢复：停掉当前客户端，在 desk-companion\\client 里重新运行 pnpm tauri dev。";
  }
  const delta = context.saved;
  const change = delta >= 0 ? `少 ${delta}` : `多 ${-delta}`;
  return `原文 ${context.before} → ${context.estimate}，${change}`;
}

function ContextMeter(props: { context: ContextView }) {
  const cap = triggerCap(props.context);
  const percent = cap === null ? undefined : cap <= 0 ? 100 : Math.round((props.context.estimate / cap) * 100);
  const fill = percent === undefined ? 0 : Math.min(100, percent);
  const label = reductionText(props.context);
  return (
    <div className="mt-2 flex items-center gap-3">
      <div
        data-context-meter=""
        data-context-progress={percent}
        data-context-cap={cap ?? undefined}
        role={cap === null ? "status" : "progressbar"}
        aria-valuemin={cap === null ? undefined : 0}
        aria-valuemax={cap ?? undefined}
        aria-valuenow={cap === null ? undefined : props.context.estimate}
        aria-label={label}
        className="h-1 min-w-0 flex-1"
        style={{ background: "hsl(var(--border))" }}
      >
        <div className="h-full" style={{ width: `${fill}%`, background: "hsl(var(--primary))" }} />
      </div>
      <span data-context-meter-label="" className="shrink-0 text-xs text-foreground">
        {label}
      </span>
    </div>
  );
}

export function ContextSplit(props: { covered: MemoryTurn[] }) {
  if (!props.covered.length) return null;
  const stats = coveredStats(props.covered);
  return (
    <div data-context-split="" data-context-chars={stats.chars} data-context-tokens={stats.tokens} className="py-1">
      <div className="h-px w-full" style={{ background: "hsl(var(--border))" }} />
      <p className="mt-1 text-center text-xs text-muted-foreground">
        {`以上已压缩 · ${stats.chars} 字 · 估算 ${stats.tokens} token`}
      </p>
    </div>
  );
}

function ContextBody(props: {
  context: ContextView;
  covered: MemoryTurn[];
  verbatim: MemoryTurn[];
  busy: boolean;
  picked?: (role: string, text: string) => boolean;
  onToggle?: (role: string, text: string, on: boolean) => void;
}) {
  if (!props.context.summary) {
    return (
      <p data-context-summary="" className="mt-2 text-muted-foreground">
        历史原文用于准备下一轮；实际发送材料可在调用上下文查看。
      </p>
    );
  }
  return (
    <div className="mt-3 max-h-64 space-y-3 overflow-y-auto">
      <section data-context-sent="" className="border-l-2 py-1 pl-3" style={{ borderColor: "hsl(var(--primary))" }}>
        <h3 className="text-sm font-semibold">发给模型</h3>
        <p className="mt-2 text-xs text-muted-foreground">摘要</p>
        <p data-context-summary className="mt-1 whitespace-pre-wrap">
          {props.context.summary}
        </p>
        <div data-context-verbatim-block="" className="mt-3 border px-3 py-2" style={{ borderColor: "hsl(var(--primary) / 0.55)" }}>
          <h4 className="text-sm font-semibold">仍按原文</h4>
          {props.verbatim.length === 0 ? (
            <p className="mt-1 text-muted-foreground">没有仍按原文发送的句子。</p>
          ) : (
            <div className="mt-2 space-y-2">
              {props.verbatim.map((item, idx) => (
                <TurnLine
                  key={`${item.role}-${idx}`}
                  item={item}
                  mark="verbatim"
                  busy={props.busy}
                  picked={props.picked}
                  onToggle={props.onToggle}
                />
              ))}
            </div>
          )}
        </div>
      </section>
      <section data-context-local="" className="px-3 py-2" style={{ background: "hsl(var(--muted-foreground) / 0.12)" }}>
        <h3 className="text-sm font-semibold">只留在对话里</h3>
        {props.covered.length === 0 ? (
          <p className="mt-1 text-muted-foreground">没有只留在对话里的原句。</p>
        ) : (
          <div className="mt-2 space-y-2">
            {props.covered.map((item, idx) => (
              <TurnLine
                key={`${item.role}-${idx}`}
                item={item}
                mark="covered"
                busy={props.busy}
                picked={props.picked}
                onToggle={props.onToggle}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function TurnLine(props: {
  item: MemoryTurn;
  mark: "covered" | "verbatim";
  busy: boolean;
  picked?: (role: string, text: string) => boolean;
  onToggle?: (role: string, text: string, on: boolean) => void;
}) {
  return (
    <div
      data-context-covered={props.mark === "covered" ? "" : undefined}
      data-context-verbatim={props.mark === "verbatim" ? "" : undefined}
      data-memory-window={props.mark === "verbatim" && props.onToggle ? "" : undefined}
      data-role={props.item.role}
      className="flex items-start gap-3"
    >
      {props.onToggle ? (
        <input
          type="checkbox"
          data-memory-pick="turn"
          data-key={props.item.role}
          checked={props.picked ? props.picked(props.item.role, props.item.text) : false}
          className="mt-0.5 h-5 w-5 shrink-0 accent-red-500"
          disabled={props.busy}
          onChange={(ev) => props.onToggle?.(props.item.role, props.item.text, ev.target.checked)}
        />
      ) : null}
      <div data-memory-line>
        <span className="text-muted-foreground">{props.item.role === "user" ? "你" : "凯尔希"}</span>{" "}
        {props.item.role === "user" ? <PlainLinks text={props.item.text} /> : props.item.text}
      </div>
    </div>
  );
}
