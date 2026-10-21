import { useState, type ReactNode } from "react";
import type { DemoAgent } from "./chat-prototype-state";

export const DEVELOPER_KEY = "tebb.developer-mode";
export function readDeveloperMode() {
  try { return localStorage.getItem(DEVELOPER_KEY) === "true"; } catch { return false; }
}
export function saveDeveloperMode(value: boolean) {
  try { localStorage.setItem(DEVELOPER_KEY, String(value)); return ""; }
  catch { return "开发者模式偏好未能保存，重启后会恢复默认。"; }
}

export const AVATAR_NAMES = { spark: "小光团", fox: "探险狐", cat: "墨水猫", owl: "夜读鸮" };
export function CompanionAvatar({ agent, group = false }: { agent?: DemoAgent; group?: boolean }) {
  const defaults: Record<string, string> = { tebb: "spark", research: "fox", writer: "cat" };
  const avatar = agent?.avatar || defaults[agent?.id || ""] || "owl";
  return <span className={`cp-avatar ${group ? "cp-group-avatar" : `cp-avatar-${avatar}`}`} aria-hidden="true">
    <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      {group ? <><circle cx="18" cy="19" r="8" /><circle cx="33" cy="21" r="6" /><path d="M5 39c0-14 25-14 25 0M31 31c8 0 12 3 12 8" /></> :
        avatar === "spark" ? <><path d="m24 5 5 9 10 3-4 10 2 10-11 1-9 5-5-9-9-5 5-9 1-10 11 1Z" fill="currentColor" opacity=".2" /><path d="M14 20c3-10 18-9 20 2 2 13-22 17-22 3" /><path d="m18 23 1 2m10-3 1 2m-9 5q4 3 7-1M34 9l2-3m4 8 3-1" /></> :
        avatar === "fox" ? <><path d="m7 11 13 8h8l13-8-3 22-14 10-14-10Z" fill="currentColor" opacity=".18" /><path d="m7 11 13 8h8l13-8-3 22-14 10-14-10Z" /><path d="m12 24 9 7-8 1m23-8-9 7 8 1m-19-8 2 1m12-1-2 1m-6 10h4l-2 3Z" /></> :
        avatar === "cat" ? <><path d="m9 8 12 9h7l11-9 1 23c-3 14-29 14-32 0Z" fill="currentColor" opacity=".18" /><path d="m9 8 12 9h7l11-9 1 23c-3 14-29 14-32 0Z" /><path d="m15 26 5-1m10 0 4 1m-12 6q2 4 5 0m-14-2-9 1m10 3-8 3m30-7 8 1m-9 3 7 3M27 12l3-5" /></> :
        <><path d="m8 10 8 6h16l8-6v19c0 17-32 17-32 0Z" fill="currentColor" opacity=".18" /><path d="m8 10 8 6h16l8-6v19c0 17-32 17-32 0Z" /><circle cx="17" cy="26" r="6" /><circle cx="31" cy="26" r="6" /><path d="M17 25v2m14-2v2m-9 6 2 3 2-3M16 39h16" /></>}
    </svg>
  </span>;
}

const ICON_PATHS: Record<string, ReactNode> = {
  new_group: <><circle cx="8" cy="7" r="3"/><circle cx="16" cy="8" r="3"/><path d="M2 20v-3a6 6 0 0 1 12 0v3m1-7h5m-2.5-2.5v5M16 20h6"/></>,
  folder: <path d="M3 6h7l2 3h9v11H3Zm0 3V4h7l2 2h8v3"/>,
  new_project: <><path d="M3 6h7l2 3h9v11H3Zm0 3V4h7l2 2h8v3"/><path d="M12 12v5m-2.5-2.5h5"/></>,
  more: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
  new_chat: <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M12 8v8m-4-4h8" /></>,
  expand: <><path d="M14 4h6v6m0-6L10 14M9 4H4v16h16v-5" /></>,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  pin: <><path d="M8 3h8M9 3v7l-3 4v2h12v-2l-3-4V3M12 16v5" /></>,
  tasks: <><rect x="5" y="4" width="14" height="17" rx="3" /><path d="m8 11 2 2 5-5M9 17h6" /></>,
  members: <><circle cx="9" cy="8" r="4" /><path d="M2 21v-3a7 7 0 0 1 14 0v3m1-17a4 4 0 0 1 0 8m2 2q4 1 4 7" /></>,
  inbox: <><path d="M3 9 6 3h12l3 6v12H3Z" /><path d="M3 13h5l2 3h4l2-3h5" /></>,
  chat: <path d="M3 4h18v13H9l-6 4Z" />,
  memory: <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 8h6m-6 4h6m-6 4h4" /></>,
  knowledge: <><path d="M12 6Q6 2 2 5v15q5-3 10 0 5-3 10 0V5q-5-3-10 1v14" /></>,
  board: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M3 9h18m-12 0v12" /></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2"/></>,
  notes: <><path d="M6 3h13v18H6ZM3 7h6M3 12h6M3 17h6m3-9h4m-4 5h4"/></>,
  feishu: <path d="m3 4 10 6 8-6-6 12-5 5-3-9Z" />,
  automation: <><circle cx="12" cy="12" r="9" /><path d="M12 6v7l4 2" /></>,
  video: <><rect x="2" y="5" width="20" height="15" rx="3" /><path d="m10 9 6 4-6 3Z" /></>,
  settings: <><circle cx="12" cy="12" r="4" /><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2" /></>,
  developer: <><path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18" /></>,
  terminal: <><rect x="2" y="4" width="20" height="16" rx="3" /><path d="m6 9 3 3-3 3m6 0h5" /></>,
  theme: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2" /></>,
  pet: <><path d="m5 4 6 5h3l5-5 1 12c-2 9-18 9-18 0Z" /><path d="M8 14h1m6 0h1m-6 4h4" /></>,
};

export function AppIcon({ name }: { name: string }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICON_PATHS[name] || ICON_PATHS.tasks}</svg>;
}

export function RailButton({ icon, label, active, onClick }: { icon: string; label: string; active?: boolean; onClick: () => void }) {
  const [tipY, setTipY] = useState(0);
  return <button onMouseEnter={event => setTipY(event.currentTarget.getBoundingClientRect().top + 17)} onFocus={event => setTipY(event.currentTarget.getBoundingClientRect().top + 17)} type="button" className={`cp-rail-button ${active ? "is-active" : ""}`} aria-label={label} aria-pressed={!!active} onClick={onClick}>
    <AppIcon name={icon} />
    <span className="cp-rail-label">{label}</span>
    <span className="cp-rail-tooltip" style={{ top: tipY }} aria-hidden="true">{label}</span>
  </button>;
}
