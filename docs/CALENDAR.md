# 本地日程与飞书同步

当前实现包含 [#142](https://github.com/liukun158959-beep/tebb/issues/142) 的连续拖动与嵌入布局修复；原始需求对应 [#140](https://github.com/liukun158959-beep/tebb/issues/140)、[#141](https://github.com/liukun158959-beep/tebb/issues/141)，用户已确认本地默认、单条/整体同步、交互及失败边界。旧 #136/#138 设计保留为历史，以本文为当前规则。本批不发布。

## 使用与交互

日程默认本周，周一至周日、北京时间 UTC+8；单日只显示所选一天。时间轴融入主页面，去掉独立外框及内部纵向滚动盒，随页面滚动；保留浅色时间网格和日期列分隔，整点实线、半点轻虚线，方便对齐时刻。窄屏周视图保留七列，仅在日历内部横向查看。今天是定位按钮，不再是独立汇总页；桌宠“今天的安排”定位当天的周日历。飞书待办及手动本周复盘在“更多”中，删除待办仍是真实删除。

- 双击空白生成一份虚线临时草稿，默认一小时、15 分钟吸附；旁边无蒙层表单，窄屏放在时间轴下方。再次双击空白移动同一草稿并保留输入。标题必填，明确保存前不写本地或飞书。未提交的新草稿即使已填标题、勾选同步，取消、×和Escape都直接丢弃，不显示保存确认。已存日程的未保存修改继续确认；已发出保存但结果不明时保留原编号核对，不能当作未保存草稿丢弃。
- 单击日程条打开详情，再点同一条关闭，点其他条切换；不常驻底部详情。相同时段并排，纵向长度对应持续时间，相邻短日程不互相覆盖。短日程采用紧凑标题，完整时间在提示及详情可读。
- 按住约 250ms 抬起卡片，原位淡化、落点预览，松手提交；无效落点、Escape、时间未变不写入。上下边缘缩放，15 分钟吸附；键盘空格抓取，方向键移动，Shift/Alt 加上下调整结束/开始，Enter 保存、Escape 取消。减少动效时取消弹动。
- 日/周完整显示 00:00–24:00，保留网格，没有凌晨折叠入口。月视图读取完整可见日期（含相邻月）；点日期进入单日，双击空白可新建草稿，点日程看详情，拥挤时展开当日列表。月视图不开放拖动或缩放，精确调整进入日/周。草稿时间变化只让草稿轻弹，时间轴不跳转。同步和新建按钮等宽等高。保存中仅在日程条及固定状态行反馈，不插入会推开时间轴的处理中面板；同一事件确认结束后立即恢复拖动，不明结果仍须核对。
- 日程与事项进度、截止日及 AI 执行相互独立。事项安排入口进入相同草稿表单并预选事项；一场日程至多关联一事项，解除只删关系。归档后需恢复才能新增/调整。

## 事实与职责

本地日程由 `LocalCalendarService` 拥有，和事项共用现有工作区 SQLite 短事务，不另建数据库。`SchedulingService` 合并月/日/周视图，处理既有远端改期与事件预留；`CalendarAdapter` 负责真实 lark-cli 协议、身份和完整参与人检查。Bridge 和认证 WebSocket 提供明确入口，不通过 GUI 或模型执行。

本地身份固定为 `calendar_id=tebb-local, event_id=local_id`；同步映射单独保存在 `remote`，成功后保持同一身份及事项关系，合并远端显示时去重。原生飞书日程保留原身份。读取飞书更新绑定快照；范围内消失的绑定另查详情，区分改期、删除、不可访问及读取失败，不把缺席当删除。本地日程在飞书断网时仍可读写/拖动；远端错误单独显示，旧远端时间标为未核对。

前端 `LocalCalendarController` 管保存/同步回执及固定清单，`ScheduleWeekController` 管时间调整、资格和乱序读取；组件拥有临时输入、日期与手势。复用 FullCalendar 的时间几何、并排与边缘缩放，正文抬起复用事项已有的 dnd-kit。可见远端事件分两路预先核对，使未选中的个人日程也能第一次直接拖动。拖动预览由 CalendarDragSurface 的独立浮层拥有，不作为 FullCalendar 事件注册，避免同一拖拽 ID 重复注册/注销。拖动帧不更新日历事件源，背景读取及资格返回在手势期间失效，不打断操作；提交期间不再对同一事件重新发起资格检查。时间轴自身的确认缓存和回执更新继续有效。

## 同步、权限与故障

默认只保存本地，勾选同步才创建飞书；成功后不提供取消绑定。未同步拖动只改本地，已同步拖动 PATCH 同一远端事件。修改前核对最新时间，有冲突先刷新提示。多人、全天、重复、跨日、已结束、其他组织者或无法完整核实的远端日程仍只读；仅本人组织、写权限、完整名单无其他参与人/资源的普通同日个人日程可调。主日历可写不等于任意事件可写；返回的可选 false 标记缺省不再误判身份未核实。

整体同步预览所有未同步本地日程，确认后冻结 ID/版本清单及当前主日历/身份，逐条执行。日程改动或登录目标变化的条目拒绝；已同步不重复创建，部分失败保留成功项。每份清单最多 1000 条，不自动执行后续清单。

同步先持久登记 `prepared → calling`，再真实创建飞书；取得身份后 `remote_created/register → synced/committed`。超时、断连或登记不明保留 `unconfirmed` 与编号，阻止该日程重复创建。重开先读，不自动重试。可输入/选择远端事件 ID 核对绑定，或明确确认没有创建并封存原编号；已知事件仍存在时不能声明没有创建。绑定前检查原身份、类型、唯一映射及事项归属。没有原回执也须明确封存，迟到请求不能再次执行。本地保存回执缺失同样支持登记当前状态，不盲目另建。

同一事件/关联事项执行及收尾期间拒绝冲突写入，其他事项、AI、读取继续。网络不持数据库、宿主或全局写锁。前端保留一个本地保存/同步待核对标记期间暂停该控制器新写入；持久批次可明确继续仅 `queued` 条目，成功、失败或不明项不自动重发。已有改期继续采用按事件核对标记。

原飞书删除入口移到详情“更多”，仍仅允许原今日快照已读取事件，保留真实删除及通知行为。本地事件本批未提供永久删除。飞书无条件版本，预检不能保证与外部同时修改的原子互斥。断连不承诺取消后台已发出的操作。

## RPC 与调试

沿用进程 token 认证和白名单。检查外层派发结果及业务 `ok`，失败看 `stage/change_state/operation`，不因超时重发。

| RPC / 动作 | 输入 |
| --- | --- |
| `load_local_calendar` | 可选 `request_id`，返回 `events,operations,batches,operation` |
| `change_local_calendar / create` | 新 `request_id`、可选稳定 `local_id`，`summary,start,end,item_id`（空表示独立）；起止含时区 |
| `change_local_calendar / update` | 同上，另需 `local_id,version`；已绑定不在此修改标题/时间 |
| `change_local_calendar / sync` | 新 `request_id,local_id,version`；不重建已有映射 |
| `change_local_calendar / prepare_sync` | 新 `request_id,events:[{local_id,version}]`，返回固定目标 `batch` |
| `calendar_sync_step` | `batch_id,local_id`，仅执行该清单尚未执行条目 |
| `change_local_calendar / resolve_sync` | 新 `request_id,local_id,operation_id,resolution`；`link` 加 `event_id`，`absent` 加 `confirmed:true` |
| `change_local_calendar / resolve_save` | 新 `request_id,local_id,operation_id,confirmed:true`；封存原保存，不重放 |
| `load_schedule_week/load_schedule` | 沿用 `day,refresh,request_id`，合并本地与远端 |
| `inspect_schedule_event/change_schedule` | 本地使用 `calendar_id=tebb-local,event_id=local_id`；`retime/reconcile_time/link/unlink` 原参数兼容 |

复制以下命令只使用临时目录与假飞书，不向真实账户写入：

```powershell
@'
from tests.test_local_calendar import LocalCalendarTests
case = LocalCalendarTests()
case.setUp()
try:
    event, request = case.create()
    assert event['sync_state'] == 'local' and not case.calendar.creates
    result, sync = case.sync(event)
    assert result['ok'] and len(case.calendar.creates) == 1
    print(event['local_id'], sync['request_id'], result['stage'])
    assert case.local.load(sync['request_id'])['operation']['state'] == 'synced'
finally:
    case.doCleanups()
'@ | python -

python -m unittest tests.test_local_calendar tests.test_schedule_week tests.test_scheduling -v
python -m unittest tests.test_local_api.LocalApiTests.test_local_calendar_authenticated_rpc_offline_sync_and_retime -v
pnpm --dir client test
pnpm --dir client exec tsc --noEmit
pnpm --dir client build
```

日志使用 `desk_companion.local_calendar` 的 `calendar operation=<编号> local=<ID> type/stage/state`，与 RPC 调试关联；不打印 token 或日程正文。先查原操作，再查映射及远端事件。`remote_created/register` 不等于未创建；`observed` 只登记当前值，不证明原请求未执行。前端标记为 localStorage 的 `tebb.calendar.pending.v1` / `tebb.schedule.week.pending.v1`；损坏时保留并暂停该类写入，先查后台回执。

实际组件隔离演示为 `client.html?debug=work` → 日程，明确标示演示数据。正式连接使用真实 CalendarAdapter，没有产品开关把同步伪装成成功。认证 WebSocket 与浏览器验收注入 `tests.schedule_fixture.TimeCalendar`，验证真实存储、RPC 和组件，仅远端模拟；不等于真实飞书写入验收。真实写入需用户指定未来个人测试日程，不能用已有会议自动回归。

### 连续拖动回归（#142，2026-10-28）

复现发现，原拖动背景预览也调用了 CalendarDragContent，使用原事件拖拽 ID 注册，结束时注销会干扰真实日程的下一次抓取；每个指针移动还重建事件源，触发 FullCalendar resetOptions。另一个视觉跳动来自保存中在日历上方插入整块恢复面板。现改为浮层预览、稳定的日历事件源和局部保存反馈；事项四列手势保持原逻辑。

复制 `node --test client/tests/local-calendar.test.cjs client/tests/schedule-week.test.cjs` 可验证多次拿起/释放、预览不注册日历事件且不重绘网格、手势期间旧读取失效及保存时禁止重复资格检查。完整前端回归、类型和构建按上方命令运行。浏览器使用真实组件与认证 WebSocket、TimeCalendar 远端边界，检查新建同步后连续改期、1800ms 慢回执、无重复拖拽源、周/单日、页面滚动及凌晨锚点；不写入真实飞书。

2026-10-28 用户补充：嵌入布局仍须保留时间网格。恢复整点/半点横线与日期列分隔，独立外框、内部纵向滚动及 #142 拖拽修复保持原行为。

## tebb 与界面共用操作（#147）

`CalendarWrites` 集中本地/飞书删除：本地只删除记录；已同步或原生飞书记录核对主日历、组织者、完整参与人、原时间后真实删除远端。事项、AI 历史和关联快照保留，关联标记失效。普通已同步日程可在编辑表单修改标题和时间，仍由原改期服务及持久回执确认，标题或时间冲突拒绝覆盖。

删除先保存操作编号，调用结果不明时保留 `local_calendar_operations`，读取不自动重发。界面“核对删除结果”及 AI `resolve_delete` 读取同一回执；远端确认不存在后收尾。仍存在时可明确选择保留当前记录并封存原删除，不推断原请求是否执行；不可访问时保留未知状态，不误报已删除。

月查询入口 `load_schedule_month({day,refresh,request_id})`，读取最多六个可见周并保留每周错误；局部失败不当作整月无日程。隔离验证：`python -m unittest tests.test_schedule_month tests.test_calendar_writes tests.test_schedule_week -v`。

## 反馈修复（#149 / #150）

- 空白日期/时段的双击在同一个时段内识别，单击已有条仍开关详情，不改拖动或松手保存。新建按钮仍明确点击即可使用。
- 2026-09-28、2026-10-12 的实际 `instance_view` 请求成功返回 `data: {}`。适配器之前强制要求 items 数组而误报；现在仅把已确认的成功空对象和显式 items=[] 视为无日程。items=null、非数组、未知非空结构及真正失败仍报错并保留缓存。日、周共用此解析规则，月视图继续汇总真实错误。
- 隔离验证：`python -m unittest tests.test_schedule_week tests.test_schedule_month -v`；真实两周只读重查为空，无外部写入。

#149/#150 验收：前端172项、类型检查及生产构建通过；真实 Edge 日/周/月均验证单击零草稿、原生双击新建、填内容后取消/×/Escape直接丢弃及连续重开。原生双击延后到FullCalendar完成mouseup时段命中回调后消费，不依赖两次dateClick累计；切换日期或卸载清理待处理动作。

## 历史日程编辑（#152）

用户确认开放已结束普通个人日程的标题和时间编辑。身份、组织者与完整参与人仍须核实；多人、全天、重复、跨日和归档事项等现行限制继续。未同步本地编辑不触碰飞书，已同步或原生飞书编辑核对原值后更新同一事件；历史编辑仍适用短时长、冲突及未知结果核对规则。新建/首次同步过去日程仍拒绝；本轮不开放已结束日程删除。

#152 验证：日程/本地保存/删除完整相关48项通过；真实浏览器经认证RPC修改2026-10-23的历史日程标题，保持原时间并确认删除仍禁用。完整前端172项、类型及生产构建通过。复制验收入口：`python -m unittest tests.test_schedule_week tests.test_local_calendar tests.test_calendar_writes -v`。本轮无真实飞书写入，浏览器远端为隔离TimeCalendar。

## 伙伴的本地日程只读能力（#198）

桌面伙伴设置可显式勾选“本地日程读取”；现有配置和默认伙伴不会因此自动获得该权限。普通伙伴只有该权限时，模型工具只有 `read_local_calendar({day:'YYYY-MM-DD'})`，没有创建、改期、删除或远端刷新。tebb现有业务工具保持原有范围，不把此细粒度权限当作对tebb全部业务权限的限制。

该工具复用 `LocalCalendarService.query(day)`，以北京时间筛选跨日重叠事件并排序，返回 `scope:'local_snapshot'`、`remote_refreshed:false`、`events/total/truncated`。最多100条；截断时不能声称列出全部。数据包括本地保存事实及最后同步快照，不代表飞书实时状态；空列表是合法结果，无效日期或读取失败保留结构化错误。工具不接收工作区路径，不能越过当前任务所属工作区。

可直接在既有服务调用 `local.query('2040-01-02')`；认证UI读取仍沿用 `load_local_calendar`。行为回归：`python -m unittest discover -s tests -p test_local_calendar_tools.py -v`。模型验收见 [EVALS.md](EVALS.md#本地日程工具验收198)：合成数据、真实任务进程、真实工具回执、读取前后快照，显式 `--live` 才消耗模型用量。

## 从事项安排时间（#192）

日、周时间轴旁显示现有页面返回的未归档事项，可按标题搜索。拖动专用抓手放入时段，或用键盘激活“安排时间”，都打开原日程确认表单，预填事项标题与真实关联ID。落点按15分钟吸附，默认一小时；跨日、无效落点及取消不创建记录，不缩短时长迁就边界。月视图不提供事项拖入。

草稿、临时日程条和表单共用现有前端状态。只有明确保存才调用原本地日程入口，保持稳定本地编号和事项关联，不推进事项业务状态；飞书同步默认未勾选，结果未知时沿用原编号读取核对，不重复创建。键盘安排默认所选日期09:00，可在表单修改。新抓手可直接拖动，原日程条继续长按拖动。

布局按时间轴容器宽度切换：宽布局的事项栏随纵向滚动保留，周视图维持760px最小内容宽度并独立横向滚动；时间轴可聚焦后用左右键查看。窄布局将事项来源放在上方，打开草稿后定位到表单。表单位置限制在实际可见区域，短窗口可滚动；尊重减少动态效果设置。

这一入口不意味着模型已能生成UI草稿。模型提案协议及真实模型零写入验收由#213单独交付；现有创建工具会实际保存，不能作为草稿接口使用。
