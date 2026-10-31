# 拉取并运行 GitHub 项目

关联 #190/#191。Tauri 主客户端中的“项目环境”能力使用官方 Docker Sandboxes，不在宿主运行下载的项目。首版面向公开 GitHub HTTPS 的 Node/Python Web 项目；私有仓库、GPU 和 Windows 桌面程序不在本轮验收范围。Docker Sandboxes 当前接口仍属预发布能力，本轮固定 CLI v0.50.1 与已验证的官方 shell digest，基础证据见 [PoC](SANDBOX_POC.md)。不据两个样本承诺任意依赖均可安装。

## 官方 CLI 故障诊断（#202）

默认服务原本已有操作日志；现在显式注入的官方 client 也通过同一记录通道保存控制输出，不替换其 runner 或子类、不再次执行命令。`create`、网络、clone、install 等阶段与原 operation 对齐；失败的 `operation.error_details` 保存 `error_code/cli_operation/exit_code/outcome_unknown/diagnostics_available`，原错误回放仍返回原记录。公开错误保持简短，原 `log_id` 即诊断关联号。

通过现有桌面 token 认证 RPC 调用 `project_environments_ui`：`operation=status, payload={environment:环境ID}` 取得原 operation 和错误码；再以 `operation=log, payload={environment:环境ID, operation:原操作ID, offset:0}` 读取 base64 日志。没有新建执行入口。CLI 控制片段最多64KiB，操作总日志仍为4MiB；Bearer/Basic、常见密钥字段、URL认证和查询签名脱敏，其他环境的 ls/policy 标准输出不写入本项目日志。日志记录失败或达到总限额不改变/重放实际 SDK 操作，失败回执会标诊断不可得。

创建回执不明、身份未绑定或原操作 unknown 时，status 明确 `recovery_required=true`。先沿用原 `reconcile` 归属标记核对，不重新 create，不凭同名停止。未在当前清单找到原名只是一时观察，不能据此声称它从未创建。模型诚实报告失败与任务完成状态的统一由 #169 跟踪。

本机无模型回归命令：

```powershell
python -m unittest tests.test_docker_sandboxes tests.test_project_environments tests.test_project_operations tests.test_project_environment_store tests.test_project_receipts -v
```

覆盖注入client/自定义runner/子类、非零退出与超时部分bytes、凭据脱敏、跨scope日志拒绝、未知原请求不重复及日志故障不改变实际结果。不重放此前失败的创建，不自动登录或重启daemon。

## 使用步骤

1. 本机先安装、登录并启动官方 Docker Sandboxes。Windows 虚拟化前置按官方要求配置；“检查连接”仅检测控制通道，不冒充 VM/项目验收。
2. 在伙伴设置显式勾选“项目环境”，再提供公开仓库地址及运行目标。旧角色不会自动获得这项权限，tebb 的身份也不能绕过它。无需先授权本机文件夹。
3. 助手在官方 VM 中拉取源码，返回实际提交和解释器信息，再依据项目文件配置和启动。Python 使用项目 `.venv`，不混用全局 `flask`。
4. 任务卡片的“项目运行/日志”和终端工具栏的“项目运行”打开同一侧栏，可查状态、阶段、日志、预览和停止。日志为只读 xterm；切换页签保留聊天和终端未发送草稿。
5. 模型答复结束后，已就绪的服务继续运行，供用户预览。停止撤销 loopback 端口映射并核对原 VM 已停，代码和依赖保留；下次可读取原项目并重新启动。

仅准备成功显示 `available`，不代表 Web 就绪。只有父后台仍持有存活的前台会话、原 SDK UUID 仍运行且真实 HTTP 200 时，才提供可用预览。状态查询重新核对这些条件，不采用磁盘里旧的 `ready` 或 PID 充当证明。预览链接和 checkout 提交是两种证据，均不冒充研究资料正文已读。

项目任务默认沿用已有编码任务的 12 轮、600 秒，路由仍为 2 轮；用户已明确保存的时间设置优先。工具等待由可信 worker 声明，TaskManager 仅接受 1–360 秒的有界值，且不能突破本次任务总时限。项目工具声明 300 秒等待，父 RPC 360 秒；SDK 创建等原操作即使回执滞后，也先核对原记录，不因等待超时新建第二份。精细 token/费用硬预算继续由 #170 跟踪。

## 职责、隔离和兼容

`ProjectEnvironmentService` 持有代码/依赖、官方 SDK 身份及服务前台会话；`EnvironmentStore` 只做短 SQLite 事务，拥有环境与操作回执。`SandboxesClient` 是官方 CLI 的薄适配，不实现虚拟化或另建容器引擎。Collaboration/TaskManager 继续管理目标、实际模型工作进程和操作取消；没有第二套 Agent 或任务队列。

