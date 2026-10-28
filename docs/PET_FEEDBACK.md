# 桌宠过程气泡（#153）

单击宠物上方的后台执行过程气泡，调用既有 PetFeedback.dismiss，关闭当前根任务整条链的提示；后续子任务和轮询不立即重弹。后台任务继续，新的独立任务仍可提示。选中文字用于复制时不触发关闭。普通动作气泡、错误恢复提示和浮动聊天窗保持原有行为。

AI 验证优先复用 `client/tests/pet-chat.test.cjs` 的根任务屏蔽回归。真实浏览器开发页 `/` 的 `window.__petDebug.feedback(progress,runs)` 可注入隔离展示快照：先传空列表，再传新 running 根任务，单击 `#bubble`，随后注入同根子任务应继续隐藏，另一新根应显示。该入口仅开发模式存在，不派发任务、不取消任务、不连接生产 RPC。

2026-10-28：真实 Edge 已验证上述点击、同根屏蔽与下一独立任务显示。历史日程及桌宠批次前端完整172项、类型和生产构建通过。验收材料见 `E:/Learn_Project/output/search-orchestration-2026-10-28/pet-ui.log`。
