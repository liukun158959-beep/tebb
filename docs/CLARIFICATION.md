# 补充问题与待你处理（#156）

2026-10-29 用户确认：聊天提供可选问题卡，每次只问必要信息，支持自定义和原有文字补充；选完点击“继续执行”才续接原主任务。全局“待你处理”覆盖所有会话，补充或取消后移除，重启保留；不创建飞书待办或业务工作卡片。

主窗、浮动聊天和全局待办复用 ClarificationCard。没有默认选中，不因点击选项或打开待办而执行。跨会话提交显式绑定原 run/conversation，不依赖当前选中会话，不覆盖当前会话草稿。待办由真实 needs_input 根任务派生，原 state.requests 只继续服务演示确认，不维护第二份可变待办记录。

问题与选项由 clarification.py 校验，最多3题，每题2至4个普通选项，界面另提供自定义。新 request_clarification 可提交 questions 或 calendar_duration 模板；旧的 question:string 保持兼容。已明确日期和开始时间、只缺时长的日程使用固定30分钟/1小时/1.5小时选项，不让模型计算相互矛盾的结束时间，也不重复确认默认本地保存。选项不是日程写入，实际创建仍由既有日程服务校验和回执。

旧问题点“生成可选答案”：已识别的日程时长由本机模板补齐（零模型调用）；其他类型进入既有单槽聊天队列，模型只有 submit_questions，无项目、网络、业务工具，至多2次调用。失败保留原问题和文字入口，登记/读取结果不明不在 tick 自动重试；取消或文字补充会取消仍在生成的选项。等待期间不占执行名额，只有用户主动生成通用选项时临时使用模型名额。

每次问题有版本。提交只接收已存选项ID或用户自定义内容，检查全部问题、版本、任务、会话和状态；重复 request_id 幂等，跨窗口竞态仅一个答案能被接受。续接按用户可见 label 组成补充文字，隐藏 value 不转成用户指令或额外授权。新问题使旧版本失效，前端按版本更新表单。目录权限、工具白名单及飞书副作用规则不变。

相对日期以原提问时刻解释，补充阶段传递原 created 与 supplemented_at。例如10月28日23:21问“明晚20:00”，到10月29日补充仍是10月29日20:00；不得无声改成10月30日。若既定时间已过去，沿用业务服务拒绝新建过去日程的规则，先由用户明确改期。

## AI 调试与验收

复用认证 collaboration_action/load_collaboration，无新认证入口。先读取 needs_input 根任务，取得 id、conversation、clarification_version、clarification_form 及 options_generation；phases/calls 记录本机模板与模型生成的真实调用数，用量复用原 usage 记录。

```json
{"type":"clarification_options","id":"原run_id","conversation":"原会话","version":"当前版本","request_id":"本轮新编号"}
{"type":"clarification_answer","id":"原run_id","conversation":"原会话","version":"当前版本","answers":{"duration":{"option_id":"one_hour"}},"request_id":"本轮新编号"}
{"type":"clarification_answer","id":"原run_id","conversation":"原会话","version":"当前版本","answers":{"duration":{"custom":"持续2小时"}},"request_id":"另一个新编号"}
```

通过 `rpc(info,"collaboration_action",{action:以上对象})` 提交，info 使用现有后台端口/token。上述答案结构按读取到的问题ID/选项ID填写，不是固定通用业务参数；示例不可对未核对的真实任务直接重放。重试同一操作保留编号，先读取核对状态。

```powershell
python -m unittest tests.test_clarification tests.test_execution_flow tests.test_collaboration tests.test_tebb_execution -v
python -m unittest tests.test_local_api.LocalApiTests.test_structured_clarification_roundtrip_is_bound_versioned_and_authenticated -v
cd client
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

本机材料位于 `E:/Learn_Project/output/clarification-2026-10-29/`：browser.log、inbox-options.png、resumed-original.png；浏览器使用真实 LiveCollaboration、认证RPC、隔离数据和队列替身。验证从写作对话中选择另一会话日程的时长，选中不恢复、明确提交才续接、原编号保持、待办移除、写作草稿保留。无真实飞书写入。

真实 mimo-v2.6-flash 另验：auto搜索实际返回任务申请，日程追问返回结构化选项，补充1小时后保持原绝对日期。首轮旧问题生成有格式失败，后续还出现“30分钟=21:00结束”和多问默认保存位置；记录保留，不能算成功。最终已识别日程时长改用本机模板，2次均零模型调用返回正确选项；通用问题仍可能模型失败，保留原问题供重新读取/重试，不宣称所有模型始终遵守格式。

最终受影响集成回归111项通过（含补充、真实派发、协作、任务、认证RPC与网页地址校验），前端177项通过，类型检查和生产构建通过。真实浏览器再次验证跨会话待办和原任务续接，见browser-final.log；外部写入始终使用替身。
