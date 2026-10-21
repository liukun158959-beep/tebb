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

已独立的范围包括普通/知识任务、共享对话业务、模型/人设配置、普通会话/只读记忆、长期事实/单句历史编辑、手动压缩及笔记读写/来源问答/勾选总结/导出/生成文件操作；后台现已独立组装所有既有服务，完整启动验收见下文。GUI 实机、真实模型和完整发布包验收单独报告。

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

若出现 `GUI import forbidden`，检查配置服务是否反向依赖窗口或 Host。若为 `unconfirmed` / `partial`，重新打开设置核对配置后重试；这里不提供跨文件事务，也不声称写入中断已自动回滚。回归使用临时数据和模拟文件写入故障，覆盖无 GUI 服务和真实本地 WebSocket。HeadlessApp 去继承与完整后台导入边界另有专项验收，真实 GUI、模型和发布包验收单独报告。

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

失败时检查 error 与 change_state：`not_changed` 表示校验或忙碌拒绝；`unconfirmed` 表示选择/摘要持久化失败、文件结果未确认，选择失败会恢复内存 ID；`partial` 表示摘要已清理但历史未清理完成；`committed` 表示数据已更新但模型临时上下文或 GUI 刷新失败。按提示重新读取并核对后重试，不声称跨文件事务回滚。记忆编辑和手动压缩的后续拆分见下文；HeadlessApp 去继承已完成，完整后台验收见下文。

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