- 仅创建固定官方 OCI kit，不执行仓库自己的 `sbxenv.yaml`，不传宿主目录，不开放 SSH、产品/模型密钥转发或宿主 shell 回退。CLI 子进程清理产品 RPC 和常见 provider/token 环境变量。
- 代码在 VM 的 `/home/agent/project`。目录和依赖保留在该环境；项目文件工具拒绝绝对路径、父级穿越及解析后逃出根目录的路径。
- 下载依赖时仅修改对应 sandbox 的网络授权，并检查实际规则。六个依赖域名与 Docker 全局/组织/deny 规则合并，**不是独占白名单**，不改全局网络策略。
- 仅发布 `127.0.0.1` 端口。改配置或执行命令前停止已就绪服务；只读文件/目录操作可与服务并行。缺失文件这种已结束的只读失败不会误停 VM。
- 同时最多两个正在操作或运行的环境；未知操作占用准入位置。单命令 1–240 秒、日志前 4MiB、文件读取 64KiB、单次写入 8KiB、目录最多 200 项并标记截断。未知配置/网络/创建回执不自动重放。
- 现有本机目录授权、WSL + bubblewrap 离线终端及历史记录保持原身份。公开仓库运行走新的官方 SDK 通道；本次没有把旧终端的所有命令和磁盘静默迁入 Docker。两者的入口与边界见 [终端](TERMINAL.md)。

## 取消、退出与恢复

创建前保存环境名、操作和归属标记，取得官方 UUID 后绑定原记录；同一请求及同项目的未决准备操作返回原回执，不重复创建。取消尚未就绪的操作会核对停止；迟到的创建回执仍需绑定并停止，不报告准备成功。已就绪服务由父后台持有，不受模型 worker 结束回收影响。

正常退出并行停止所属 VM，保留磁盘。SDK 停止有界，原生壳保留约 100 秒退出宽限；无回执或超时留下“不明”，不能宣称一定停完。异常退出后将旧活跃操作/服务标为待核对，旧预览失效，不按持久 PID 接管或杀进程，也不自动重建、重启服务或重放配置。

`reconcile` 默认只观察原候选。ID 未知时，用户明确选择“启动候选并验证归属”才读取固定 `TEBB_ENV_ID` 标记；匹配后绑定原 UUID。已知 UUID 与同名实例不同则拒绝，既不探测也不接管。验证不执行项目代码；回执丢失后即使找到仓库，也不把原准备操作改报完整成功。核对原文件、日志后再按需配置。

## AI 调试与验收入口

模型调用链为：`project_*` → `worker_rpc` → 带 token 的 `project_operation` → 父后台服务 → 官方 CLI。父后台从真实运行快照、当前物理任务、collaboration 会话和显式能力推导归属，不接受模型传入 `scope/owner/request_id`。实际 tool call ID 参与稳定请求编号；已取消、过期阶段、跨项目和无权限调用在 SDK 操作前拒绝。项目 RPC 不等待聊天全局锁。

| 工具 | 输入与结果 |
| --- | --- |
| `project_prepare` | `repository, ref?`；环境 ID、真实提交、解释器、准备回执和日志 |
| `project_read` | `environment, path`；目录 `entries/truncated` 或文件 `text/encoding/sha256` |
| `project_write` | `environment, path, text`；VM 内写入及真实退出码 |
| `project_execute` | `environment, command, timeout?`；有界命令、退出码、stdout 和日志 |
| `project_start` | `environment, command, port, expected_http_text?`；前台服务、真实 HTTP 及 loopback 预览 |
| `project_status / project_log` | 原环境；实时状态或按原操作 ID、字节 offset 读日志 |
| `project_stop / project_list` | 停止原 UUID，或查询当前项目/会话的保存记录 |
| `project_reconcile` | `environment, verify?`；观察原候选或明确验证归属，不重建 |

`expected_http_text` 是 HTTP 正文条件，不能填终端的 `Listening/Running` 日志；不确定时省略。工具保留结构化失败，不把失败对象包装成已运行。`project_receipt` 事件关联 call/environment/operation/log 编号，完整模型请求及工具返回仍见 [调用调试](AGENT_DEBUG.md)。

桌面 token 另可调用 `project_environments_ui(operation, payload)` 的 `probe/list/status/log/stop/reconcile`，用于独立核对和停止；它不提供任意创建/执行命令入口。浏览器协作预览 token 无权使用这些方法。

离线回归（临时数据、假 SDK/模型）可复制执行：

```powershell
python -m unittest tests.test_docker_sandboxes tests.test_project_environment_store tests.test_project_environments tests.test_project_operations tests.test_project_receipts -v
python -m unittest tests.test_tasks tests.test_collaboration tests.test_local_api -v
```

真实验证需要显式选择隔离模型、普通用户的 SDK 控制通道及新数据目录，不在回归中自动开启。可在隔离本地后端设置测试模型后，使用下列认证 RPC；`TEBB_RPC_PORT/TOKEN` 取该后端的开发启动参数，不能使用浏览器预览 token，也不要把 token 打印或提交。Windows 代理执行隔离环境可能不能连接同一 loopback，应在实际普通用户上下文核对，不据此修改全局代理或重启 daemon。

