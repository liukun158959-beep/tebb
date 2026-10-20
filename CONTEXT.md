# desk-companion

Windows 上常驻的个人助手，作者本机自用。凯尔希桌宠是入口，Atlas 是脑子。不做多用户、安装包分发或插件市场。

## 当前架构

- 主入口：client/ 的 Tauri 2 + React + TypeScript 客户端，主窗和透明 Live2D 宠物窗。
- Rust 拉起 desk_companion.local_api.server，绑定本机随机端口；前端通过带进程 token 的 WebSocket 调用白名单 RPC，并接收对话流。
- Python 的普通及知识问答任务直接使用 ConversationService，通过 Atlas 组装模型与业务工具，无需导入窗口模块。App / HeadlessApp 复用此服务；其余数据 RPC 正在按职责分批拆分。
- 模型、人设配置集中在 AssistantSettingsService；普通会话与只读记忆集中在 ChatSessionService，长期事实和单句历史编辑集中在 MemoryEditingService，手动压缩集中在 ContextCompressionService，笔记四基础入口集中在 NotebookService。Bridge 直接调用共享服务，旧桌面通过兼容方法和适用的刷新回调适配。会话修改及历史删除覆盖任务排队、执行、取消和收尾写回；压缩只预留当前会话，模型等待不占全局锁。笔记读取不等待模型、写入忙碌即拒绝，存储读改写统一跨进程保护；看板及笔记问答、汇总、导出等生命周期仍待后续拆分。
- python -m desk_companion 的 Electron / pywebview 旧入口仍保留。pet-ui/、Windows 窗口辅助模块、旧看板及资源仍有实际引用，不能当成无用文件删除。

## 已有能力与约束

- 多会话对话、模型清单与切换、思考和 token 统计、技能与临时 MCP 工具、飞书整篇文档注入。
- 当前线程去重后的全部正文参与上下文；超过输入预算才压缩，原文仍留在 memory/chat.jsonl。
- 跨会话事实由工具或用户写入 memory/facts.json。模型写入必须有当前用户原话作依据，工具没有成功回执不能声称记住。
- 飞书文档知识库使用本地向量检索和重排。笔记会话与普通对话分开，带来源引用，可导出 Markdown 或飞书文档。
- 飞书日程和待办、GitHub 状态与路线图、字幕视频学习及飞书知识库归档。
- 本地文件/文件夹读取、按知识库分组、文字工具栏、非阻塞附件窗口与全量 Agent 调试。游戏业务入口与工具已移除。
- 对话底部文字工作栏与分类 `/` 菜单；共享侧栏显示 Markdown 引用、文件提取正文与原生网页。预览不自动注入上下文；远程子网页没有本地 IPC/窗口权限。笔记目录与正文阅读分开，批量合并只在多选时出现。
- 失败要说明原因和恢复方式，不编数据、不悄悄换来源。
- 侧栏终端以 xterm 显示真实 Linux PTY。用户及 Agent 命令经 WSL + bubblewrap 隔离，不回退到宿主执行；每会话独立 /workspace，网络关闭，支持附件复制与结果预览，记录留在 memory/terminal。Agent 工作进程通过认证本地 RPC 使用同一执行服务；任务结束取消所属命令，后端失联由监督租约停止。

## 文件和提交

个人配置、凭据、记忆、模型权重、游戏数据、Live2D 素材与生成产物均按 .gitignore 留在本机。历史文档保留为决策记录，阅读时以当前代码和更新的产品条款核对。

每项改动对应一个 GitHub issue，提交写 Fixes #编号: 中文说明。，直推 main，不开 PR。验证命令见 README。
