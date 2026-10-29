import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { backendInfo, rpc, type BackendInfo } from "./api";
import type { LocalSource } from "./local-sources";
import { ChatPrototype } from "./chat-prototype";
import { ReportAssetProvider } from './report-charts';
import type { DemoAction, DemoState } from "./chat-prototype-state";
import { startupReady } from "./startup";
import { CollaborationSync, loadCollaboration } from './collaboration-sync';

const ACCESS_KEY = "tebb.collaboration.preview-access";

export function browserAccess(): BackendInfo | null {
  try {
    const fragment = new URLSearchParams(window.location.hash.slice(1)).get("collaboration");
    const raw = fragment || sessionStorage.getItem(ACCESS_KEY);
    if (!raw) return null;
    const info = JSON.parse(raw);
    if (!Number.isInteger(info.port) || info.port < 1 || info.port > 65535 || typeof info.token !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(info.token)) return null;
    const access: BackendInfo = { port: info.port, token: info.token, scope: "collaboration" };
    if (fragment) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      try { sessionStorage.setItem(ACCESS_KEY, JSON.stringify(access)); } catch { /* This tab can still use the in-memory capability. */ }
    }
    return access;
  } catch { return null; }
}

export function LiveCollaboration({ info: provided, standalone = false, composeRequest, navigation, developerMode, onDeveloperMode, attachments, onAttach, onDetach, onProject, onFile, onTerminal, onCalendar }: { attachments?: LocalSource[]; onAttach?: () => void; onDetach?: (id: string) => void; onProject?: () => void; onFile?: () => void; onTerminal?: () => void; onCalendar?: () => void; info?: BackendInfo | null; standalone?: boolean; composeRequest?: { id: number; text: string }; navigation?: ReactNode; developerMode?: boolean; onDeveloperMode?: (value: boolean) => void }) {
  const [info, setInfo] = useState<BackendInfo | null>(provided || null);
  const [state, setState] = useState<DemoState | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const pending = useRef<{ body: string; id: string } | null>(null);
  const sync = useRef(new CollaborationSync());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; if (standalone) startupReady(); return () => { mounted.current = false; }; }, [standalone]);
  useEffect(() => {
    let live = true;
    if (provided) { setInfo(provided); return; }
    const access = browserAccess();
    if (access) { setInfo(access); return; }
    void backendInfo().then(value => { if (live) setInfo(value); }).catch(() => {
      if (live) setError("浏览器尚未连接本地协作服务。请使用本机生成的临时验证入口，或打开桌面客户端主窗口。");
    });
    return () => { live = false; };
  }, [provided, retry]);
  const accept = useCallback((next: DemoState) => {
    const accepted=sync.current.snapshot(next);
    if(accepted)setState(accepted);
  }, []);
  useEffect(() => {
    if (!info) return;
    let live = true, loading = false;
    const refresh = async () => {
      if (loading) return; loading = true;
      try {
        const result = await loadCollaboration(info, sync.current.sync);
        if (live) { const next=sync.current.update(result);if(next)setState(next);setError(""); }
      } catch (err) { if (live) setError(String(err)); }
      finally { loading = false; }
    };
    void refresh(); const timer = window.setInterval(() => void refresh(), 400);
    return () => { live = false; window.clearInterval(timer); };
  }, [info, retry, accept]);
  const act = async (action: DemoAction): Promise<boolean> => {
    if (!info || !state || busy) return false;
    setBusy(true); setActionError("");
    const body = { ...action, conversation: action.type === "select" ? action.id : "conversation" in action && action.conversation ? action.conversation : state.selected };
    const fingerprint = JSON.stringify(body);
    if (!pending.current || pending.current.body !== fingerprint) pending.current = { body: fingerprint, id: crypto.randomUUID() };
    try {
      const result = await rpc<{ ok: boolean; state: DemoState }>(info, "collaboration_action", { action: { ...body, request_id: pending.current.id } }, { timeoutMs: 12000 });
      if (mounted.current) accept(result.state);
      pending.current = null;
      return true;
    } catch (err) {
      if (mounted.current) setActionError(String(err) + " 请先核查会话；重试相同操作会沿用去重编号。");
      return false;
    } finally { if (mounted.current) setBusy(false); }
  };
  if (!state) return <div className="flex min-h-0 flex-1"><section className="cp-connect" aria-label="连接真实协作"><span>tebb / LOCAL AGENTS</span><h1>你的伙伴，即将就位</h1>
    <p>{error || "正在连接本地模型与协作任务服务…"}</p><button onClick={() => setRetry(value => value+1)}>重新连接</button><a href="/client.html?preview=chat">返回交互演示</a></section>{navigation && <nav className="cp-rail" aria-label="工作区快捷栏">{navigation}</nav>}</div>;
  return <ReportAssetProvider info={info}><ChatPrototype backend={info} attachments={attachments} onAttach={onAttach} onDetach={onDetach} onProject={onProject} onFile={onFile} onTerminal={onTerminal} onCalendar={onCalendar} composeRequest={composeRequest} navigation={navigation} developerMode={developerMode} onDeveloperMode={onDeveloperMode} standalone={standalone} live={{ state, act, busy, error: actionError || error }} /></ReportAssetProvider>;
}
