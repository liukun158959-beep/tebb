import { useEffect, useRef, useState } from "react";
import manifest from "../../../desk_companion/ui/release.json";
import poster from "../../../desk_companion/ui/release-v0.4.1.png";
import { rpc, type BackendInfo } from "./api";
import { MdLink, Markdown } from "./Markdown";
import { localDay, readReleasePreferences, saveReleasePreferences, shouldShowRelease, type ReleasePreferences } from "./release-preferences";

export const CURRENT_RELEASE = manifest;
const releases = "https://github.com/liukun158959-beep/tebb/releases";
export const CURRENT_RELEASE_URL = `${releases}/tag/${manifest.tag}`;

export function useReleaseIntro(ready: boolean) {
  const [open, setOpen] = useState(false);
  const [prefs, setPrefs] = useState<ReleasePreferences>(readReleasePreferences);
  const [preferenceError, setPreferenceError] = useState("");
  const started = useRef(false);
  useEffect(() => {
    if (!ready || started.current) return;
    started.current = true;
    if (shouldShowRelease(prefs, manifest.version)) setOpen(true);
  }, [ready, prefs]);
  useEffect(() => {
    if (!open || !ready) return;
    const day = localDay();
    if (prefs.seen[manifest.version] === day) return;
    const next = { ...prefs, seen: { ...prefs.seen, [manifest.version]: day } };
    setPrefs(next); setPreferenceError(saveReleasePreferences(next));
  }, [open, ready, prefs]);
  function setDaily(daily: boolean) {
    const next = { ...prefs, daily };
    setPrefs(next); setPreferenceError(saveReleasePreferences(next));
  }
  return { open, show: () => setOpen(true), close: () => setOpen(false), daily: prefs.daily, setDaily, preferenceError };
}

export function ReleaseIntro(props: { daily: boolean; onDaily: (daily: boolean) => void; onClose: () => void; error?: string }) {
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    section.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => previous?.focus?.();
  }, []);
  const button = "desk-btn desk-btn-lg";
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4 backdrop-blur-sm" data-release-intro
    onMouseDown={ev => { if (ev.target === ev.currentTarget) props.onClose(); }}>
    <section ref={section} role="dialog" aria-modal="true" aria-labelledby="release-title"
      className="flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-border bg-background text-foreground shadow-2xl"
      onKeyDown={ev => {
        if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); props.onClose(); }
        if (ev.key !== "Tab") return;
        const elements = Array.from(ev.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), input, a[href]"));
        const first = elements[0], last = elements[elements.length - 1];
        if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last?.focus(); }
        else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first?.focus(); }
      }}>
      <header className="flex items-center justify-between gap-3 border-b border-border px-6 py-4">
        <div><p className="text-xs text-primary">版本介绍 · v{manifest.version} · {manifest.date}</p><h1 id="release-title" className="mt-1 text-xl font-semibold">{manifest.title}</h1></div>
        <button className={button} aria-label="关闭版本介绍" onClick={props.onClose}>关闭</button>
      </header>
      <div className="overflow-y-auto">
        <img src={poster} alt={`tebb v${manifest.version} 概念图：事项日程、伙伴协作与可视化成果`} className="block aspect-video w-full object-contain" />
        <div className="px-6 py-5"><p className="mb-4 font-medium text-primary">{manifest.summary}</p>
          <ul className="grid gap-4 sm:grid-cols-2">{manifest.highlights.map(item => <li key={item.title}>
            <h2 className="text-sm font-semibold">{item.title}</h2><p className="mt-1 text-sm leading-6 text-muted-foreground">{item.description}</p>
          </li>)}</ul>
        </div>
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-6 py-4">
        <label className="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={props.daily} onChange={ev => props.onDaily(ev.target.checked)} />每日只跳出一次</label>
        <div className="flex items-center gap-4 text-sm"><MdLink href={CURRENT_RELEASE_URL}>完整更新日志</MdLink><button className={`${button} desk-btn-solid`} onClick={props.onClose}>开始使用</button></div>
        {props.error && <p role="status" className="w-full text-xs text-muted-foreground">{props.error}</p>}
      </footer>
    </section>
  </div>;
}

type UpdateResult = { ok: boolean; current_version: string; latest_version?: string; status?: string; title?: string; published_at?: string; notes?: string; error?: string; release_url: string };

export function ReleaseSettings({ info, onShowIntro }: { info: BackendInfo | null; onShowIntro: () => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UpdateResult | null>(null);
  async function check() {
    if (!info || busy) return;
    setBusy(true); setResult(null);
    try { setResult(await rpc<UpdateResult>(info, "check_updates")); }
    catch { setResult({ ok: false, current_version: manifest.version, error: "暂时无法检查更新，请稍后重试。", release_url: releases + "/latest" }); }
    finally { setBusy(false); }
  }
  return <section className="mt-6 flex flex-col gap-3 rounded-xl border border-border bg-card/60 p-5" data-release-settings>
    <h2 className="text-sm font-semibold text-primary">版本与更新</h2><p className="text-sm">当前版本 v{manifest.version}</p>
    <div className="flex flex-wrap gap-3"><button className="desk-btn desk-btn-lg" onClick={onShowIntro}>查看版本介绍</button>
      <button className="desk-btn desk-btn-lg desk-btn-solid" disabled={!info || busy} onClick={() => void check()}>{busy ? "正在检查…" : "检查更新"}</button></div>
    {result && <div role="status" aria-live="polite" className="space-y-3 text-sm">
      {result.ok ? <><p>{result.status === "available" ? `发现新版本 v${result.latest_version}` : result.status === "ahead" ? `当前版本高于最新正式版 v${result.latest_version}` : "当前已是最新正式版本。"}</p>
        {result.published_at && <p className="text-xs text-muted-foreground">发布时间：{new Date(result.published_at).toLocaleString()}</p>}
        {result.notes && <details><summary className="cursor-pointer text-primary">查看更新说明</summary><div className="mt-3 max-h-64 overflow-y-auto"><Markdown text={result.notes} /></div></details>}
      </> : <p className="text-destructive">{result.error}</p>}
      <MdLink href={result.release_url}>打开 GitHub 下载页面</MdLink>
    </div>}
    <p className="text-xs text-muted-foreground">下载并解压新版便携包，关闭旧程序后运行新版。个人配置和记录保留在原用户数据目录。</p>
  </section>;
}
