import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { Markdown, openableHref } from "./Markdown";
import { LoadingText } from "./loading-text";
import { rpc, type BackendInfo } from "./api";
import type { LocalSource } from "./local-sources";
import type { NoteCite } from "./note";
const ProjectPanel = lazy(() => import("./project-panel").then(module => ({ default: module.ProjectPanel })));
const TerminalPane = lazy(() => import("./terminal").then(module => ({ default: module.TerminalPane })));
const ProjectRuntimePane = lazy(() => import("./project-runtime").then(module => ({ default: module.ProjectRuntimePane })));

type Request = { kind: "project" } | { kind: "project-runtime"; environment?: string; operation?: string } | { kind: "file"; source: LocalSource } | { kind: "web"; url: string } | { kind: "quote"; quote: NoteCite } | { kind: "terminal" };
type Tab = { id: string; title: string; request: Request; text: string; total: number; next: number | null; busy: boolean; error: string; raw: boolean };
type BrowserState = { url: string; title: string; loading: boolean };
export function quoteIdentity(quote: NoteCite) { let hash = 2166136261; for (const ch of quote.context || quote.text) hash = Math.imul(hash ^ ch.codePointAt(0)!, 16777619); return `quote:${quote.doc_id}:${quote.n}:${hash >>> 0}`; }
export function previewSource(source: LocalSource) { window.dispatchEvent(new CustomEvent("desk-open-preview", { detail: { kind: "file", source } })); }
export function previewQuote(quote: NoteCite) { window.dispatchEvent(new CustomEvent("desk-open-preview", { detail: { kind: "quote", quote } })); }
export function openProject() { window.dispatchEvent(new CustomEvent("desk-open-preview", { detail: { kind: "project" } })); }
export function openProjectFolder() { window.dispatchEvent(new CustomEvent("desk-open-preview", { detail: { kind: "project", pick: true } })); }
export function openTerminal() { window.dispatchEvent(new CustomEvent("desk-open-preview", { detail: { kind: "terminal" } })); }
export function togglePreview() { window.dispatchEvent(new CustomEvent("desk-toggle-preview")); }

