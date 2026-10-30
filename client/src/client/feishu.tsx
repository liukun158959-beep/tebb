import { Button } from "reend-components";

// 飞书页只转述登录接口里已有的字段。登录地址由 feishu_login 现取。

export type FeishuSnap = {
  ok?: boolean;
  installed?: boolean;
  logged_in?: boolean;
  system_logged_in?: boolean;
  connected?: boolean;
  user_name?: string;
  has_calendar?: boolean;
  has_task?: boolean;
  has_docs?: boolean;
  hint?: string;
  error?: string;
  login_error?: string;
  login_busy?: boolean;
};

export const WAITING_TEXT = "已打开浏览器。请完成授权。";

const SCOPES: { key: "has_calendar" | "has_task" | "has_docs"; label: string }[] = [
  { key: "has_calendar", label: "日历" },
  { key: "has_task", label: "待办" },
  { key: "has_docs", label: "文档" },
];

export function feishuWaiting(snap: FeishuSnap | null): boolean {
  return snap?.login_busy === true && snap.logged_in !== true;
}

export function feishuLoggedIn(snap: FeishuSnap | null): boolean {
  return snap?.logged_in === true;
}

export function missingScopes(snap: FeishuSnap | null): string[] {
  if (!feishuLoggedIn(snap) || !snap) return [];
  return SCOPES.filter((item) => snap[item.key] !== true).map((item) => item.label);
}

export function feishuNotice(snap: FeishuSnap | null): string {
  if (!snap) return "";
  if (snap.ok === false) return snap.error || snap.hint || "读取飞书登录态失败。";
  if (snap.login_error) return snap.login_error;
  if (feishuWaiting(snap)) return WAITING_TEXT;
  if (snap.system_logged_in && !snap.connected) return '检测到本机已有飞书登录，尚未连接此 tebb。连接后才能读取该账户的资料。';
  if (!feishuLoggedIn(snap)) return snap.hint || snap.error || "未登录";
  const name = snap.user_name?.trim();
  return name ? `已登录 ${name}` : "已登录";
}

export function FeishuPane(props: {
  snap: FeishuSnap | null;
  busy: boolean;
  debug: boolean;
  onLogin: () => void;
  onLogout: () => void;
}) {
  const logged = feishuLoggedIn(props.snap);
  const waiting = feishuWaiting(props.snap);
  const notice = feishuNotice(props.snap);
  const failed = props.snap?.ok === false || Boolean(props.snap?.login_error);
  const missing = missingScopes(props.snap);
  return (
    <div data-feishu data-feishu-logged={logged ? "1" : "0"} data-feishu-busy={waiting ? "1" : "0"}>
      <div className="mb-6">
        <h1 className="text-xl font-semibold">飞书</h1>
        <div className="mt-1 text-sm text-muted-foreground">每个 tebb 数据目录独立连接；系统已有登录不会自动导入日程和待办。</div>
      </div>
      {props.debug ? <div className="mb-4 text-xs text-muted-foreground">调试样本，未连接后端</div> : null}
      {notice ? (
        <p data-feishu-message className={`text-sm leading-6 ${failed ? "text-destructive" : ""}`}>
          {notice}
        </p>
      ) : null}
      {logged && missing.length > 0 ? (
        <ul className="mt-4 space-y-1 text-sm">
          {missing.map((label) => (
            <li key={label} data-feishu-missing>
              缺{label}权限
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-6" data-guide-anchor="feishu-connect" tabIndex={-1}>
        {logged ? (
          <span data-feishu-logout="">
            <Button type="button" variant="secondary" size="sm" disabled={props.busy} onClick={props.onLogout}>
              断开 tebb 连接
            </Button>
          </span>
        ) : (
          <span data-feishu-login="" data-feishu-login-disabled={props.busy || waiting ? "1" : "0"}>
            <Button
              type="button"
              variant="primary"
              size="sm"
              disabled={props.busy || waiting}
              onClick={props.onLogin}
            >
              {props.snap?.system_logged_in ? '连接本机账户' : '打开网页登录并连接'}
            </Button>
          </span>
        )}
      </div>
    </div>
  );
}
