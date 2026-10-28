# 通用 tebb 与工作区演进

2026-10-28 用户 Q1–Q7 已全部接受推荐方案，进入实施，无待确认产品分支。分批对应 #144、#145、#146、#147、#148；不发布。

- #144：日/周固定 00:00–24:00，移除凌晨折叠，保留时间网格与页面滚动。月视图完整读可见范围；点事件看详情，日期进入单日，月格可建草稿；拥挤展开日列表。精确拖动只在日/周，不扩大只读类型编辑。
- #146：事项负责人默认 tebb，可选职责伙伴，明确点击才执行。关联/拖动不启动。旧执行 links 是成果历史，不转成负责人，项目权限与每轮快照继续存在。
- #145：默认显示 tebb·通用助手、研究助手、写作助手、编程助手；保留稳定 ID、历史、模型及自定义。tebb统一入口，保留直接找伙伴。tebb可发现使用全部伙伴、工具、Skill和已连接MCP；目录、编辑、终端沿用项目授权。一层委派，tebb亲自汇总，停止传递子任务，子任务不递归委派。
- #147：共享界面与AI日程服务，任意日期本地和可编辑个人飞书日程CRUD。默认本地，明确飞书才同步；删除已同步/飞书记录真实删远端，保留事项和AI历史；歧义先问，既有只读范围保留。新增通用tebb自动化，支持一次/每天/每周，保存目标/项目/负责人，每轮独立执行。后台运行才触发，结果不明不盲目重做外部写入。
- #148：左下角改为桌宠页，预览、显示/隐藏、三种圆点配色/大小及已安装凯尔希，复用原偏好及宠物窗；只迁桌宠设置，主界面主题/背景留设置。不新增任意素材导入。

复用现有领域服务、SQLite执行队列、认证RPC、权限和回执。能力通过实际工具接入，不靠提示词宣称执行。界面/服务/执行任务分别验证，并覆盖并发、断连、失败及数据兼容。模拟外部边界，不向真实飞书做自动测试写入。

## 实际调用链与调试

事项 `change_work_item(type=execute)` → WorkExecutionService → Collaboration.create_execution → 原 TaskManager。通用自动化 `tebb_task` → AutomationService → Collaboration.submit_execution，提交键固定为 `automation-<execution.id>`，同轮不会重建根执行。

tebb worker 工具 `calendar_query/calendar_change/automation_list/automation_save/automation_manage` → worker_rpc → 原带 token 的 WebSocket `tebb_operation` → 父后台活跃 tebb 身份校验 → 既有领域服务。`_call_id` 由实际模型工具调用传入，派生请求编号，不能靠回答正文声称已执行。工具错误保留结构化 stage/change_state/operation。MCP 先列已配置服务器与真实 schemas，再调用；不把配置密钥返给模型。Skill 复用 list/read，视频和事实复用既有工具，事实写入绑定本轮原始用户目标。

用临时数据复现：

```powershell
# 仓库根目录，无真实模型或飞书写入
python -m unittest tests.test_tebb_execution tests.test_calendar_writes tests.test_schedule_month -v
python -m unittest tests.test_local_api.LocalApiTests.test_tebb_worker_uses_authenticated_shared_calendar_and_automation_services -v
# 全量与前端
python -m unittest discover -s tests -v
pnpm --dir client test
pnpm --dir client exec tsc --noEmit
pnpm --dir client build
```

真实后台调试沿用 [Agent 调试](AGENT_DEBUG.md)。先记录事项 request_id、根 run_id 或自动化 execution.id，再看执行页父子关系、工具开始/结束、结构化失败和原回执。日程未知写入读取原编号；自动化 `check_automation_execution({job_id})` 只观察原任务，不重跑目标。

界面演示 `client.html?debug=work`，左侧日程切换月/周/日，左下桌宠进入独立外观页，事项编辑选择 AI 负责人后在详情明确执行。演示数据不会调用真实模型或飞书，不能作为真实账户可用性的证据。

职责和兼容决策见 [ADR 0020](adr/0020-tebb-owned-execution.md)。本批清理凌晨折叠状态/动画及未用的旧删除传参，保留旧桌面与已有外观入口的兼容适配。不更改 Atlas 接口、Rust 壳、版本或发布产物。

## 验收记录

2026-10-28：后端全量 672 项，658 通过、14 按原有环境开关/权限条件跳过，无失败；最终补充的执行/日程写入专项15项通过；负责人旧客户端兼容收尾后，事项/执行/日程合并专项23项通过。前端完整 172 项、TypeScript 和生产构建通过，既有分包体积提示保留。

真实 Edge 浏览器通过完整24小时、月/日切换、月格无蒙层草稿、事项负责人、桌宠导航/外观及设置隔离；认证 RPC 的 TimeCalendar 组件通过同一新建同步日程连续拖动、慢回执、页面滚动落点一致。实际 worker 使用假模型经认证 WebSocket 调用真实共享日程与自动化服务。没有向真实飞书测试写入；真实模型编排质量、用户配置 MCP、原生宠物窗口和发布包未重新实机验收。

