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

已独立的范围包括普通/知识任务、共享对话业务、模型/人设配置、普通会话/只读记忆、长期事实/单句历史编辑、手动压缩及笔记基础读写/导出/生成文件操作；本地 API 的其它数据 RPC 仍通过 HeadlessApp 继承兼容，不代表整个后端已无 GUI 依赖。GUI 实机、真实模型和完整发布包验收单独报告。

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

## 无 GUI 的普通会话服务验收

在仓库根目录执行，全部使用临时用户目录、脚本 worker 和模拟故障，不请求真实模型或发送外部消息：

```powershell
python -m unittest discover -s tests -p test_chat_sessions.py -v
python -m unittest tests.test_local_api.LocalApiTests.test_headless_sessions_clear_and_isolate_data_without_gui_methods tests.test_local_api.LocalApiTests.test_session_rpc_blocks_pending_and_tail_tasks_but_keeps_reads_available -v
python -m unittest tests.test_local_api.LocalApiTests.test_first_access_initializes_tasks_for_rpc_and_chat -v
python -m unittest discover -s tests -p test_conversation.py -v
```

入口为 `ChatSessionService(conversation, mutation_guard=...)`，共享 ConversationService 的 UserState、Agent 和忙碌状态。独立调试可在临时 `DESK_COMPANION_DATA_DIR` 中用 `state.load_state(0, 0)` 创建持久配置，再构造 ConversationService；不使用保存为 no-op 的 worker `for_session` 来管理当前选择。后台注入 `TaskManager.session_mutation_guard`，桌面注入 GUI 忙碌检查；不要绕过生产入口的保护。服务提供新建、切换、清空、历史和只读记忆 5 个同名 RPC 方法；切换输入为已有普通会话 ID，返回保持原有 ok/session_id/items/sessions/context 等字段。

空会话重复新建复用 ID；清空保留当前 ID，仅移除该会话的历史、压缩摘要及 Agent 临时上下文，其它会话、长期事实、笔记和任务记录保留。已清空会话会从基于历史推导的列表中消失。当前会话排队、执行、取消或收尾未结束时，修改返回明确错误，不自动取消任务；其它会话任务继续，历史/记忆读取不受模型回复限制。旧桌面忙碌覆盖发送起点和回调落盘阶段；普通回复与每日纸条均验收。

测试先拦截 GUI 导入再实际执行服务与队列保护；真实离线子进程会在任务终态后暂停部分回复写入，验证此时仍禁止修改，待收尾完成才能清空。另通过线程屏障验证提交无法穿入修改临界区。WebSocket 测试覆盖后台无窗口清空、5 个 RPC 绕过继承方法、任务状态与数据隔离，以及宿主执行锁被占用时仍可读取。

