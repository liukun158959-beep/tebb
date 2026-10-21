
export function versionTap(previous: { count: number; time: number }, now: number) {
  const count = now - previous.time > 3000 ? 1 : previous.count + 1;
  return { count: count >= 5 ? 0 : count, time: now, open: count >= 5 };
}

export function TopToolbar({ title, model, connected, busy, onNewChat, onFile, onFolder, onTerminal, sidebarOpen, sidebarAvailable, onSidebarToggle }: {
  title: string; model: string; connected: boolean; busy: boolean;
  onNewChat?: () => void;
  onFile?: () => void; onFolder?: () => void; onTerminal?: () => void;
  sidebarOpen?: boolean; sidebarAvailable?: boolean; onSidebarToggle?: () => void;
}) {
  return <header data-top-toolbar className="flex min-h-10 shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-border bg-card/85 px-4 py-1">
    <div className="flex min-w-0 items-center gap-3"><span className="h-2 w-2 shrink-0 rounded-full bg-primary" />
      <span className="text-sm font-medium">{title}</span><span className="hidden max-w-64 truncate text-xs text-muted-foreground xl:block" title={model}>{model || "未选择模型"}</span>
      <span className="text-xs text-muted-foreground">{connected ? "已连接" : "连接中"}{busy ? " · 执行中" : ""}</span>
    </div>
    <nav aria-label="顶部工具栏" className="flex items-center gap-1 text-xs">
      {onSidebarToggle && <button className="desk-menu-item" disabled={!sidebarAvailable} aria-expanded={!!sidebarOpen} aria-label={sidebarOpen ? '收起侧栏' : '打开侧栏'} onClick={onSidebarToggle}>{sidebarOpen ? '收起侧栏' : '打开侧栏'}</button>}
      {onNewChat && <button className="desk-menu-item" onClick={onNewChat}>新对话</button>}
      <button className="desk-menu-item" disabled={!connected} onClick={onTerminal}>终端</button>
      <button className="desk-menu-item" disabled={!connected} onClick={onFile}>文件</button>
      <button className="desk-menu-item" disabled={!connected} onClick={onFolder} title="选择本机项目文件夹，打开文件树与权限设置">打开文件夹</button>

    </nav>
  </header>;
}
