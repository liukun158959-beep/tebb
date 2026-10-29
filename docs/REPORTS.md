# 长文证据与技术报告图表（#160 / #161 / #163）

2026-10-29 用户Q3–Q5确认：按需补读长文，报告采用“结论摘要 → 技术机制 → 可比数据与图表 → 适用场景 → 证据与限制”。不相关行业背景省略；已有报告保留，用户明确重新生成才产生新稿。模型提示约束章节，并不等同自动证明报告结构或结论全部正确。

最新旧版技术报告确实成功交付，但研究取到的Kimi正文20638字符和Anthropic新闻室15134字符仅保留前12000；该次整链9次模型调用。搜索摘要300/600字符、网页节选12000字符和成果24000字符是不同限制，不应混称模型窗口截断。

## 同一正文快照续读

`web_fetch`首次下载依旧有1.5MB、总时限、公开地址/TLS/IP锁定和跳转保护，无浏览器登录态。完整解析正文保存到本机任务链独立目录；返回不超过12000字符的区间、完整哈希、snapshot_id、read_start/read_end、next_offset和总字符数。后续按snapshot_id+offset，或query/section定位已保存正文；不会用后来的网页版本拼接旧证据。续读仍消耗既有工具预算，不通过无限扩大消息或下载上限解决。

快照仅在原主任务链内使用，子伙伴和汇总可共享；标识、来源和完整哈希均复核，不能跨任务读任意文件。页面正文可能包含用户提供链接可访问的内容，快照与带查询参数的完整来源仅保存在本机。该批不新增自动清理已有用户资料。

相同快照的实际读取区间合并，覆盖完整正文后才显示已读正文；只读尾部仍是节选。证据传递保留区间，来源检查显示已读字符范围。403、PDF、不支持页面或超大文件仍明确失败。模型应将结论对应实际证据，尾部关键数据不足先补读；来源核验仍只证明取材来源与阅读范围，不能自动证明语义成立。

```json
{"url":"已发现的公开链接"}
{"url":"同一链接","snapshot_id":"上次返回的编号","offset":12000}
{"url":"同一链接","snapshot_id":"上次返回的编号","section":"技术指标"}
{"url":"同一链接","snapshot_id":"上次返回的编号","query":"待核对的关键词"}
```

## 构建图表

