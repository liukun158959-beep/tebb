# Agent 调试窗口

连续点击左侧 tebb. 下方的版本号5次切换开发者模式，再从顶部工具栏打开 Agent 调试。Esc、关闭按钮或点击遮罩可关闭。关闭窗口不会取消后台任务。

## 能查看什么

- 每次实际发送的完整消息：system / developer / user / assistant / tool，以及工具定义、采样参数和其它 JSON 字段。
- 模型实际返回的正文、工具请求、服务提供的 reasoning_content、用量与原始响应。SSE 增量合并成卡片，同时保留原始事件流；不能显示服务未返回的内部推理。
- 相邻调用的完整消息与参数差异，便于观察工具结果、历史或系统规则如何改变上下文。
- 所属任务、来源与会话，HTTP 状态、首字节时间、总耗时、错误或中断状态。
- 「耗时分析」展示排队、进程/上下文准备、每次模型调用、工具执行和收尾；最耗时阶段高亮。伙伴聊天关联自己的工作队列，直接读取任务快照，不再误查普通任务队列。
- 模型请求进一步展示 DNS/TCP、TLS、请求发送、等待响应头、等待首段可见文字及后续输出。任务层与 HTTP 层互相包含，不能相加；DNS与TCP合并测量，无法从客户端分解上游网络、中转和服务端内部耗时。缺失测量显示未测得，旧记录不补造阶段。
- 最近100条已结束请求的P50/P95/P99、失败率和超时数量；包含失败/超时，不只保留成功样本。P99至少100个对应测量值，P95至少20个；首段文字只统计成功且测得的调用，样本不足不展示百分位。
- Agent 实际执行的工具参数、结果和任务时间线。视频等专用流程的手工事件可能只提供状态，实际送给模型的数据仍在请求载荷中。
- 按来源与模型/任务/会话筛选、加载更早记录、导出所选调用 JSON，以及暂停/恢复后续记录。

“协助解读”先提供消息角色与字符组成说明。点击“请模型协助解读”才把所选调用的完整请求和返回交给当前模型，创建单独的本地只读任务；不执行载荷中的指令，不发送飞书消息。可输入具体问题、查看流式答案并停止解读。它会计入实际模型用量，过大的载荷仍受模型上下文容量限制，不静默截断后冒充全量。

## 无 GUI 的对话服务验收

可供 AI 直接调用的离线入口，在仓库根目录执行：

```powershell
python -m unittest discover -s tests -p test_conversation.py -v
python -m unittest discover -s tests -p test_local_api.py -v
python -m unittest discover -s tests -p test_tasks.py -v
```

`test_conversation.py` 使用临时数据目录、脚本模型和模拟的检索片段；子进程实际运行 `task_worker` / Atlas 工具循环，同时拒绝导入 App、HeadlessApp、显示器、窗口、托盘及 WebView 模块。覆盖冷启动/预热、普通与知识任务、流式与工具事件、历史/用量、继续任务不重复写入、资料与思考隔离、旧桌面不重复记录，以及思考回调/知识检索/MCP 回收异常后的状态恢复。测试不会访问真实模型、下载权重或写入外部系统。

共享入口是 `ConversationService.for_session(session_id)` 及其 `run_chat(...)`。输入包含正文、点选、采样、模型 ID、知识模式和恢复说明；回调提供正文、思考、状态及检索过程，返回最终正文，异常交给现有任务层记录。会话历史和用量继续使用原有存储接口，任务状态和错误继续通过现有任务查询接口获取。无需增加网络调试服务或绕过权限。

预期：离线测试全部通过；后台任务不改变桌面选中的会话或窗口配置；模型或检索失败时不写入成功回复，临时工具清理失败时仍释放忙碌状态和回调。若失败，先看 unittest 的具体断言与子进程 stderr：`GUI import forbidden` 表示新增了窗口依赖；缺少 result 或出现 failure 则检查本轮异常、采样输入、工具回执及模型记录。

已独立的范围包括普通/知识任务、共享对话业务及模型/人设配置；本地 API 的其他数据 RPC 仍通过 HeadlessApp 继承兼容，不代表整个后端已无 GUI 依赖。GUI 实机、真实模型和完整发布包验收单独报告。

## 记录范围与验证

记录从安装此功能后的新调用开始，默认开启；无法还原旧任务未保存的完整载荷。当前 Atlas 使用同步 OpenAI 兼容 SDK，记录点位于 HTTPX 实际发送边界，覆盖桌面/飞书任务、视频分段与格式校正、日报、上下文压缩及子 Agent 的模型请求。主后端也记录笔记问答与其它直接模型调用。API 鉴权头、Cookie 和 URL 查询参数不进入记录；消息正文保持原样。