服务测试的子进程拒绝 GUI 导入并实际执行编辑；真实失败 worker 在终态后暂停最终写回，验证历史删除仍被阻止，事实编辑允许。认证 WebSocket 回归令旧 App 编辑方法报错，确保 RPC 直接调用独立服务，覆盖成功快照、忙碌/隔离、摘要故障和已保存但读取失败。服务级与完整后台级导入检查分别保留；实机、真实模型及发布包验证单独报告。对应 [#118](https://github.com/liukun158959-beep/tebb/issues/118)。

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

失败回执：`not_changed` 为准入/校验等失败；`partial` 为生成文件清理未完成、笔记记录仍保留，附 `session_id/note_id/removed_files/failed_file`；`unconfirmed` 为笔记记录保存结果未确认，附目标 ID 及已清理文件；`committed` 为记录已保存但聚合页读取失败。先重新读取目标核对；附件部分删除不会恢复，读取失败不能被解释为保存没有发生。问答与勾选总结的后续共享服务见下文；完整后台现已另行去除 GUI 继承，见下文。见 [ADR 0008](adr/0008-notebook-service.md)、[#127](https://github.com/liukun158959-beep/tebb/issues/127) 及 [Windows 锁缺陷 #128](https://github.com/liukun158959-beep/tebb/issues/128)。

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

## 无 GUI 的笔记问答与勾选总结验收

在仓库根目录使用隔离数据、假模型或 OpenAI SDK 的 MockTransport、系统启动替身运行，无真实模型或外部写入：

```powershell
$env:PYTHONUTF8='1'
python -m unittest tests.test_notebook_generation -v
python -m unittest tests.test_local_api.LocalApiTests.test_notebook_generation_service_rpc_preserves_protocol_and_failure_phases tests.test_local_api.LocalApiTests.test_notebook_generation_disconnect_finishes_once_without_blocking_reads_or_workers tests.test_local_api.LocalApiTests.test_notebook_reads_and_busy_writes_during_question_summary_and_compression -v
# client/ 下运行笔记传输、前端完整回归、类型检查和构建
node --test tests/workspace.test.cjs
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

独立入口为 `NotebookService.run_notebook(session_id,text,doc_ids,sampling,delta_sink,status_sink)` 和 `summarize_notebook(session_id,note_ids,sampling)`。复用已有会话服务及其 ConversationService；在隔离数据中绑定 FakeLLM/MockTransport，并注入笔记平台替身，不能调用真实系统启动。服务返回业务 `ok`；问答传输仍用 `type=notebook`，成功 `done.data` 为原 JSON 字符串 `{answer,cites}`，失败 `error.data` 仍为字符串，额外 `result` 提供结构化失败详情。总结仍为同名 RPC，外层派发和内层业务两层 `ok`，成功包含原笔记页和 path/message。

两入口短暂取得宿主准入、预留共享 running 后释放锁，忙碌立即拒绝；模型、文件导出和系统启动收尾结束才释放预留。读取和独立任务继续，模型及系统等待不持宿主、笔记存储或全局 WRITES。问答先保存提问再检索，来源错误也可能已留下提问。正常完成、非空字符串且没有 tool_calls/function_call 才持久化完整回答/新笔记；失败片段仅留当前页面或失败回执，不写成完整回答。总结使用所选已存回答快照、按选择顺序重新编号引用，不重新检索原文；成功保存新笔记再导出 Markdown 并请求系统打开。

失败回执包含 `session_id/stage/change_state/steps`，以及已取得的 `answer/answer_complete/cites/note_id/path`。`answer_complete` 仅表示模型正文已通过校验，不表示已保存。`not_changed` 指笔记尚未变更（可能已有用量记录）；`partial` 指提问已写入但整轮未完成；`unconfirmed` 指提问/回答/笔记或文件登记写入结果未确认；`committed` 指笔记或文件已确认完成但后续步骤失败。结合 steps 判断哪项已确认，不能仅看全局 change_state。保存未确认先重新读取；已取得正文通过服务/RPC 失败结果保留，问答前端保留流式片段及返回正文，不将失败当成成功。

用量沿用正常模型返回后先记录、再校验并保存正文的顺序；用量保存失败阻止正文持久化，正文仍通过失败回执返回。异常携带的 partial usage 本批不升级补记，不能据错误声称零费用。无 GUI 子进程用例覆盖正文/工具校验、SDK 不完整 finish、用量失败、写入确认丢失及总结导出/刷新/打开失败；认证 WebSocket 用例验证协议、错误 token、立即拒绝、独立 worker 实际写入，以及两入口断连后仅执行一次并完成后台收尾。

笔记流加入一次性终态处理：提前关闭、网络错误、非法 JSON/事件或非法完成结果均解除前端等待并提示先核对，不把断线视为完成。正常 done 后主动关闭不再误报失败，忽略重复或迟到帧。只调整笔记流，普通聊天流不在本批。同步总结保留现有 RPC 断连提示；没有新增任务 ID、取消、自动重试或持久化恢复接口。断连后台可能继续保存/打开，重连后 load_notebook 核对；未落盘片段无法从后端恢复。真实模型、GUI 实机、系统应用显示及完整发布包需另外验收。见 [ADR 0010](adr/0010-notebook-generation.md) 与 [#130](https://github.com/liukun158959-beep/tebb/issues/130)。

## 无 GUI 的知识库服务验收

仓库根目录运行，所有数据在临时目录中，下载使用本地响应替身，不下载真实权重或请求飞书：

```powershell
$env:PYTHONUTF8='1'
python -m unittest tests.test_knowledge_service tests.test_resource_lock -v
python -m unittest tests.test_local_api.LocalApiTests.test_knowledge_eight_service_routes_and_authenticated_failure_receipts tests.test_local_api.LocalApiTests.test_knowledge_compute_disconnect_keeps_reads_workers_and_busy_guards tests.test_local_api.LocalApiTests.test_knowledge_host_lock_and_pet_busy_immediately_refuse_admission -v
# 完整后端回归；Atlas 源码路径按本机调整
$env:DESK_TEST_ATLAS_SOURCE='E:\Learn_Project\Atlas'
python -m unittest discover -s tests -v
```

无 GUI 入口 `KnowledgeService(chat_sessions, start_guard=...)` 提供 `load_knowledge()`、`save_knowledge(payload)`、`download_knowledge(repo)`、`delete_model(repo)`、`add_knowledge(doc_id,label,source=None)`、`delete_knowledge(doc_id)`、`rebuild_knowledge()`、`ask_knowledge(text)`，RPC 保留同名、参数和外层派发/内层业务两层 `ok`。在临时 `DESK_COMPANION_DATA_DIR` 中构造 ConversationService/ChatSessionService，绑定 FakeLLM；替换 `knowledge._require_models/_embed/_rerank/_ms_files` 和 `urllib.request.urlopen` 以复现模型/网络阶段，不要在调试时运行真实下载。GUI 导入禁用子进程覆盖全部八入口；认证 WebSocket 直接调用服务而非旧宿主业务方法。

页面与下载进度读取不取得宿主执行锁，也不占共享助手；同步修改与试问短时准入，已有共享助手、笔记生成、手动压缩、宿主操作或其他知识库同步操作忙碌时立即拒绝。长网络/模型计算不持宿主、WRITES 或知识库存储锁。开始后预留共享 running 至成功/失败清理结束；读取和独立任务可继续。每次检索冻结设置和索引快照；入库/重建先完成全部计算，再在短存储锁内复验配置与索引，任何变化均拒绝覆盖。设置保存不自动重建，改变向量/切块设置后保留旧索引并明确要求重建。

设置与索引使用同一跨进程资源锁及原子替换，JSON 格式兼容。删除来源仅移出索引，不删除 local_sources 登记、本地/飞书原文、笔记或引用快照；删除模型仅清指定缓存目录，保留索引、设置和笔记。失败返回 `stage/change_state` 及可得的目标编号/路径：`not_changed` 是尚未提交；`unconfirmed` 是进入写入但结果未确认；`committed` 是修改完成但页面读取失败；`partial` 是模型清理中可能已有部分文件删除。先重新读取核对，不自动重试，不承诺跨文件回滚。

下载保留后台 daemon 与内存进度轮询。启动成功只代表已获资源并启动工作线程，不代表下载完成；启动后页面失败额外返回 `started=true/repo`，请先 `load_knowledge` 核对 `downloads`。失败保存 `.part`，用户再次请求时继续；进程退出不保留任务进度、不自动恢复、不新增取消。模型加载/推理以仓库名取得共享资源锁，下载/删除取得排他锁，跨进程冲突立即拒绝，不打断已有使用；不同模型及兼容读者可并行。文件元数据变化会使进程内缓存重新加载。Windows 共享锁使用 [LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)，与原排他锁互斥；跨句柄、线程和真实进程用例验证准入与进程退出释放。

试问同步返回原 trace，不写聊天/笔记历史。Atlas 验证正常完成后，产品只接受非空字符串且无工具调用，普通后台知识问答与笔记服务复用正文校验。用量仍在正常模型返回后、正文校验前记录；失败可能已有费用记录，异常携带的部分用量本批不补记。失败回执保留 `answer/answer_complete/stage`，错误文字同样包含已收到正文，供现有页面显示；`answer_complete` 只表示模型正文已校验，不能代替整体成功。同步 RPC 断连不取消后台操作，无自动重试；重新读取页面/进度核对，未落盘的试问正文无法在重连后恢复。真实权重加载、真实下载网络、GUI 实机和发布包另行验收。见 [ADR 0011](adr/0011-knowledge-service.md) 和 [#131](https://github.com/liukun158959-beep/tebb/issues/131)。

## 无 GUI 的今日看板服务验收

在仓库根目录执行；全部用临时数据和假飞书响应，不创建真实会议、删除待办或通知参会人：

```powershell
$env:PYTHONUTF8='1'
python -m unittest tests.test_board_service -v
python -m unittest tests.test_local_api.LocalApiTests.test_board_four_rpc_routes_keep_contracts_and_failure_stages tests.test_local_api.LocalApiTests.test_board_network_disconnect_keeps_cache_models_and_workers_independent -v
# client/ 下运行实际表单、请求顺序和完整前端检查
node --test tests/board-actions.test.cjs
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

服务入口为 `BoardService().load_board(refresh=False)`、`create_agenda(summary,start,end)`、`delete_agenda(event_id)`、`delete_task(guid)`；RPC 沿用同名参数、外层派发和内层业务两层 `ok`。调试时指定临时 `DESK_COMPANION_DATA_DIR`，替换 `board_data._run_lark/fetch_board`，不能直接运行真实飞书修改。时间沿用带时区的 ISO 字符串及东八区今日规则。新建写当前用户的主日历；删除日程仍真实删除并通知参会人，删除待办仍真实删除，均非隐藏或标记完成。创建不要求已有今日缓存；无缓存时正常返回 `snapshot=null`，非今日日程仍可创建并提示今日页可能不显示。远端可提供的 event_id 会保留，但不要求 CLI 一定返回该字段。

当天缓存命中立即可读；刷新、缺少当天缓存的加载、创建与删除共同取得跨进程看板操作资源，冲突即 `stage=admission`，不排队。共享助手回复、笔记生成、压缩及其他任务仍可继续，看板不修改 conversation.running。飞书等待不持 HOST.turn_lock、WRITES 或缓存存储锁；真实 worker 的 create_calendar_event 继续是写工具，保留 completed_writes 指纹检查，但由看板资源保护，不再额外持全局写锁。其他写工具的全局保护不变。桌宠、自动化首次/跨日加载也可能立即返回看板忙碌；当天缓存命中不受影响。

today.json 所有读改写复用 ResourceLock 与原子替换。刷新仅合并日程/待办，日程变更仅合并日程，待办变更仅合并待办；提交时读取最新摘要、摘要时间、文档链接和无关分区。save_today_fields 只占短时存储锁，可与远端等待并行。单侧查询失败保留该侧明确错误，另一侧照常保留；失败不会被当成“没有安排”，也不拿旧列表冒充新查询。换日创建新快照不继承昨天的摘要/链接；跨午夜旧响应不落盘，远端修改已完成仍明确报告，重新加载核对。共同保护仅约束这些应用入口，不承诺串行化用户在飞书或其他应用中独立进行的修改。

失败回执包含 `stage/change_state/remote_state/cache_state` 和可取得的目标 ID、标题、时间，以及查询已落盘时的 snapshot。`remote_state=unconfirmed` 指修改请求已进入调用但无明确成功回执；`confirmed` 表示飞书已确认成功。`cache_state=unconfirmed` 指缓存保存进入但结果未确认，可能已经替换；`confirmed` 说明已落盘。`change_state=not_changed` 是准入/校验或未提交的旧日查询，`unconfirmed` 是远端或缓存写入未确认，`committed` 是远端已完成但后续查询或跨日处理失败。结合两个阶段状态判断，不能据总错误盲重试；错误文字同样说明已成功或未确认，并包含可得目标信息，供旧工具字符串和现有页面显示。

正常返回结构兼容；远端已成功但查询失败会返回业务 `ok=false`、`stage=query`，同时保留失败分区的快照。前端保留原列表和输入、显示真实失败，只有业务成功才清空创建表单。读取失败保留当前页面并允许刷新；无初始缓存时也能刷新恢复。useBoardActions 集中管理请求与行内错误，旧读取响应、修改期间发起的读取均不能覆盖后续操作结果或清掉新的失败提示。调试样本操作不发送 RPC。

沿用 CLI 单次 60 秒超时及同步 RPC，不新增操作 ID、取消、持久化任务、自动重试或远端幂等协议。断连只结束前端等待，后台可能继续创建/删除和写缓存；先重新连接并用 `load_board(refresh=true)` 查询远端，必要时去飞书按事件 ID/标题/时间核对，不能只凭未刷新的本地缓存判定未执行。跨日或没有 event_id 时，需在对应日期核对；不自动重建同一日程。真实飞书服务/通知、GUI 实机、真实沙箱与发布包另行验收。见 [ADR 0012](adr/0012-board-service.md) 和 [#132](https://github.com/liukun158959-beep/tebb/issues/132)。

## 无 GUI 的自动化服务验收

仓库根目录使用临时数据、假周复盘/资讯与实际子进程验证，不发真实飞书消息或覆盖文档：

```powershell
python -m unittest tests.test_automation_service tests.test_weekly_review.ReviewSchedulerTests tests.test_news -v
python -m unittest tests.test_local_api.LocalApiTests.test_automation_four_routes_skip_host_busy_and_keep_active_guards_and_auth tests.test_local_api.LocalApiTests.test_automation_disconnect_keeps_receipt_other_config_and_worker_available -v
$env:DESK_TEST_ATLAS_SOURCE = 'E:/Learn_Project/Atlas'
python -m unittest discover -s tests -v
# client/：pnpm test、pnpm exec tsc --noEmit、pnpm build
# client/src-tauri/：cargo test --locked --lib、cargo check --locked
```

无 GUI 入口是 `AutomationService(review=..., tasks=..., busy=..., changed=...)`。review 复用 WeeklyReviewService 的生成和定时覆盖接口；tasks 为返回同一个 TaskManager 的提供函数；busy 只用于复盘延后，changed 由宿主适配通知。四方法参数保持 `list_automation_jobs(seen=False)`、`save_automation_job(payload)`、`delete_automation_job(job_id)`、`run_automation_job(job_id)`；另有 tick/stop 生命周期。生产新旧入口持有同一数据目录进程运行权，必须在飞书、TaskManager 恢复与调度前取得；第二进程被拒绝且不能修改原任务状态。独立调试使用隔离 DESK_COMPANION_DATA_DIR 与注入依赖，不在生产数据目录另构造任务管理器来观察原任务。

列表/空闲配置不取得宿主执行锁或共享助手，配置 ResourceLock 只保护短读改写与 JSON 原子替换。活跃范围含排队、执行、资讯监控/重试和回执收尾，禁止修改启停/动作/目标及删除该作业；其他空闲作业可继续，删除仅删除配置。前端同时检查 active 与 queued/running，保留失败表单并显示实际阶段，旧桌面亦禁止活跃行操作；后端仍是最终准入所有者。

automation_jobs.json 保留既有字段，兼容增加可选 execution：id/action/doc/phase/accepted_at/result；这是最近一轮执行快照，不是可重放的持久队列或完整历史。原正常返回结构保留，另提供 job_id/execution/active；回执写入未确认时还提供 pending_result，继续保留活跃占用，tick 只补写本地回执，不能再次调用模型或发布。stage/change_state 区分准入 not_changed、store_save/enqueue_save 未确认以及提交后 snapshot 读取失败 committed；未知回执可能已落盘，需读取核对。入队保存抛错时不安装内存队列，不会在后续 tick 悄悄执行；新建保存错误仍保留产生的 job_id，便于确认是否已创建。

复盘开始前保存 running，模型和远端请求不持配置/宿主/WRITES。保留上批 retryable_busy 延后而不报业务失败；同类 action 去重与顺序继续，定时冲突而未执行明确记 missed。禁用配置可手动执行，正常手动执行不消费自动时段。复盘漏跑不补跑，资讯追赶最近一次；未开始、已开始、旧版本 queued 无法判定分别处理，不能把历史 queued 一概说成未执行。恢复时消费已经到达的复盘时段，防止在同一分钟把不明执行自动重提；未来正常定时继续。

资讯控制器依赖明确的 TaskManager。自动化每轮/重试使用现有 submission_key 去重，登记后回执异常先按同一任务 ID 查询，不能重提；读取暂时失败保留执行占用与错误，继续观察该任务。保留原来 15 分钟预算、最多一次临时失败重试、取消不重试，以及新闻发布阶段/远端核对/消息幂等窗口。不另造重试或外部写入协议。资讯任务已创建但 running 回执写入失败，仍监控该任务；最终结果收尾保存失败则仅补写本地记录。

stop 先关准入和启动、清理未开始队列，再在默认 0.5 秒内等待调度/监控线程；TaskManager 使用原有限时退出，客户端保持原 6 秒退出轮询窗口及超时终止。不能承诺取消已发出的网络请求；复盘已有结果可能迟到，仍归同一 execution ID，进程运行权不能提前释放。崩溃或强制退出由系统释放运行权，重启对已开始/旧版不明复盘提示先核对，不自动重发；资讯按任务和发布回执恢复最近一次。同步 RPC 断连不等于取消或接受失败，先重新读取，不自动重提。

验收包括禁止 GUI 导入、实际认证 WS、跨进程配置并发、生产后台/旧桌面双实例拒绝与杀进程后重新准入、存储前后替换故障、晚到回执、资讯监控故障及既有资讯工作流。真实模型、真实飞书写入/通知、完整 GUI 实机和发布包另行验收；本批不发布。见 [#134](https://github.com/liukun158959-beep/tebb/issues/134)。

## 无 GUI 的周复盘服务验收

仓库根目录执行，使用临时数据、假模型、飞书 CLI 替身和实际认证 WebSocket，不向真实飞书创建文档或请求系统打开：

```powershell
python -m unittest tests.test_weekly_review -v
python -m unittest tests.test_local_api.LocalApiTests.test_review_three_rpc_routes_authentication_and_failure_facts tests.test_local_api.LocalApiTests.test_review_model_and_publication_disconnect_allow_reads_board_and_workers tests.test_local_api.LocalApiTests.test_review_host_lock_and_old_desktop_busy_refuse_immediately -v
python -m unittest discover -s tests -v
```

`WeeklyReviewService(chat_sessions, start_guard=..., open_document=...)` 提供三个原有入口：无参 `generate_week_review()`、无参 `write_today_summary_doc()`、`write_week_review_doc(payload)`，及调度器使用的 `write_scheduled_review(doc)`。独立调试先设临时 `DESK_COMPANION_DATA_DIR`，构造 ConversationService、ChatSessionService，给 conversation.agent 绑定 FakeLLM，替换 board_data 的看板/周材料读取、github.format_week_review_text、skill_catalog.read_skill、feishu_auth.create_markdown_doc、feishu_docs.overwrite_markdown_doc，并注入打开函数。认证 RPC 白名单和参数不变，检查外层派发 `ok` 与内层业务 `ok`；直接调用共享服务无需导入 App、Bridge 或窗口。

材料加载前即预留共享助手至保存/打开或失败清理，忙碌立即拒绝；只读、今日看板远端操作、独立后台任务继续。短准入时非阻塞检查宿主锁和旧桌宠 busy，外部等待不持宿主、全局 WRITES、会话或存储锁；自动化复盘最终准入竞争返回 `retryable_busy=true`，队列和 queued 记录保留至后续调度，不能立刻循环重试，也不能把它写作模型失败。独立资讯动作仍可执行。自动化配置与整体调度/停机恢复不属于本批。

生成固定开始日期、周一起点和材料截止时刻；summary_at 记录该材料截止时刻。沿用 GitHub 产出、飞书日程和未完成待办及 weekly-retro 技能，区分产出和会议/待办；用量仍在正常模型返回后、正文校验及保存前落账。Atlas 负责完成原因，产品复用 require_text 拒绝空、非字符串和工具调用正文；模型异常的 partial 正文保留在失败回执，不冒充已落盘复盘。模型/材料/用量失败不替换旧正文，保存结果未确认先读取核对。保存日期在同一短存储锁内复验，旧日结果不得写进新日缓存；新正文、summary_at、清空 doc_url 一次原子替换，看板分区仍保留最新值。

三种发布统一要求非空正文、summary_at 在当前周且不在未来。write_today_summary_doc 总是新建、登记有效 URL 并请求打开；write_week_review_doc 接收 doc 或 title 二选一，覆盖或新建，有有效返回链接才登记，仅新建请求打开；自动化只覆盖配置文档，不登记链接、不请求打开。打开结果为请求已提交，不承诺用户系统已展示文档。每次固定开始时正文，登记时检查原日期、原正文与生成时间，冲突保留已确认远端结果而不将旧链接写到新正文。

回执增加 `stage/change_state/steps`、可取得的 summary、summary_at、target、url、document_id，以及发布的 remote_state、cache_state；原正常快照和 URL 保留。远端未明确成功是 unconfirmed；确认远端成功后本地登记/读取/系统打开失败是 committed，文案同样保留目标或链接。CLI 确认创建但缺链接时保留 document_id，标记 remote_result 阶段，不盲重建。保存与读取合并在既有 save_today_fields 入口中，没有额外自动远端刷新；存储前置核对未通过说明 not_changed，进入原子写入后异常按 unconfirmed 报告，不能声称回滚。错误字段保留已得正文并不表示该正文已替换今日缓存。

同步 RPC 与模型/飞书既有超时沿用，不增加进度、取消、幂等键或自动重试。断连后台可能继续保存或打开，重连先用 load_board 读取本地并在飞书核对目标文档；仅缓存没有链接不能证明远端未创建。真实模型、真实飞书、GUI 实机与发布包另行验收。见 [ADR 0013](adr/0013-weekly-review-service.md) 和 [#133](https://github.com/liukun158959-beep/tebb/issues/133)。

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

同步 RPC 沿用既有模型网络超时，没有新增任务 ID、流式进度、总时限或取消入口。连接断开不取消线程内模型请求，可能继续保存；重连后调用 `load_memory/load_chat_log` 核对，不自动重试。预期专项测试通过，特别是模型等待时另一个真实 worker 可完成 WRITES 保护的工具，以及断连后只有一次模型调用、预留正确释放。HeadlessApp 已独立组装服务，完整后台检查见下文；GUI 实机、真实模型、真实沙箱和发布包另行报告。见 [#119](https://github.com/liukun158959-beep/tebb/issues/119)。


## 无 GUI 的完整后台验收

在仓库根目录运行：

```powershell
python -m unittest tests.test_headless_host -v
python -m unittest tests.test_local_api tests.test_feishu_agent tests.test_feishu_cards -v
$env:DESK_TEST_ATLAS_SOURCE = 'E:\path\to\Atlas'
python -m unittest discover -s tests -v
```

`test_headless_host` 自动使用临时数据目录，在新进程中安装 GUI 导入拦截器，再通过 `tests.headless_backend_fixture` 实际执行 `server.main()`：取得运行权、构造宿主、启动监听和调度，验证 health、错误 token、七个查询、会话 RPC、真实任务 worker 的假模型对话/历史以及认证 shutdown。飞书自动连接与目录外部适配被替换，不访问真实模型、GitHub 或飞书。配置损坏会在监听和任务恢复前明确失败，不改写原文件。其它测试覆盖保存坐标、并发首次访问只构造一个任务/协作实例、显式飞书模式及旧桌面 pet 收尾保护。

服务级 `tests.gui_import_guard.NoGui()` 仍禁止 App、Host、Bridge、BoardWorkbench 与窗口库；后台级 `NoGui(BACKEND_FORBIDDEN)` 只允许 Host 和 Bridge，仍禁止 App、BoardWorkbench、显示器、桌宠、托盘、WebView 与 win32 GUI 模块。先安装拦截器再导入才能验证真实启动链，不能在已加载窗口模块的测试进程里只检查继承关系。

成功应包含 `backend GUI boundary verified`、认证 RPC 的外层和业务层 `ok`、两条普通会话历史以及正常退出码。出现 `GUI import forbidden` 时按 traceback 查引入窗口的实际调用；启动失败先核对隔离目录的配置错误，不能把失败解释为无任务或空结果。错误 token 和白名单外 GUI 方法必须被拒绝，不能为调试绕过现有权限。

直接服务调试可在隔离目录构造 `HeadlessApp()`，通过 `bridge` 或各业务服务调用；独立构造不替代生产入口的数据目录运行权，也不要在正在运行的真实数据目录构造第二个 TaskManager。正式后台仍运行 `python -m desk_companion.local_api.server --port <空闲端口> --token <临时令牌>`；该入口会使用真实配置与飞书自动连接，离线验收优先运行上述测试，不要将令牌或用户数据写入报告。

本批保留系统文件选择、打开/定位、Windows 平台约束与现有退出顺序，不新增任意执行接口或后台重试。真实模型、飞书写入/消息、完整 GUI 实机、沙箱与发布包需分别验收；本批不发布，也不启动 Gugu 产品需求。见 [#135](https://github.com/liukun158959-beep/tebb/issues/135)。

## 业务工作看板验收

范围与协议见 [业务看板](WORK_BOARD.md)；直接服务不构造 GUI 或任务管理器，不连接模型或飞书。以下命令从仓库根目录运行，自动使用临时数据，禁止指向正在运行的用户目录：

```powershell
$env:PYTHONUTF8 = '1'
@'
import os, tempfile, uuid
with tempfile.TemporaryDirectory() as root:
    os.environ['DESK_COMPANION_DATA_DIR'] = root
    from desk_companion.work_board import WorkBoardService
    service = WorkBoardService()
    request = dict(type='create', request_id=uuid.uuid4().hex,
                   fields=dict(title='隔离验收事项', priority='high', due='2026-10-25'))
    page = service.change(request)
    assert page['ok'], page
    item = next(i for i in page['items'] if i['id'] == page['receipt']['item_id'])
    moved = service.change(dict(type='move', request_id=uuid.uuid4().hex,
                               id=item['id'], version=item['version'], state='review'))
    assert moved['ok'], moved
    checked = service.load(request['request_id'])
    assert len(checked['items']) == 1 and checked['items'][0]['state'] == 'review'
    assert service.change(request)['receipt'] == checked['receipt']
    print('work board verified:', checked['receipt']['item_id'])
'@ | python -

python -m unittest tests.test_work_board -v
python -m unittest tests.test_local_api.LocalApiTests.test_work_board_authenticated_contract_dedup_and_busy_independence tests.test_local_api.LocalApiTests.test_work_board_rpc_preserves_conflicts_and_committed_refresh_failure tests.test_headless_host -v

pnpm --dir client test
pnpm --dir client exec tsc --noEmit
pnpm --dir client build
```

认证 WebSocket 沿用 `ws://127.0.0.1:<port>/ws?token=<该进程临时 token>`。发送 `{"type":"rpc","id":"board-read","method":"load_work_board","args":{}}` 获取事项版本与执行目录；写入示例为 `{"type":"rpc","id":"board-create","method":"change_work_item","args":{"action":{"type":"create","request_id":"debug-create-0001","fields":{"title":"隔离事项"}}}}`。读取回执时传 `args.request_id`。外层 `result.ok` 是派发结果，内层 `result.result.ok` 才是业务结果。使用上述测试的临时后端即可验证真实认证链，不要在用户目录人工重发写入。

`tests.test_work_board` 覆盖持久化、归档恢复、跨项目关联、删除项目的原子解除归属、重复请求、版本竞争、执行状态保留、提交确认丢失及刷新失败；禁止 GUI 导入的新进程还验证不构造 TaskManager。`test_local_api` 通过实际本地 WebSocket 检查错误 token、共享助手忙碌/宿主锁期间仍可操作、去重回执及业务失败；`test_headless_host` 在导入拦截器下实际启动完整后台并调用新建/移动 RPC。执行目录只暴露展示字段，不包含模型密钥、执行输入和任务内部载荷。

界面隔离演示：运行 `pnpm --dir client dev --host 127.0.0.1`，打开所输出端口的 `/client.html?debug=work`。该入口运行真实 WorkBoardPane 与控制器，但注入内存数据，明确显示“演示数据，未连接后端”；刷新恢复初始数据。可检查跨列拖拽、移至菜单/键盘、编辑、筛选、归档/恢复、关联与详情。界面复用全局主题和减少动效偏好。前端行为测试额外用延迟/乱序/断连替身验证连续拖动、失败恢复、旧草稿版本、逐卡核对与手动原编号重试。

遇到 `conflict` 保留输入并重新读取；`unconfirmed` / `committed` 或调用超时保留操作编号，先查回执，不自动重发。找不到回执不代表后台肯定没执行；只有用户明确手动重试时复用原编号和内容。新移动不会覆盖其它事项，项目删除后旧版本必须冲突，归档或完成均不能改变 AI 运行状态。本批未调用真实模型/飞书写入，未验证打包后的 Tauri 实机，未发布版本；日程创建、双向定位和时间轴拖动属于 #123 后续批次。
