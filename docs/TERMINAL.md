# 终端与本地沙箱

顶部文字菜单「终端」在对话侧栏打开命令工作区，和文件、网页共用侧栏。组件按需加载。

公开 GitHub 项目的拉取、联网装依赖及持续运行走[官方 Docker 项目环境](PROJECT_RUNTIME.md)，可从终端工具栏进入“项目运行”查看状态、日志和预览。下文 WSL/bubblewrap 说明仅对应原有离线命令终端；不把两种环境或历史记录混为同一后端。

终端采用 VS Code 式紧凑面板：顶部标题工具栏、最近执行记录标签、命令草稿、实时 xterm 输出和状态栏。`Alt+N` 打开命令草稿，标签支持方向键/Home/End 切换；“新命令”不会立即执行或创建后台 shell，切换记录保留输入草稿，后台刷新不会将显式打开的新命令草稿切回旧记录。执行记录按钮展开完整历史，最多显示既有后端返回的 100 条记录。

清屏仅清空当前 xterm 视图，不删除后端命令或输出；复制优先取选中文字，否则复制当前已加载的终端缓冲区，不宣称包含全部历史输出。停止仍以原命令编号请求取消、等待实际状态回执。浅深色通过产品主题映射到 xterm，布局适配窄侧栏。每条命令的新 Bash 与时限边界仍按下述规则，不把记录标签视为持久 shell 会话。

## 使用

1. 沙箱自检通过后，输入 Bash 命令，点击「执行」或按 Ctrl+Enter。可以使用 `python3 -c "print(1 + 1)"`；运行 `bash --noprofile --norc` 可启动交互 Shell，时限仍生效。
2. 输出实时显示，支持中文、ANSI 色彩、终端输入和尺寸调整。「停止命令」等待实际退出回执；收起侧栏不停止执行。历史区分手动与 Agent 命令，可查看其他会话及退出码。
3. 「导入本轮附件」复制已选择资料到新的 `import-*` 目录，保留文件名及文件夹内部层级，不修改原文件。只复制附件读取器已经支持并登记的文件；隐藏文件和不支持的格式不会自动复制。
4. 「工作文件」列出结果；停止命令后可点击文件在侧栏预览，沿用附件的格式与读取限制。「解读输出」把命令、状态和部分输出加入聊天草稿，由用户发送。

每会话独立持久 `/workspace`。每条命令使用新的 Bash 环境，`cd` 或环境变量不延续到下一条；目录输入框可指定 `/workspace` 下的已有目录。

## 连接与边界

Windows 使用本机 WSL 发行版（默认 Ubuntu）中的 `/usr/bin/python3` 和 `/usr/bin/bwrap`。需支持非特权 user namespace 和 bubblewrap `--disable-userns`。展开状态行可修改发行版、重新检查。缺少环境或自检失败时拒绝执行，不自动安装系统组件，不回退到宿主执行。

沙箱使用独立用户、进程、IPC、网络、UTS 与挂载命名空间；禁用进一步创建 user namespace，清空环境，移除 capabilities。根目录只读，只读挂载 WSL `/usr` 运行库，可写当前会话 `/workspace` 和临时 `/tmp`。Windows 磁盘、个人 home、桌宠 API token、模型密钥和 WSL 互操作入口不可见，网络关闭。本版支持 Linux 命令，不能执行宿主 PowerShell 或访问未导入的文件。

用户时限 15～300 秒，Agent 最多 60 秒；同时最多 3 条，每会话最多 1 条。每进程地址空间 256 MiB、CPU 时间 60 秒、进程数限制 64、单文件写入限制 32 MiB；`/tmp` 64 MiB。工作目录每秒检查，超过 128 MiB 或 10000 个条目时停止，可能短暂超过阈值；这些限制不是容器总内存或磁盘硬配额。

沙箱使用 WSL 的 Linux 内核，不等同于独立虚拟机；隔离强度取决于具体策略及内核。实现依据 [bubblewrap 官方说明](https://github.com/containers/bubblewrap) 与 [WSL 安全说明](https://github.com/microsoft/WSL/blob/master/doc/docs/technical-documentation/security.md)。

## 记录与停止

每条命令输出最多 4 MiB，达到限制停止并保留此前输出。侧栏按原始字节分页重读；Agent 初次回执只附前 16000 字符，可以调用 `read_terminal_output` 继续按字节读取。输入按序发送，结束清除输入控制文件。

侧栏显示最近 100 条记录，历史原始记录保留在磁盘；已知命令编号仍可由工具分页读取。

`execute_command` 禁用自动重试，UI 提交带唯一请求编号，重试同一请求不重复执行。任务恢复需核对原记录。工具调用同时保留在任务时间线和 LLM 调试记录中。

停止按钮、任务取消/超时/完成和正常关闭客户端会停止对应命令与后代；后端异常退出时，Linux 监督进程在最后一次租约更新约 8 秒后停止。重启后端不会重放命令。

工作文件和记录位于用户数据目录 `memory/terminal/`，源码默认在仓库下，发布版在 `%LOCALAPPDATA%/DeskCompanion`。工作目录满时，停止命令后可在对应 `workspaces` 子目录清理结果。本次源码已验证开发机已有 Ubuntu 环境；公开安装包需另行安装这些可选依赖。

## 验证

AI 可直接复用认证 RPC：`terminal_status`（自检）、`terminal_start`（明确 command/session/cwd/timeout/request_id）、`terminal_list`（按 request_id 查重）、`terminal_read`（按 job_id/offset 读实际输出）、`terminal_input`（输入/尺寸）和 `terminal_cancel`（按 job_id 停止）。模型使用现有 `execute_command` / `read_terminal_output`，将 task_id、job_id、输入命令、原始输出、退出码关联保存。验证真实模型任务时，必须确认它实际调用工具、取得真实执行结果并正确报告失败/超时，不能以页面演示或假 RPC 成功代替。

UI 交互用例 `client/tests/terminal.test.cjs` 覆盖真实组件的输入队列、停止回执、沙箱拒绝、项目授权、只生成解读草稿，以及清屏/复制/新草稿不触发执行或取消。真实模型与真实沙箱专项应分别报告通过、失败、跳过或未执行。

```powershell
$env:DESK_TEST_SANDBOX='1'
python -m unittest tests.test_terminal -v
python -m unittest tests.test_local_api tests.test_tasks -v
cd client
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

真实测试覆盖目录、网络及环境隔离，只读根和运行库，中文交互输入，WSL 符号链接拒绝，时限、退出码、输出上限，停止后后台子进程回收。未设置测试开关时跳过真实沙箱组，普通单元测试仍运行。