本机日志位于 `E:/Learn_Project/output/calendar-stability-2026-10-28/`：以 `evolution-full-python-final.log`、`tebb-final.log`、`work-tebb-final.log`、`evolution-frontend-final.log`、`evolution-types-final.log`、`evolution-build-final.log`、`evolution-ui-final.log`、`repeat-evolution-final.log` 为准。较早全量日志曾随会话中断，不能把未结束日志当成失败或成功；已以上述最终日志完成复核。月、事项与桌宠截图为 `month-view.png/work-owner.png/pet-page.png`。

## 日程请求规划修复（#151，2026-10-28）

用户请求在 route 后进入 plan，但原规划请求没有工具，只要求正文 JSON。实际模型因阶段能力不明确回答无日历工具，又在纠正后输出解释混合 JSON，未通过 parse_plan，两回合耗尽后误报通用预算失败；没有进入日程调用，也没有创建日程。

规划现在提供 submit_plan 结构化提交，明确本阶段只分配、执行阶段给tebb真实日程/自动化工具。仍校验成员、依赖、任务数量及一层边界，不提高预算、不重放旧请求。兼容合法正文 JSON；两回合纠正仍无效时明确“委派计划格式纠正后仍无效，未执行任何日程或其他业务操作”。新建缺少结束时间/时长先追问，不擅自扩大为飞书或多人会议。

验收增加无工具误解→结构化纠正、非法成员有限失败及实际规划→自身任务→认证RPC→本地保存链路。另以当前 mimo-v2.6-flash 和隔离数据实测：缺时长的 route 先追问，时间明确的 plan 返回tebb自执行计划；不派发返回任务、不创建真实日程。

#149–#151 最终验证：后端全量678项，664通过、14按既有条件跳过，无失败；前端172项、TypeScript、生产构建通过。日志位于 `E:/Learn_Project/output/calendar-fixes-2026-10-28/` 的 `backend-final.log/frontend-final.log/types-final.log/build-final.log/ui-final.log/real-model-readonly.log`。两周飞书只读核对和真实模型route/plan均完成，不重放用户失败任务、不对真实日程做测试写入。


## 目标核对与主任务（#154，2026-10-28）

当前行为取代 #151 之前的强制规划与自执行子任务：先用无业务工具的 route 核对目标，明确简单目标直接执行；确有独立职责工作时才计划，服务和 worker 双重拒绝自我委派。最多一层其他伙伴子任务，最终 tebb 汇总在原主任务完成。没有第二套模型循环或队列。

目标不清楚时调用 request_clarification，原记录进入 needs_input（待补充），显示具体问题与“尚未开始执行”，不占模型执行名额、不读取项目/搜索/委派。主窗口、浮动聊天消息和任务卡提供“补充目标”；沿用 send＋run＋request_id，在原事务追加补充并继续同一根编号，可幂等重试或取消。事项不随执行自动改变业务状态；同一事项待补充时不重复派发。自动化显示“待补充目标”，不重复本轮目标、不占同类任务执行名额；停止与重启保留此状态并重新观察原记录，用户补充或取消后更新同一回执。

默认聊天和执行页展示一个主任务，展开查看职责伙伴、任务目标、阶段、工具、耗时、结果与整链调用数。主任务自身的阶段不是自己的子任务。旧 route/plan/自任务历史保留，通过 chain 派生视图显示整体结果；子节点失败不能被成功的计划或后续取消掩盖。执行中的耗时由界面计算，不因读取时钟不断改变后端快照和同步哈希；旧历史没有真实结束时间则不伪造耗时。桌宠采用同一根任务状态，原有单击屏蔽整链继续生效。

AI 调试复用认证 load_collaboration 的 run.chain、phases、clarification，以及原任务队列和调用日志。补充入口：collaboration_action({action:{type:"send",conversation:原conversation,run:原run_id,text:"具体目标与期望结果",request_id:新编号}})，重复相同编号不追加第二次。取消仍用 type:cancel、id:原run_id。等待期间 tick 不提交模型任务，重启可读取原状态；每阶段独立预算，calls 与 chain.calls 保留真实总数。

验证命令：`python -m unittest tests.test_execution_flow tests.test_collaboration tests.test_tebb_execution tests.test_automation_service -v`；认证RPC用 `tests.test_local_api` 的 awaiting/search/tebb 三项。真实 mimo-v2.6-flash 的 test、缺结束时间、简单转换、明确职责分工4例各一次模型调用，前两例追问，后两例分别选择direct/delegate，业务工具调用均为0；不会把此只读路由验收当作真实飞书写入验收。Edge 验证搜索测试按钮、待补充入口、原任务续接与单张任务卡，模型与业务写入为隔离替身。记录在 `E:/Learn_Project/output/search-orchestration-2026-10-28/`。

搜索已独立，见 [搜索服务与稳定性验收](SEARCH.md)；架构取舍见 [ADR0021](adr/0021-execution-readiness.md)。


#154/#155 交付验证：后端全量691项（677通过、14按既有条件跳过，无失败）；最后消息顺序与日志收尾后，相关114项复核通过。前端174项、TypeScript和生产构建通过；Edge搜索设置及原主任务续接通过。日志以 `backend-final.log/backend-affected-delivery.log/frontend-final-delivery.log/types-final-delivery.log/build-final-delivery.log/flow-ui.log/model-readiness-live.json/search-live-final.json` 为准。保留首轮失败/旧测试适配记录，不把中间日志当最终结论；无真实飞书写入，不发布。
