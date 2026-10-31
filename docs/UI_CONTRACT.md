# 工作区共享控件与反馈

对应 #124，按本批“对齐全部采用推荐方案”的授权实施。保留当前温和工作区、深浅主题、导航、看板和日程，首批在真实看板与日程落地共享控件；其它页面按功能需要迁移。

## 职责与复用

工作区主题拥有语义颜色、字号、留白和圆角 token；`ui-controls.tsx` / `ui-controls.css` 拥有公共控件及状态；页面保留卡片、栏位、编辑器和拖动区域的布局。既有 `.desk-btn` 定义集中到共享样式，不再在根样式复制。`--wb-*` 继续作为工作区主题的兼容别名。

- `UiButton` 保留原生 button，默认 `type="button"`，表单提交显式使用 submit。提供默认、主要、危险样式，支持 hover、focus-visible、active、aria-pressed、disabled 与 busy。busy 同时提供可读文字、aria-busy 和 disabled，复用 LoadingText。
- `UiField` 为单一控件关联 label、帮助和错误；保留已有 id/aria-describedby，自动 id 在重渲染中稳定。首批使用原生文本输入；其它包装组件需要转发 id 与 ARIA。普通输入/select/textarea 使用主题外观；checkbox/radio 保留既有专属呈现。错误不替换输入节点或清空内容。
- `UiFeedback` 区分 loading、empty、no_results、success、error、conflict、unconfirmed。后三种使用 alert，其余使用 status；加载另有 aria-busy。文字直接表达状态，不依赖颜色。控件不发请求、不判断业务成功，也不添加重试按钮。

保存、冲突和原回执仍由 WorkBoardController、ScheduleWeekController 与 LocalCalendarController 拥有。结果未确认时保留原编号与草稿，暂停新的保存；它与正在执行不同，不显示忙碌动画。核对、明确重试及恢复继续调用原控制器接口。

## 真实页面规则

看板首次未读完时，列数显示“—”，不提前显示空列。读取完成后区分无事项与筛选无结果；字段验证失败保留输入，修正本地标题校验错误后移除该提示。未保存提示关闭后回到原输入；详情关闭后回到打开它的控件。

日程的新建/编辑表单是非模态编辑器：打开时进入标题，关闭或完成后返回原控件，日历仍可用于预览调整。继续沿用既有草稿取消、已存修改保护和飞书显式同步规则。空态只在读取有效、没有待处理的本地保存/核对和错误时展示；不把读取失败显示为空日程。

真正的模态工作区窗口继续复用 WorkspaceDialog 的原生 dialog、焦点范围、Escape 和关闭返回；看板已有侧栏键盘范围继续有效。PageStack 保留输入与滚动，隐藏页停止原有轮询。看板菜单/键盘与日历键盘调整仍走相同的业务控制器。

LoadingText、Framer Motion、dnd-kit、FullCalendar 继续承担现有动效与拖动。共享控件不接管拖动 transform 或几何；减少动效时关闭新增按钮过渡，既有加载文字和拖动降级继续复用。forced-colors 使用系统焦点色与边框，状态仍有文字。

## AI 调试与验收

在 `client/` 运行 `pnpm dev`，打开其输出的本地地址并追加 `?debug=work`。页面使用真实 WorkBoardPane 和日程组件及明确标注的内存示例，不调用模型、不写用户数据或真实飞书。展开“共享控件状态样板”查看按钮、字段及七种反馈；下方真实看板/日程使用同一组控件。

```powershell
# client/；隔离模拟服务，无模型费用或外部写入
node --test tests/ui-controls.test.cjs tests/work-board.test.cjs tests/schedule-week.test.cjs
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

控件测试检查原生语义、稳定字段/描述关联及不同反馈；实际看板测试模拟“写入后连接断开”，检查保留标题、未确认时停止忙碌、重新读取原回执及后续正常操作。日程原有取消、键盘、拖动和无写入保护测试继续执行。

浏览器验收包含深浅主题、600 像素窄窗口、字段帮助/错误关联、键盘可见焦点、未保存提示的焦点返回，以及真实看板和日程交互。减少动效与 forced-colors 提供组件/样式降级；当前浏览器验收未切换系统偏好，不将其记为实机偏好切换通过。此项不需要真实模型调用；业务链路与真实模型结果仍使用各自的认证接口和统一任务评测入口。