首次访问回归不预先注入任务管理器，分别以清空会话 RPC 和发送消息作为首次入口，使用脚本 worker 完成任务、写入历史并查询任务列表，确认只初始化一次。若首次操作出现 `NoneType` 的 `submit` 或 `session_mutation_guard` 错误，先检查 HeadlessApp.tasks 的延迟初始化，而非窗口状态。修复见 [#117](https://github.com/liukun158959-beep/tebb/issues/117)。

失败时检查 error 与 change_state：`not_changed` 表示校验或忙碌拒绝；`unconfirmed` 表示选择/摘要持久化失败、文件结果未确认，选择失败会恢复内存 ID；`partial` 表示摘要已清理但历史未清理完成；`committed` 表示数据已更新但模型临时上下文或 GUI 刷新失败。按提示重新读取并核对后重试，不声称跨文件事务回滚。记忆编辑和手动压缩的后续拆分见下文；整个 HeadlessApp 去继承仍待后续处理。

## 无 GUI 的记忆编辑服务验收

在仓库根目录执行，使用临时用户目录、真实存储、脚本 worker 和故障注入，不请求真实模型或发送外部消息：

```powershell
python -m unittest tests.test_memory_editing -v
python -m unittest tests.test_local_api.LocalApiTests.test_memory_editing_rpc_uses_service_and_preserves_task_boundaries tests.test_local_api.LocalApiTests.test_memory_delete_preserves_history_when_summary_invalidation_fails -v
```

入口是 `MemoryEditingService(chat_sessions)`，组合生产使用的 `ChatSessionService`，不要绕过其任务保护或创建另一份当前会话状态。在临时 `DESK_COMPANION_DATA_DIR` 中创建 `state.load_state(0, 0)`、`ConversationService(state)` 和带 `mutation_guard` 的会话服务，再创建编辑器；后台注入 `TaskManager.session_mutation_guard`，旧桌面复用自己的忙碌检查。

4 个方法与 RPC 参数一致：`add_fact(text)`、`update_fact(fact_id, text)`、`delete_fact(fact_id)`、`delete_memory_turn(role, text)`。成功返回原记忆页快照的 `ok/total/fact_limit/facts/context/items`。事实全局共享，沿用 30 条、80 字、去重和来源规则；同正文更新保留原来源，实际改写变为用户来源并清空原话。共享助手回复期间拒绝编辑，独立任务运行期间允许；已有任务可能继续使用旧提示，之后的新一轮读取新值，后台工具仍可能随后更新事实。

删除历史仅针对当前普通会话，去空白后同角色、同正文的重复记录一起删除；不存在时不改动。先使覆盖该句的摘要失效，再删除原文、清理助手临时上下文；未覆盖该句的摘要、其他会话、长期事实、笔记和任务记录保留。排队、执行、取消和终态后的收尾写回期间均拒绝删除，不自动取消，其他会话可继续。直接服务调用也按 WRITES、会话选择锁、任务条件锁的顺序保护，不能只依赖 RPC 层互斥。

失败查看 `error/change_state`：`not_changed` 表示校验或忙碌拒绝；`unconfirmed` 表示事实写入或摘要失效等文件操作结果未确认，摘要失败尚未删除历史；`partial` 表示包含该句的摘要已失效，但历史删除未完成；`committed` 表示编辑已完成但记忆页读取或临时上下文清理失败。后两者不能当作“未修改”重复提交；先用 `load_memory/load_chat_log` 核对，需要时检查文件并恢复后重试。原存储没有跨文件事务，此处不承诺中断回滚。

服务测试的子进程拒绝 GUI 导入并实际执行编辑；真实失败 worker 在终态后暂停最终写回，验证历史删除仍被阻止，事实编辑允许。认证 WebSocket 回归令旧 App 编辑方法报错，确保 RPC 直接调用独立服务，覆盖成功快照、忙碌/隔离、摘要故障和已保存但读取失败。该服务独立不代表整个 HeadlessApp 已去除 GUI 继承；实机、真实模型及发布包验证单独报告。对应 [#118](https://github.com/liukun158959-beep/tebb/issues/118)。

## 无 GUI 的笔记基础服务验收

仓库根目录执行，使用临时笔记、假模型、假知识库和真实 localhost WebSocket；不读私人笔记、不写飞书或请求真实模型：

```powershell
python -m unittest tests.test_resource_lock tests.test_notebook_service -v
python -m unittest tests.test_local_api.LocalApiTests.test_notebook_reads_and_busy_writes_during_question_summary_and_compression tests.test_local_api.LocalApiTests.test_notebook_rpc_failure_phases_host_admission_and_authentication -v
```

独立调用先设置临时 `DESK_COMPANION_DATA_DIR`，构造 `ConversationService`、`ChatSessionService` 和 `NotebookService(chat_sessions, start_guard=...)`。四个方法为 `load_notebook()`、`new_notebook()`、`save_notebook_note(session_id, question, answer, cites)`、`delete_notebook_note(session_id, note_id)`。服务可以在禁止 GUI/Bridge 导入的子进程内调用，知识库仍须有可读的配置及索引（空库可用）；故障测试用 `knowledge.public_view` 替身，不依赖 embedding 下载。RPC 按同名方法派发，保留外层派发、内层业务两层 `ok`，不能只检查外层。

正常读取返回 `ok/docs/sessions`，新建另有 `session_id`，保存另有 `note_id`，删除返回新快照。新建每次分配独立 ID；重复保存同一内容仍生成新笔记，故不自动重试。三项写入不改变普通会话选择；引用是已存快照，不要求来源以后一直留在知识库。笔记生成文件仅限笔记目录，删除不影响问答 turns、其它笔记及来源原文。

只读加载不取得宿主执行锁，也不占用共享助手。宿主忙碌、共享回复、笔记问答/汇总、手动压缩期间，三项写入立即返回 `ok=false/change_state=not_changed`，不等模型结束后执行；旧桌面还检查宠物收尾状态。独立任务不一概阻止笔记操作。专项 WebSocket 测试暂停三个假模型调用，验证读取、忙碌拒绝和独立 worker 实际写入可同时进行。

底层 `notebook.json` 使用独立跨进程锁保护所有读改写，包括问答、收藏、导出登记及生成文件清理；复用临时文件原子替换，模型等待、飞书调用和系统打开文件不持笔记存储锁。跨进程测试竞争保存、问答写回、成果收藏及 Markdown 导出，核对每项增量均保留；操作系统锁在进程退出时释放。原子替换不等于笔记和生成文件的跨文件事务，也不承诺掉电耐久性。

失败回执：`not_changed` 为准入/校验等失败；`partial` 为生成文件清理未完成、笔记记录仍保留，附 `session_id/note_id/removed_files/failed_file`；`unconfirmed` 为笔记记录保存结果未确认，附目标 ID 及已清理文件；`committed` 为记录已保存但聚合页读取失败。先重新读取目标核对；附件部分删除不会恢复，读取失败不能被解释为保存没有发生。问答、汇总生命周期仍留宿主，HeadlessApp 整体尚未脱离 GUI。见 [ADR 0008](adr/0008-notebook-service.md)、[#127](https://github.com/liukun158959-beep/tebb/issues/127) 及 [Windows 锁缺陷 #128](https://github.com/liukun158959-beep/tebb/issues/128)。

## 无 GUI 的笔记导出及生成文件验收

在仓库根目录运行；全部使用临时数据、假飞书 CLI 和系统启动替身，不创建真实飞书文档或打开系统程序：

```powershell
$env:PYTHONUTF8='1'
python -m unittest tests.test_notebook_files -v
python -m unittest tests.test_local_api.LocalApiTests.test_notebook_output_rpc_success_partial_failure_and_authentication tests.test_local_api.LocalApiTests.test_notebook_external_wait_does_not_hold_host_storage_or_global_write_lock tests.test_local_api.LocalApiTests.test_notebook_reads_and_busy_writes_during_question_summary_and_compression -v
```

入口为 `NotebookService(chat_sessions, platform=..., create_document=...)`，可注入 `open(target)/reveal(path)` 平台对象及 `create_document(title, markdown)` 可调用对象。省略时使用现有 Windows 启动和飞书创建 CLI；调试必须注入替身。五个同名 RPC 为 `export_notebook_markdown(session_id,note_id)`、`export_notebook_feishu(session_id,note_id)`、`open_notebook_file(session_id,note_id,name)`、`reveal_notebook_file(session_id,note_id,name)`、`delete_notebook_file(session_id,note_id,name)`。后面三项仅按笔记登记名字查找，不接受任意路径。RPC 沿用外层派发和内层业务两层 `ok`、本机 token 认证及原参数。前端现有“打开”按钮仍通过资料暂存进行侧栏预览，不等于系统打开 RPC。

五项均复用短时宿主准入及共享 busy，问答/汇总/压缩或其它共享操作忙碌时立即拒绝，开始后直到失败/完成才释放；读取及独立任务继续。网络和系统请求不持宿主执行锁、笔记存储锁或全局 WRITES；本地文件清理/写入与登记保存仍共同持短时笔记锁。专项回归暂停假飞书调用，核对另一个真实 worker 可完成 WRITES 保护的写入，笔记问答写回也可完成。

Markdown 保留同名覆盖、旧文件清理及登记；成功导出后请求系统打开。成功回执保留原字段，额外提供 `steps`：`cleanup/generation/registration/refresh/open` 或 `deletion/registration/refresh`；飞书为 `creation/open`。步骤值包括 `not_started/confirmed/unconfirmed/failed/already_missing/requested`，其中 `requested` 仅说明启动请求已返回，不能证明系统应用已显示文件。失败另有 `stage`、`change_state`、目标 ID 和路径/链接；已有生成结果即使打开失败也保留，错误文字同样包含路径/链接，适配现有错误展示。

`partial` 为旧文件清理未完成；`unconfirmed` 为文件生成/删除或登记写入结果未确认；`committed` 为文件与登记已完成但刷新或打开失败。`path` 始终为目标路径，是否确认生成需同时检查 `steps.generation`，不能仅凭 path 判定存在。刷新失败不继续系统打开。删除只影响指定生成文件和登记，保留其它文件、笔记、问答和引用来源；文件已缺失时清理失效登记。登记重复文件名时拒绝不明确的单文件操作。

飞书每次创建新文档，保留现有 90 秒 CLI 超时；本服务不重试，底层 CLI 的内部行为不构成云端幂等保证。异常/超时/无有效回执按创建未确认处理，先去飞书核对；已确认创建但打开失败时回执保留 `url/document_id`。连接断开不取消已经运行的同步操作；本地导出/删除应重连后读取笔记并核对文件，飞书应核对云端，不能盲重试。本批无取消入口、云端去重或新 UI。参见 [ADR 0009](adr/0009-notebook-outputs.md) 和 [#129](https://github.com/liukun158959-beep/tebb/issues/129)；真实飞书、系统应用显示和发布包未由替身测试验证。

## 无 GUI 的手动压缩服务验收

仓库根目录执行，使用临时数据、真实 OpenAI SDK 的 MockTransport、脚本 worker 和文件故障，不请求真实模型或发送外部消息：

```powershell
python -m unittest tests.test_context_compression -v
python -m unittest tests.test_local_api.LocalApiTests.test_compression_rejects_current_queue_without_calling_model tests.test_local_api.LocalApiTests.test_compression_rejects_occupied_host_without_waiting_or_calling_model tests.test_local_api.LocalApiTests.test_compression_rpc_keeps_reads_other_workers_and_cancellation_available tests.test_local_api.LocalApiTests.test_compression_disconnect_finishes_once_and_can_be_read_after_reconnect tests.test_local_api.LocalApiTests.test_compression_rpc_distinguishes_uncertain_save_and_committed_refresh_failure -v
```

入口 `ContextCompressionService(chat_sessions, start_guard=...)` 的 `compress_context()` 无参数，保留原记忆页 `ok/total/fact_limit/facts/context/items` 结构；RPC 仍有外层派发与内层业务两层 `ok`。独立调用时先设置临时 `DESK_COMPANION_DATA_DIR`，创建持久 UserState 和 ConversationService，再构造会话服务。绑定 TaskManager 时同时传入 `mutation_guard=manager.session_mutation_guard` 和 `reservation_guard=manager.reserve_session`；只有前者不足以保护长调用期间的新任务提交。后台的 start_guard 短暂、非阻塞获取宿主操作锁，只保护开始和模型绑定，忙碌则明确拒绝。

压缩开始前必须没有当前会话排队、执行、取消或 active 收尾任务，也不能有共享助手回复/其它预留。会话服务短暂固定当前选择并设置共享忙碌，任务层登记会话预留；网络等待不持有会话锁、任务条件锁、宿主锁或全局 WRITES。压缩期间拒绝重复压缩、当前会话新任务、会话切换/清空、记忆编辑及模型/人设/模型网络配置修改，直到保存、快照读取与清理结束。读取及其他会话任务的执行、查询和取消仍可用；同 submission_key 重取已存在任务不算新任务，保留原幂等回执。

模型和上下文复用 ConversationService.prepare_context：传入已有摘要和未覆盖的较早原文，保留最近完整 user/pet 一轮；尾部不成完整一轮时保留最后一句，没有更早记录则明确失败。完整原文、长期事实、其他会话与笔记保留。没有工具定义，也不执行模型返回的工具；Atlas 校验正常完成后，产品还要求无工具调用且正文为非空字符串，空、截断、过滤、工具返回或网络失败均不提交新摘要。自动压缩共用相同的模型结果校验。

摘要保存沿用 context_pack 的校验、临时文件替换和其他会话保留，通过公开 `save_pack` 入口短暂获取共享写锁完成。`change_state=not_changed` 表示未替换摘要；`unconfirmed` 表示已进入摘要保存、文件结果未确认；`committed` 表示摘要已保存，但后续快照读取或清理失败。先重新读取记忆页核对，不能把后两者当作“未执行”直接重复压缩，也不声称跨文件回滚。

用量沿用原顺序：正常模型回执先落账，再检查正文并保存，所以空正文、工具返回或保存失败可能已有费用记录；用量写入失败会阻止新摘要保存。模型异常携带的 partial usage 本批未另行补记。请求仍走现有 HTTP 调试记录和会话上下文，不增加日志或调试网络服务。

同步 RPC 沿用既有模型网络超时，没有新增任务 ID、流式进度、总时限或取消入口。连接断开不取消线程内模型请求，可能继续保存；重连后调用 `load_memory/load_chat_log` 核对，不自动重试。预期专项测试通过，特别是模型等待时另一个真实 worker 可完成 WRITES 保护的工具，以及断连后只有一次模型调用、预留正确释放。整个 HeadlessApp 仍有看板/笔记继承；GUI 实机、真实模型、真实沙箱和发布包另行报告。见 [#119](https://github.com/liukun158959-beep/tebb/issues/119)。
