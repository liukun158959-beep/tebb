# Atlas 执行只读研究分工，产品保留生命周期

#171 按用户本批“全部采用推荐”授权推进。grill-with-docs 的事实前沿已核对固定Atlas fdafaec与当前tebb：Agent支持三参数ChatModel、run_detailed及公开memory接口；CollaborationLLM可复用原传输。不是以代码行数或线程超时宣称收敛。

首版限Windows：已验证ProcessTree的Job随父进程异常退出关闭；Linux尚无等价父退出保护，不默认启用。仅新建的一层、无前序依赖、collect阶段、角色权限非空且不超出联网搜索/网页读取的分工进入试点；旧任务、依赖/写入分工、规划路由与负责人综合交付沿原路径。产品保留两路任务进程、父子身份、权限、持久化、来源、实际回执、预算、取消与恢复。每个实际子任务原线程调用Atlas Agent，不使用parallel_gather内部线程池，不共享LLM或动态状态，不创建虚拟任务/新根预算。

Atlas只执行获准研究工具白名单，且仍须通过产品角色、附件与来源范围。auto_inject_spawn_tool=False，无委派/写工具登记；工具retry_max=0（Atlas的1代表首次加一次重试），不能隐藏真实尝试。模型调用和函数执行处保留Ledger准入；插件异常会被Atlas吞掉，不能依靠插件保障权限或预算。多个tool_calls的正常执行为同线程串行，保留ContextVar。

公开ChatModel适配只暴露模型名并复用CollaborationLLM.chat/close与ExecutionLLM。不能直接把CollaborationLLM.model配置字典交给Atlas观测，它会原样记入journal/tracer且含凭据。使用明确journal与公开memory接口承接上下文/追加资料/来源目录；产品共用来源、错误、回执与独立审阅规则，Atlas success仅说明产生终答，terminated/suspended不得升级为产品完成。

MaxSteps当前在递增后按>=检查，N最多允许N-1次工作调用；Timeout同样只在回合边界检查。实际发送/工具额度由根Ledger决定，阻塞IO、取消、超时和父退出由TaskManager/ProcessTree收尾。保留局部候选/审阅额度，不能把线程future超时当请求已取消。

调试沿原任务/调用/事件，显式标记实际运行时，不提供旁路Agent或任意执行入口。固定隔离对照旧串行/旧并行/Atlas并行，分别报告质量、来源、错误、用量完整性、未知费用与时延；实际取消、部分失败与进程退出必须验证。真实模型队列运行前冻结有限计划，保留全部失败，不写飞书、创建SDK环境或修改冻结发布物。

工具白名单为web_search、web_fetch、read_local_source、project_chat_search；具体登记仍取决于角色和所选资料，不能借白名单读取未授权附件。带GitHub专用、终端或其他扩展工具的角色仍走旧路径。模型名仅字符串；InMemoryJournal显式禁用工具结果缓存，重读仍执行产品函数并计入预算。调试契约及三种模式入口见[只读研究验证](../READONLY_RESEARCH.md)。
