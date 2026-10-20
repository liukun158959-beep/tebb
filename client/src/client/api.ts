import { invoke } from "@tauri-apps/api/core";
import type { Sampling } from "./sampling";

export type BackendInfo = { port: number; token: string; scope?: "collaboration" };

export type ChatItem = {
  role: string;
  text: string;
  ts?: string;
  notes?: string[];
  knowledge?: {
    subagent: string;
    question: string;
    candidates: { doc: string; title: string; score: number; text: string }[];
    kept: { doc: string; title: string; score: number; text: string }[];
    answer?: string;
  };
  thinking?: string;
  elapsed_s?: number;
  react_loops?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  model?: string;
  reasoning_effort?: string;
  temperature?: number;
  top_p?: number;
};

export type SessionItem = {
  id: string;
  title: string;
  updated: string;
  count: number;
};

type RpcEnvelope<T> = { ok: boolean; result?: T; error?: string };

export async function backendInfo(): Promise<BackendInfo> {
  return invoke<BackendInfo>("backend_info");
}

function wsUrl(info: BackendInfo): string {
  return `ws://127.0.0.1:${info.port}/${info.scope === "collaboration" ? "collaboration" : "ws"}?token=${encodeURIComponent(info.token)}`;
}

export type StreamHandlers = {
  onToken: (piece: string) => void;
  onThink: (piece: string) => void;
  onStatus: (text: string) => void;
  onDone: (answer: string) => void;
  onError: (message: string) => void;
  onKnowledge?: (trace: ChatItem["knowledge"]) => void;
};

export type ChatChips = { skills: string[]; cli: string[]; github: string; attachments?: string[] };

export type NotebookDone = {
  answer: string;
  cites: {
    n: number;
    doc: string;
    title: string;
    text: string;
    doc_id: string;
    context?: string;
  }[];
};

export type NotebookFailure = Partial<NotebookDone> & { answer_complete?: boolean };

export function streamNotebook(
  info: BackendInfo,
  sessionId: string,
  text: string,
  docIds: string[],
  sampling: Sampling,
  handlers: {
    onToken: (piece: string) => void;
    onStatus: (text: string) => void;
    onDone: (payload: NotebookDone) => void;
    onError: (message: string, result?: NotebookFailure) => void;
  },
): void {
  const ws = new WebSocket(wsUrl(info));
  let settled = false;
  function fail(message: string, result?: NotebookFailure) {
    if (settled) return;
    settled = true;
    ws.close();
    handlers.onError(message, result);
  }
  ws.onopen = () => {
    if (settled) return;
    ws.send(JSON.stringify({
      type: "notebook",
      session_id: sessionId,
      text,
      doc_ids: docIds,
      sampling,
    }));
  };
  ws.onmessage = (ev) => {
    if (settled) return;
    let msg: { type: string; data?: string; result?: NotebookFailure };
    let done: NotebookDone | undefined;
    try {
      msg = JSON.parse(String(ev.data));
      if (typeof msg?.type !== 'string' || typeof msg.data !== 'string') throw new Error('invalid event');
      if (msg.type === 'done') {
        done = JSON.parse(msg.data) as NotebookDone;
        if (typeof done?.answer !== 'string' || !done.answer.trim() || !Array.isArray(done.cites)) throw new Error('invalid result');
      } else if (!['token', 'status', 'error'].includes(msg.type)) throw new Error('unknown event');
    } catch {
      fail('本地后端返回了无法解析的笔记结果，操作可能仍在执行；请重新读取笔记核对。');
      return;
    }
    if (msg.type === 'token') handlers.onToken(msg.data || '');
    else if (msg.type === 'status') handlers.onStatus(msg.data || '');
    else if (done) {
      settled = true;
      ws.close();
      handlers.onDone(done);
    } else fail(msg.data || '笔记提问失败', msg.result);
  };
  ws.onerror = () => fail('笔记连接异常，尚未确认操作结果；后台可能继续，请重新读取笔记核对。');
  ws.onclose = () => fail('笔记连接已断开，尚未收到完成结果；后台可能继续，请重新读取笔记核对。');
}

export function streamChat(
  info: BackendInfo,
  text: string,
  sampling: Sampling,
  chips: ChatChips,
  modelId: string,
  handlers: StreamHandlers,
  knowledge = false,
): void {
  const ws = new WebSocket(wsUrl(info));
  ws.onopen = () => ws.send(JSON.stringify({ type: "chat", text, sampling, chips, model_id: modelId, knowledge }));
  ws.onmessage = (ev) => {
    const msg = JSON.parse(String(ev.data)) as { type: string; data?: string };
    if (msg.type === "token") handlers.onToken(msg.data || "");
    else if (msg.type === "think") handlers.onThink(msg.data || "");
    else if (msg.type === "status") handlers.onStatus(msg.data || "");
    else if (msg.type === "knowledge") handlers.onKnowledge?.(JSON.parse(msg.data || "null"));
    else if (msg.type === "done") {
      handlers.onDone(msg.data || "");
      ws.close();
    } else if (msg.type === "error") {
      handlers.onError(msg.data || "对话失败");
      ws.close();
    }
  };
  ws.onerror = () => handlers.onError("连不上本地后端。恢复：看 tebb 日志后重启客户端");
}

export function rpc<T>(
  info: BackendInfo,
  method: string,
  args: Record<string, unknown> = {},
  options: { timeoutMs?: number } = {},
): Promise<T> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl(info));
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    function finish(error?: Error, result?: T) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      ws.close();
      if (error) reject(error); else resolve(result as T);
    }
    if (options.timeoutMs) timer = setTimeout(() => finish(new Error("调用超时，已停止等待；操作可能仍在执行，请先核查结果。")), options.timeoutMs);
    ws.onopen = () => {
      if (settled) return;
      ws.send(JSON.stringify({ type: "rpc", id: method, method, args }));
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(String(ev.data)) as {
          type: string;
          result?: RpcEnvelope<T>;
          data?: string;
        };
        if (msg.type === "rpc_result" && msg.result?.ok) {
          finish(undefined, msg.result.result as T);
          return;
        }
        finish(new Error(msg.result?.error || msg.data || "RPC 失败"));
      } catch { finish(new Error("本地后端返回了无法解析的结果，请查看日志。")); }
    };
    ws.onerror = () => finish(new Error("连不上本地后端。恢复：看 tebb 日志后重启客户端"));
    ws.onclose = () => finish(new Error("连接已断开，尚未收到操作结果；请先核查是否已执行。"));
  });
}