export function SideWorkspace(props: { children: ReactNode; info: BackendInfo | null; enabled: boolean; suspended?: boolean; debug?: boolean;
  session?: string; onTerminalDraft?: (text: string) => void; onVisibilityChange?: (value: { open: boolean; available: boolean }) => void;
  attachments: LocalSource[]; onAttach: (sources: LocalSource[]) => void; onDetach: (id: string) => void; onLink: (url: string) => void; onRequest: () => void }) {
  const [tabs, setTabs] = useState<Tab[]>([]), [active, setActive] = useState(""), [closed, setClosed] = useState(false);
  const [projectDirty, setProjectDirty] = useState(false);
  const [folderRequest, setFolderRequest] = useState(0);
  const [width, setWidth] = useState(440), [notice, setNotice] = useState("");
  const [browser, setBrowser] = useState<BrowserState>({ url: "", title: "网页", loading: false }), [address, setAddress] = useState("");
  const [webRequest, setWebRequest] = useState<{ url: string; seq: number } | null>(null);
  const box = useRef<HTMLDivElement>(null), host = useRef<HTMLDivElement>(null), alive = useRef(true), lastWidth = useRef(440);
  const tabsRef = useRef(tabs); tabsRef.current = tabs;
  const activeRef = useRef(active); activeRef.current = active;
  const browserVisible = useRef(false), generation = useRef(new Map<string, number>()), timeout = useRef<ReturnType<typeof setTimeout>>();
  const propsRef = useRef(props); propsRef.current = props;
  const current = tabs.find(t => t.id === active), visible = props.enabled && tabs.length > 0 && !closed;
  useEffect(() => { propsRef.current.onVisibilityChange?.({ open: visible, available: props.enabled && tabs.length > 0 }); }, [visible, tabs.length, props.enabled]);
  useEffect(() => {
    const toggle = () => { if(tabsRef.current.length) setClosed(value => !value); };
    window.addEventListener('desk-toggle-preview', toggle);
    return () => window.removeEventListener('desk-toggle-preview', toggle);
  }, []);
  browserVisible.current = !!visible && current?.request.kind === "web" && !props.suspended;
  const update = (id: string, patch: Partial<Tab>) => setTabs(ts => ts.map(t => t.id === id ? { ...t, ...patch } : t));
  async function read(id: string, source: LocalSource, more = false) {
    const previous = tabsRef.current.find(t => t.id === id), offset = more ? previous?.next || 0 : 0;
    const seq = (generation.current.get(id) || 0) + 1; generation.current.set(id, seq); update(id, { busy: true, error: "" });
    try {
      if (propsRef.current.debug) throw new Error("演示页不读取本地文件；请在正常客户端选择资料。");
      if (!propsRef.current.info) throw new Error("本地后端尚未连接，请重试。");
      const result = await rpc<{ ok: boolean; error?: string; source: { text: string; chars: number; next_offset: number | null } }>(propsRef.current.info, "read_local_source", { source_id: source.id, offset }, { timeoutMs: 90000 });
      if (!result.ok) throw new Error(result.error || "文件预览读取失败。");
      if (!alive.current || generation.current.get(id) !== seq) return;
      update(id, { text: (more ? previous?.text || "" : "") + result.source.text, total: result.source.chars, next: result.source.next_offset, busy: false });
    } catch (err) { if (alive.current && generation.current.get(id) === seq) update(id, { busy: false, error: String(err) }); }
  }
  useEffect(() => {
    alive.current = true;
    const open = (event: Event) => {
      const request = (event as CustomEvent<Request>).detail;
      if (!request || !["file", "web", "quote", "terminal", "project", "project-runtime"].includes(request.kind)) return;
      if (request.kind === "project" && (request as { pick?: boolean }).pick) setFolderRequest(value => value + 1);
      if (request.kind === "web" && !openableHref(request.url)) { setNotice("网页地址无效，只支持 http/https。"); return; }
      const id = request.kind === "project-runtime" ? `project-runtime:${request.environment || ""}:${request.operation || ""}` : request.kind === "project" ? "project" : request.kind === "terminal" ? "terminal" : request.kind === "web" ? "web" : request.kind === "file" ? `file:${request.source.id}` : quoteIdentity(request.quote);
      const title = request.kind === "project-runtime" ? "项目运行" : request.kind === "project" ? "项目文件" : request.kind === "terminal" ? "终端" : request.kind === "web" ? "网页" : request.kind === "file" ? request.source.name : request.quote.title;
      const exists = tabsRef.current.some(t => t.id === id);
      setTabs(ts => ts.some(t => t.id === id) ? ts : [...ts, { id, title, request, text: request.kind === "quote" ? request.quote.context || request.quote.text : "", total: 0, next: null, busy: request.kind === "file", error: "", raw: false }]);
      setActive(id); setClosed(false); setNotice(`已打开${title}，聊天输入保持可用。`); propsRef.current.onRequest();
      if (request.kind === "file" && !exists) void read(id, request.source);
      if (request.kind === "web") { setAddress(request.url); setWebRequest(r => ({ url: request.url, seq: (r?.seq || 0) + 1 })); }
    };
    window.addEventListener("desk-open-preview", open);
    return () => { alive.current = false; window.removeEventListener("desk-open-preview", open); clearTimeout(timeout.current); if (isTauri()) void invoke("side_browser_layout", { visible: false, bounds: null }).catch(() => {}); };
  }, []);
  function syncBrowser() {
    if (!isTauri()) return;
    const rect = host.current?.getBoundingClientRect();
    const bounds = rect ? { x: rect.left, y: rect.top, w: rect.width, h: rect.height } : null;
    void invoke("side_browser_layout", { visible: browserVisible.current && !!rect && rect.width > 20 && rect.height > 20, bounds }).catch(err => { if (alive.current) setNotice(String(err)); });
  }
  useEffect(() => {
    syncBrowser();
    const observer = new ResizeObserver(syncBrowser); if (box.current) observer.observe(box.current); if (host.current) observer.observe(host.current);
    window.addEventListener("resize", syncBrowser); window.addEventListener("scroll", syncBrowser, true);
    return () => { observer.disconnect(); window.removeEventListener("resize", syncBrowser); window.removeEventListener("scroll", syncBrowser, true); };
  }, [visible, active, width, props.suspended]);
  useEffect(() => {
    if (!isTauri()) return;
    let disposed = false; let off: (() => void) | undefined;
    void listen<BrowserState>("side-browser-state", event => {
      if (disposed) return;
      setBrowser(event.payload); setAddress(event.payload.url);
      if (!event.payload.loading) { clearTimeout(timeout.current); if (activeRef.current === "web") setNotice("页面加载已结束，聊天保持可用。"); }
    }).then(unlisten => { if (disposed) unlisten(); else off = unlisten; });
    return () => { disposed = true; off?.(); };
  }, []);
  useEffect(() => {
    if (!webRequest) return;
    let stale = false;
    setBrowser(b => ({ ...b, url: webRequest.url, loading: true }));
    setNotice("正在打开网页，聊天保持可用。");
    clearTimeout(timeout.current); timeout.current = setTimeout(() => { if (!stale) setNotice("网页仍未完成加载，可停止或刷新；聊天保持可用。"); }, 45000);
    if (!isTauri()) { setBrowser(b => ({ ...b, loading: false })); setNotice("内置浏览器需在桌面客户端中使用。"); clearTimeout(timeout.current); return; }
    void invoke("open_side_browser", { url: webRequest.url }).then(() => { if (!stale) syncBrowser(); }).catch(err => { if (!stale) { setBrowser(b => ({ ...b, loading: false })); setNotice(String(err)); clearTimeout(timeout.current); } });
    return () => { stale = true; clearTimeout(timeout.current); };
  }, [webRequest]);
  async function browserAction(action: string) {
    if (!isTauri()) { setNotice("请在桌面客户端中浏览网页。"); return; }
    try {
      await invoke("side_browser_action", { action });
      if (action === "stop") { clearTimeout(timeout.current); setBrowser(b => ({ ...b, loading: false })); setNotice("已停止网页加载。"); }
      else setNotice(action === "reload" ? "已请求刷新网页。" : action === "back" ? "已请求后退；没有上一页时保留当前页。" : "已请求前进；没有下一页时保留当前页。");
    }
    catch (err) { setNotice(String(err)); }
  }
  function closeTab(id: string) {
    if (id === "project" && projectDirty) { setNotice("项目文件有未保存修改，请先保存或撤销后关闭。"); return; }
    if (id === "project") setFolderRequest(0);
    generation.current.set(id, (generation.current.get(id) || 0) + 1);
    const remaining = tabs.filter(t => t.id !== id); setTabs(remaining); if (active === id) setActive(remaining[remaining.length - 1]?.id || "");
    if (id === "web") { setWebRequest(null); clearTimeout(timeout.current); if (isTauri()) void invoke("side_browser_action", { action: "close" }).catch(err => setNotice(String(err))); }
    setNotice("标签已关闭，聊天内容已保留。");
  }
  const web = current?.request.kind === "web", file = current?.request.kind === "file" ? current.request.source : null;
  const included = file && props.attachments.some(s => s.id === file.id);
  const markdown = current?.request.kind === "quote" || !!file && /\.(md|markdown)$/i.test(file.name);
  return <div ref={box} className={`side-workspace ${visible ? "has-preview" : ""}`} style={{ "--preview-width": `${width}px` } as React.CSSProperties} data-side-workspace>
    <div className="workspace-conversation">{props.children}</div>
    <><button hidden={!visible} style={!visible ? { display: "none" } : undefined} type="button" className="workspace-resize" aria-label="拖动或按左右方向键调整侧栏宽度" onKeyDown={event => { if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); setWidth(w => Math.min(680, Math.max(280, w + (event.key === "ArrowLeft" ? 20 : -20)))); } }} onPointerDown={event => {
      event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
      const handle = event.currentTarget, move = (e: PointerEvent) => { const rect = box.current!.getBoundingClientRect(); setWidth(Math.min(Math.max(280, rect.right - e.clientX), Math.min(680, Math.max(280, rect.width - 386)))); };
      const end = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", end); handle.removeEventListener("pointercancel", end); };
      handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", end); handle.addEventListener("pointercancel", end);
    }} />
    <aside hidden={!visible} style={!visible ? { display: "none" } : undefined} className="workspace-preview" aria-label="侧边工作区" onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); setClosed(true); } }}>
      <header className="workspace-header"><span>侧边工作区</span><div><button className="desk-menu-item" onClick={() => { if (width >= 600) setWidth(lastWidth.current); else { lastWidth.current = width; setWidth(640); } }}>{width >= 600 ? "恢复宽度" : "放大"}</button><button className="desk-menu-item" onClick={() => { setClosed(true); setNotice("侧栏已收起，内容保留。"); }}>收起</button></div></header>
      <div className="workspace-tabs" aria-label="已打开资料">{tabs.map(t => <div key={t.id} className={t.id === active ? "active" : ""}><button className="desk-menu-item" aria-pressed={t.id === active} onClick={() => setActive(t.id)}>{t.title}</button><button className="desk-menu-item" aria-label={`关闭 ${t.title}`} onClick={() => closeTab(t.id)}>×</button></div>)}</div>
      {tabs.some(t => t.request.kind === "project") && <div hidden={current?.request.kind !== "project"} style={{ display: current?.request.kind === "project" ? "flex" : "none", flex: 1, minHeight: 0 }}><Suspense fallback={<p>正在打开项目文件…</p>}><ProjectPanel info={props.info} folderRequest={folderRequest} onQuote={text => props.onTerminalDraft?.(text)} onDirty={setProjectDirty} /></Suspense></div>}
      {tabs.some(t => t.request.kind === "terminal") && <div hidden={current?.request.kind !== "terminal"} style={{ display: current?.request.kind === "terminal" ? "flex" : "none", flex: 1, minHeight: 0 }}><Suspense fallback={<p className="terminal-notice" role="status"><LoadingText text="正在打开终端…" /></p>}><TerminalPane info={props.info} session={props.session || ""} debug={props.debug} attachments={props.attachments} onPreview={previewSource} onDraft={text => props.onTerminalDraft?.(text)} /></Suspense></div>}
      {current?.request.kind === "project-runtime" ? <Suspense fallback={<p role="status">正在打开项目运行…</p>}><ProjectRuntimePane info={props.info} debug={props.debug} environment={current.request.environment} operation={current.request.operation} /></Suspense> : current?.request.kind === "project" ? null : current?.request.kind === "terminal" ? null : web ? <><div className="workspace-browserbar"><button className="desk-menu-item" onClick={() => void browserAction("back")} aria-label="后退">←</button><button className="desk-menu-item" onClick={() => void browserAction("forward")} aria-label="前进">→</button><button className="desk-menu-item" onClick={() => void browserAction(browser.loading ? "stop" : "reload")}>{browser.loading ? "停止" : "刷新"}</button><input aria-label="网页地址" value={address} onChange={e => setAddress(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); const url = openableHref(address); if (url) setWebRequest(r => ({ url, seq: (r?.seq || 0) + 1 })); else setNotice("地址无效，只支持 http/https。"); } }} /></div><div className="workspace-meta" role="status">{browser.loading ? <LoadingText text="正在打开网页…" /> : browser.title}</div><div ref={host} className="workspace-browser-host">{!isTauri() && <p>网页浏览在桌面客户端侧栏中使用。</p>}</div><div className="workspace-footer"><span>网页浏览</span><button className="desk-menu-item" disabled={!openableHref(browser.url)} onClick={() => { props.onLink(browser.url); setNotice("网页链接已加入草稿，尚未发送。"); }}>引用链接到草稿</button></div></> : current && <>
        <div className="workspace-file-head"><div>{current.title}<span className="workspace-meta">{file ? `已预览 ${current.text.length}/${current.total || file.chars} 字${/\.(pdf|docx|xlsx|pptx)$/i.test(file.name) ? " · 提取正文" : ""}` : current.request.kind === "quote" ? `引用 [${current.request.quote.n}] · ${current.request.quote.doc} · 来源片段` : "来源片段"}</span></div><div><button className="desk-menu-item" aria-pressed={!current.raw} onClick={() => update(current.id, { raw: false })}>阅读</button><button className="desk-menu-item" aria-pressed={current.raw} onClick={() => update(current.id, { raw: true })}>源码</button></div></div>
        <div className="workspace-file-body">{current.error && <div role="alert" className="text-destructive">{current.error}<button className="desk-menu-item" onClick={() => file && void read(current.id, file)}>重试</button></div>}{current.busy && <p role="status"><LoadingText text="正在读取文件…" /><button className="desk-menu-item" onClick={() => { generation.current.set(current.id, (generation.current.get(current.id) || 0) + 1); update(current.id, { busy: false, error: "已停止等待，已读取内容保留。可重新读取。" }); }}>停止等待</button></p>}{markdown && !current.raw ? <Markdown text={current.text} /> : <pre>{current.text}</pre>}{current.next !== null && file && <button className="desk-menu-item" disabled={current.busy} onClick={() => void read(current.id, file, true)}>继续读取</button>}{file?.kind === "folder" && file.files?.map(child => <button key={child.id} className="workspace-file-link" onClick={() => previewSource(child)}>{child.relative || child.name}</button>)}</div>
        {file && <div className="workspace-footer"><span>{included ? "已加入本轮资料" : "仅预览"}</span><button className="desk-menu-item" onClick={() => included ? props.onDetach(file.id) : props.onAttach([file])}>{included ? "移出本轮资料" : "加入本轮资料"}</button></div>}
      </>}
      {notice && <p className="workspace-notice" role="status">{notice}</p>}
    </aside></>
  </div>;
}