新增工具 `build_chart`：bar、line、timeline、flow，输入仅为结构化数据，不执行模型提供的Python/HTML/JS。复用Matplotlib非交互Agg渲染PNG/SVG，不导入窗口框架；后端依赖在源码与运行时需求中声明。参考[官方静态渲染说明](https://matplotlib.org/stable/users/explain/figure/backends.html)。

数值图最多20个标签、4个系列，需要单位、可比口径与实际已读正文的证据ID；时间线最多15项且有来源，流程图最多8节点/16条边、标为示意。缺值显示缺失，不填0；非有限数、非法ID及重复边拒绝。有环关系需明确使用 cycle 或 decision，旧输入默认无环。派生数值需说明计算。程序校验格式与来源归属，**不能自动证明模型抽取数值和可比口径正确**；不同评测口径不得伪装同轴排名。

2026-10-29 样式确认：新图使用统一字体、低饱和配色、直接数值与轻网格，聊天标题只显示一次；数据、来源和原始结构按需展开。已有 PNG 不重绘，读取旧图时避免重复标题；静态导出保留数据与依据。

流程布局通过 flow_style 指定 steps（线性）、decision（条件）、parallel（并行）、swimlane（职责）、cycle（循环），或 auto。auto 根据节点类型、分支及泳道选择；不为美观编造分工或条件。node_kinds 与 nodes 一一对应，支持 process/decision/terminal；edge_labels 与 edges 对应，判断的每条分支须有条件；node_lanes 与 nodes 对应，泳道最多四类且需显式负责人。条件和职责同时保留在聊天数据、Markdown和飞书文字回退中。渲染使用已有 Matplotlib，不执行模型代码。

tebb/写作助手默认增加构建图表权限，用户可在伙伴配置关闭；自定义伙伴按明确工具权限执行。增加图表能力不改变写作交接阶段，工具事件展示真实开始/结束。每条主任务链最多4次有效渲染尝试，子伙伴和重启共用已保存预算，失败渲染也计入，不自动重跑整条报告。

成功生成后返回 `tebb-chart`代码块和真实资产ID，必须原样插入。聊天、成果与笔记读取认证RPC返回的固定PNG，可展开原始数据、单位、口径与来源；不开放任意路径或执行原始HTML。数据与图表按创建时快照保存，删除聊天/笔记不会破坏独立原报告。

保存笔记沿用成果正文，Markdown导出生成同目录PNG并一起登记，删除该笔记生成文件时按现有规则处理。导出包含图表数据表和来源，媒体不能只靠丢失的截图解释。飞书本批不嵌入PNG，退化为可读数据表/关系说明；不把内部图表ID当成飞书图像，也不新增上传副作用。

当模型路由提取到用户明确要求图表，requires_charts作为原任务及交付阶段验收条件；缺少真实图表或资产核对失败不能宣称完整交付。工具失败保留正文、数据、已确认文件格式和阶段；登记失败不声称回滚，不自动重跑整个任务。

## AI 调试与验收

认证RPC `get_report_chart({chart_id:"已返回的32位资产ID"})` 返回PNG、spec、sources；没有可用登记或哈希不匹配则失败。任务工具事件与完整调用记录继续复用AGENT_DEBUG。

```powershell
python -m unittest tests.test_web_snapshots tests.test_report_charts tests.test_notebook_service tests.test_notebook_files tests.test_notebook_generation -v
python -m unittest tests.test_local_api.LocalApiTests.test_context_inspection_uses_authenticated_shared_rpc -v
# 外部公开只读请求与真实模型会产生用量；临时数据，不重放用户原任务、不操作飞书。
python scripts/probe_report.py --live --url https://www.kimi.ai/blog/kimi-k3-1-pro --output E:/Learn_Project/output/report-live.json
```

脚本验证实际执行器、模型、网页快照续读、构建工具及引用，不代替完整调度和实机验收。正文/图表失败样本保留，不以短时成功承诺长期服务稳定。

## 本机验收记录（2026-10-29）

- 完整后端最终730项（716通过、14跳过），前端181项通过；类型检查和生产构建通过。额外验证了独立暂存上下文13项、网页/协作84项及快照完整性定向回归。
- 真实mimo-v2.6-flash：同一Kimi官方博客快照读取0–12000与12000–20638，原文哈希保持一致；构建1张有来源、单位、口径及计算说明的图表，引用检查显示0–20638已读完整，共4次模型调用。首轮3次调用后漏图，被严格检查拒绝，失败记录保留；补齐至多一次纠正路径并以替身验证，不自动重跑旧用户任务。
- 真实LiveCollaboration、HeadlessApp与认证WebSocket，配合隔离队列/调用夹具，验证聊天图表、数据与来源、普通用户上下文、容量快照、无嵌套表单及390px无横向溢出。首次夹具停在其他会话、补主题时引用了错误CSS路径，修正夹具后通过；不将这些失败说成生产功能测试成功。
- 材料目录：`E:/Learn_Project/output/context-report-2026-10-29/`，含browser-final.log、context-debug.png、report-charts.png、context-mobile.png、live-report.json（首轮）与live-report-final.json。回归日志为output/context-report-back-final.log、context-report-front-final.log和context-report-build-final.log。

没有向真实飞书写入测试数据，也没有发布版本。来源引用和格式校验不能代替事实/评测口径审阅；复杂图形、飞书内嵌图像及新的自动上下文管理策略不在本批。

2026-10-29 #163 样式更新验收：五类流程均实际生成 PNG/SVG，展开数据与 Markdown 保留条件和职责，旧资产继续可读。检查了实际五类导出图，修正正反向分支标签重叠；既有四类图、失败回执、预算、笔记导出及任务交付回归通过。完整后端735项（721通过、14跳过），最新受影响83项、前端183项及类型/构建通过。实际认证浏览器、数据表和静态图验收资料见 `E:/Learn_Project/output/context-style-2026-10-29/`。本轮没有重跑用户报告或调用真实模型/飞书。
