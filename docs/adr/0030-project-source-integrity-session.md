# 从已有项目服务会话取得源码完整性回执

#205沿用户统一推荐授权实施。目标是让Agent取得原提交与原跟踪文件的实际观察，解决原Python项目已运行而审阅缺证的问题；不改写旧成绩，不保证全部运行行为或从未修改。

本机官方sbx v0.50.1的exec会启动已停止实例，没有已证实的原子no-start/预期UUID选项。单纯先查running再exec无法排除外部停止或同名替换。选择复用project_start已授权建立的官方exec -i前台连接：固定Python包装器启动原Web命令并转发输出，stdin只接受固定Git检查请求。完整性接口只向原Popen对象发请求，不再exec/capture/reconcile；没有存活控制会话则unknown，不为检查启动或重启。

通道绑定原environment/service/start-operation/UUID与实际owner握手。每次guest启动有新代际，旧请求不能检查新代际；后台重启不恢复PID或通道。Web stdout/stderr先封装再转发，与固定元数据分流，不能从日志JSON/前缀推断可信回执。Web子进程退出时包装器退出并保留原退出码，不能把包装器活着算Web就绪。

完整性操作与产品stop串行；停止、断管、替换、取消、超时唤醒并失效待答请求，不重连、不误停其他UUID。操作按read类别保留已就绪服务，检查失败不自动停止Web。首版仅在存活的非交互Web服务控制会话中采集；旧服务无通道时不自动重启。Web的stdin与元数据输入隔离。

原基线来自prepare新clone检查点；旧成功prepare可按其原结果核对，不能拿恢复时的当前HEAD冒充原基线。固定Git读取禁可选索引写入、fsmonitor/external diff/textconv等可执行配置，遇到隐藏索引标记或不支持范围保留未知。输出含实际UUID/owner、观察时间、基线与当前commit、跟踪文件变化、完整性/稳定性以及untracked排除范围，不扩大为全部源码或运行时安全保证。普通并发编辑仍非全仓原子快照。

复用认证项目RPC、稳定工具请求、日志和project_receipt/反馈材料；模型无任意元数据命令入口。停止前取得回执，验收不再为只读检查隐式恢复已停VM。验证假SDK/真实本机PIPE、Git反例、取消/断管/代际/输出伪造与完整回归；真实公开项目模型样本另定有限计划，保留全部失败，不重放旧unknown或重启daemon，不改冻结0.4.1。

拒绝本地 `core.worktree` 重定向，不能让另一份干净工作目录代替原项目。实际临时仓库反例先保留失败，再验证返回 unknown；这项保守拒绝在原生样本冻结后补充，另以定向回归及最终精确 CI 覆盖，不改样本源码或原成绩。

事实来源：[官方exec](https://docs.docker.com/reference/cli/sbx/exec/)；[官方API/SDK入口](https://docs.docker.com/ai/sandboxes-api/)面向cloud，不据此假设兼容local。