外部 MCP 服务器内部自行调用的模型不经过桌宠 SDK，需要由该服务器提供追踪；本窗口可查看 Agent 发起的工具调用及返回。

记录保存在用户数据目录的 `memory/agent_debug/`，不自动上传。索引与载荷分开保存，列表不加载全部正文；只有选择某次调用时才读取完整内容，折叠只控制显示。流式内容逐段写入，即使 worker 被终止也能查看已接收部分，并标为中断。暂停只影响后续请求，已开始的调用仍收完返回。正文、工具 Schema、函数参数和返回内容均不设置展示截断。

测试使用真实 OpenAI SDK 配合本地 MockTransport，验证长上下文末尾保留、流式中文、压缩响应、工具参数增量、用量、正常结束与中断、凭证头排除和记录开关；界面测试验证第五次点击、全量载荷显示、请求差异和按需解读。浏览器样本预览不伪装成真实调用。

## 无 GUI 的配置服务验收

在仓库根目录执行：

```powershell
python -m unittest discover -s tests -p test_assistant_settings.py -v
python -m unittest tests.test_local_api.LocalApiTests.test_settings_rpc_calls_independent_service_and_preserves_contracts -v
python -m unittest tests.test_local_api.LocalApiTests.test_legacy_save_rpc_preserves_invalid_config_and_reports_saved_initialization_failure -v
python -m unittest discover -s tests -p test_model_network.py -v
python -m unittest discover -s tests -p test_persona_migration.py -v
```

共享入口是 `AssistantSettingsService(conversation)`；传入已有 `ConversationService`，其 `state` 是配置的唯一内存来源。新建隔离数据时可先设置临时 `DESK_COMPANION_DATA_DIR`，调用 `state.load_state(0, 0)` 并构造对话服务；不要使用会把保存变成 no-op 的 worker `for_session` 入口来修改配置。

服务提供 `load_persona`、`save_persona(payload)`、`load_model`、`load_models`、`save_model_entry(payload)`、`delete_model_entry(model_id)`、`use_model(model_id)`、`save_model(payload)` 和 `test_model(payload)`。输入与原 RPC 一致：人设包含 persona/max_steps/nudge_enabled；模型包含 base_url/model，按需传入 api_key、id 或 copy_key_from。旧模型保存可附输入/输出价格；连接测试不保存配置。返回既有 `ok`、错误或公开字段，模型 Key 只展示 has_key。

测试用临时用户目录、假 Agent 与真实 OpenAI SDK 的 MockTransport，拒绝真实模型请求；覆盖参数校验、保存后重建/绑定、忙碌限制、Key 复用及隐藏、20 秒连接测试策略、失败时序与客户端回收。子进程在导入服务前拦截 App、HeadlessApp、Bridge、窗口、托盘和 WebView，实际运行配置操作；WebSocket 测试则经过真实本地认证和双层 `ok` 包装，并让旧 App 配置方法报错，确认 RPC 已直接使用服务。

`save_model` 在写入前完成连接参数和价格校验；价格必须成对填写或都留空，填写时须为有限的非负数字，拒绝布尔值、NaN 和无穷大。输入无效时，配置文件、内存价格和 Agent 均不改变。原有注释、其它环境变量和空 Key 复用规则保持兼容。

保存结果通过 `ok`、`error` / `message` 及 `save_state` 判断：`not_saved` 表示校验拒绝、未开始写入；`unconfirmed` 表示连接配置写入失败、文件结果未确认；`partial` 表示连接已写入但价格持久化未完成，内存价格恢复为原值；`saved` 表示两份配置均保存成功，即使之后助手初始化失败也不声称未保存。初始化失败仍返回 `ok: false`，错误明确提示“配置已保存，但助手初始化失败”，检查配置后重试即可。旧桌面沿用原错误展示，无需新增界面流程。此修复见 [#115](https://github.com/liukun158959-beep/tebb/issues/115)。

若出现 `GUI import forbidden`，检查配置服务是否反向依赖窗口或 Host。若为 `unconfirmed` / `partial`，重新打开设置核对配置后重试；这里不提供跨文件事务，也不声称写入中断已自动回滚。回归使用临时数据和模拟文件写入故障，覆盖无 GUI 服务和真实本地 WebSocket。整个 HeadlessApp 仍有其他 GUI 继承，真实 GUI、模型和发布包验收单独报告。