```python
import json, os, uuid
from websockets.sync.client import connect

def rpc(method, **args):
    port, token = os.environ['TEBB_RPC_PORT'], os.environ['TEBB_RPC_TOKEN']
    with connect(f'ws://127.0.0.1:{port}/ws?token={token}', proxy=None) as ws:
        ws.send(json.dumps(dict(type='rpc', method=method, args=args)))
        frame = json.loads(ws.recv(timeout=80))['result']
        if not frame['ok']: raise RuntimeError(frame['error'])
        return frame['result']

state = rpc('load_collaboration')['state']
partner = next(item for item in state['agents'] if item['id'] == 'research')
rpc('collaboration_action', action=dict(type='agent', agent={**partner,
    'tools':['项目环境']}, request_id=uuid.uuid4().hex))
admitted = rpc('collaboration_action', action=dict(type='send', mode='task',
    conversation='dm-research', request_id=uuid.uuid4().hex,
    text='实际运行 https://github.com/heroku/node-js-getting-started，'
         '固定提交8b8dec11fcf027522a857e0c831ee01b7124f673；'
         '用原npm start和3000端口，不修改源码/测试，给真实预览和日志，答复后保持服务。'))
print([item['id'] for item in admitted['state']['runs']])
# 轮询 load_collaboration 查该run；list_debug_calls按collab-<run>查真实调用。
items = rpc('project_environments_ui', operation='list')['environments']
if items:
    ident = items[0]['id']
    print(rpc('project_environments_ui', operation='status', payload={'environment':ident}))
    # 用户验完后明确停止，不因答复结束停止：
    # rpc('project_environments_ui', operation='stop', payload={'environment':ident})
```

验收必须检查真实提交、配置/命令、HTTP、任务结束后持续访问、日志及实际停止，不只看任务 `succeeded`。Node 样本用原 `npm start`；Python 样本为 Flask `49943b5a94413a530761350f04e896f76e8d620e` 的原 `examples/tutorial/flaskr`，用项目解释器安装教程并初始化 SQLite，不另写 hello server。真实模型每轮在新目录声明次数/时间上限，保存成功、失败、取消及不完整；更改后重验与原失败分开记录。完整质量基线仍见 [EVALS](EVALS.md)。

## 2026-10-31 实测记录

最终同一版15文件后端快照，MiMo v2.6-flash 的Node/Flask各1个任务分别7/9次真实调用：固定提交、跟踪文件无修改、真实日志及HTTP200、任务结束8秒后仍可预览、停止和后台退出均通过。原始第5轮Node脚本在目标完成后因日志辅助函数参数重名退出，未开始Flask；原报告保留，另核对原日志与停止，业务源码不变，只补跑未开始的Flask。两份源码副本哈希一致，不把前四轮失败并成成功率。

零模型的实机验证另覆盖：创建后故意丢回执，找回原标记/UUID且只创建一次；目录列表、缺失文件不误停VM、停止后恢复读取64KiB内容；在原VM真实执行sleep后撤销任务存活信号，7.36秒内登记取消并确认SDK停止。后者是父服务取消接口实测，不冒充完整模型取消样本。

另在原Node测试VM实际运行期间退出正常 `server.main`：14.86秒内后端正常退出、原UUID停止、前台CLI消失且预览不可访问，未强制杀后端。该用例无模型调用、不新建VM，验证的是父后台退出收尾；原生壳另通过完整离线Cargo检查。

完整本地后端890项中无断言失败，1个既有压缩RPC在loopback握手阶段超时；原报告保留，同版隔离复测25.5秒通过，未为它修改压缩业务逻辑。13项按沙箱opt-in/Windows符号链接权限条件跳过。最终前端隔离205项通过、类型检查/生产构建/Cargo离线检查通过；原工作树仍有用户桌宠改动造成的1项DOM夹具失败，未覆盖用户源码。提交后由完整CI再次复核；这次本机网络超时不能改写成单批全绿。

首个6031c4d的Windows完整CI另发现新增目录测试的8.3路径别名问题：子路径解析成长名，根路径未解析，导致同一目录被误判越界。本机已用真实短路径复现，读写根统一规范化后49项项目相关回归通过。该修订不重算前述16次模型成绩；实机读取补验单独保存于 `output/project-191-canonical-physical`，完整CI通过后再进入main。

本机材料保留在工作区上级 `output/project-agent-191-live-attempt1…5*`、`project-191-receipt-loss-physical`、`project-191-cancel-physical`、`project-191-shutdown-physical`。第1轮权限身份错误、第2轮HTTP条件/解释器/stdio契约、第3轮缺失读取误停及300秒边界、第4轮工具等待未被父管理器采纳和额度耗尽，均保留原始失败。接口修订后才独立重验，不把排队成功当运行成功。模型临时配置已删除，已记录worker和测试环境均已停止；原始载荷不上传仓库。
