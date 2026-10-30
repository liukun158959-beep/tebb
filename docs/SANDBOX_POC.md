# Docker Sandboxes 本地验证

关联 #190。首批确认范围为 Windows 11、本机 Linux 沙箱、公开 GitHub 的 Node/Python Web 项目，允许安装依赖、保留环境，并提供本机预览、日志和停止。私有仓库、GPU、Windows 桌面项目与云沙箱不在本次范围。

## 当前状态

2026-10-31 已通过 Windows 11 / sbx 0.50.1 的真实 Node/Python 项目验证。先在 built-in shell 验证，再在固定 digest 的官方预发布 v3 shell 重做：实际拉取 Express/Flask 固定提交、安装依赖、启动Web服务、宿主loopback访问、日志/进程检查、撤销端口、停止环境及确认会话退出。环境磁盘保留；这是当时的PoC范围，后续#191产品通道和独立Agent验收见[PROJECT_RUNTIME.md](PROJECT_RUNTIME.md)，原离线终端保留。

最初代理启动daemon时，官方shim连接console socket报 `An invalid argument was supplied`。用户在普通PowerShell启动daemon的受控对照通过，之后代理复用该daemon完成上述验证；启动上下文有影响，底层根因尚未确定。[Docker #719](https://github.com/docker/sbx-releases/issues/719)只保留为相同表象线索。未自动重启Windows、reset或清理缓存。

本机构建自定义v3 kit时，官方builder启动在约6分钟后返回504，尚未进入项目Dockerfile构建；只读history查询也未在30秒内返回。保留失败记录，停止本次新建的8GiB builder回收资源，保留缓存和用户daemon。后续采用官方已发布v3 shell，OCI元数据先核对再按digest创建，没有再次构建至成功。

v3实测使用Node 24.22.2、Python 3.14.10。项目管理器持有真实Web进程的前台 `sbx exec` 会话，两个服务持续约67秒后仍返回预期HTTP内容，guest进程仍活着；显式stop后两条会话均以143退出、预览不可用。该路径符合[官方session/idle契约](https://docs.docker.com/ai/sandboxes/usage/)，无需本机自定义long-running kit；不能只让nohup进程或端口代替活跃会话。

`bubblewrap` 是成熟的隔离组件；当前不足来自产品配置的完全禁网、短命令时限和缺少常驻服务/端口生命周期。将执行环境换成 microVM 仍需显式对接项目授权、任务取消、服务状态和预览，不能仅替换命令前缀。

## 前置与官方命令

依照 [Docker 安装说明](https://docs.docker.com/ai/sandboxes/install/)，本地环境需要 Windows 11 x64、Windows Hypervisor Platform 和 Docker 登录；不要求另外安装 Docker Desktop。系统组件的启用和可能要求的重启由用户控制，不由 tebb 自动完成。

```powershell
winget install -h Docker.sbx
sbx version
sbx login
sbx diagnose --format json
```

全新安装须选择网络策略。用户已允许下载开发依赖的验证使用官方推荐 `balanced`，不使用 `allow-all`。若已有组织或个人策略，不覆盖；应检查现有策略是否允许所需来源。

```powershell
# 仅全新、尚未初始化网络策略时执行。
sbx policy init balanced
sbx create --name tebb-poc-example --skills off --cpus 2 --memory 2g docker.io/docker/sbx-kit-shell@sha256:a72c2ead8a2ab26ac552e4578ea0e23b5e777e81e8a90108e676be09cffa69a4
sbx exec tebb-poc-example bash --noprofile --norc -c 'git --version; node --version; npm --version; python3 --version'
sbx policy check network --sandbox tebb-poc-example --json github.com:443
sbx ports tebb-poc-example --publish 127.0.0.1:18080:3000/tcp4
sbx stop tebb-poc-example
```

`create`不传宿主PATH；`run`首次创建时省略PATH可能默认挂载当前目录，不能互换。创建的v3实例实际WorkspaceDir/AdditionalWorkspaces/SourceRepoDir为空，Skills=off，KitSSHAgent无授权；声明未请求SSH、credential或host-mount，guest无SSH_AUTH_SOCK，凭据Scope/Services/Sources/Values/CustomSecrets为空。Spec仍保留全局SSH管道配置及凭据绑定文件路径，这些配置指针不等同于v3能力授权，不能据其非空声称注入了密钥，也不能据无workspace声称全部宿主连接皆不存在。

本轮既有治理实际允许GitHub/npm/PyPI，无需改规则。后续如需授权，官方 `policy allow network --sandbox NAME ...` 只针对该环境；kit allow与全局/组织/deny规则合并，不保证只有列出的域名可访问，不覆盖用户全局策略。见[治理契约](https://docs.docker.com/ai/sandboxes/governance/concepts/)。端口显式绑定loopback，stop保留磁盘，rm是独立操作。

## 验收依据

固定公开样本源码：Express `1c6f1b0fe4000977b9a7b4f34097e4d527dd9824` 的 `examples/hello-world`；Flask `49943b5a94413a530761350f04e896f76e8d620e` 的 `examples/tutorial`。来源分别为 [Express](https://github.com/expressjs/express)、[Flask](https://github.com/pallets/flask)。

逐阶段保留命令退出码、实际源码提交、依赖安装记录、服务日志和 HTTP 内容检查。不能只用启动命令退出码判断成功；服务需在命令返回后仍存活，预览必须能从宿主 loopback 实际访问。停止需获得实际回执，失败或超时必须记录原阶段，不能把重试后的结果覆盖原失败。

本地材料位于工作区上级output：`sbx-native-190/`（用户启动上下文对照）、`sbx-node-python-190-attempt1/`（built-in完整链）、`sbx-v3-190-attempt1/`（builder失败）、`sbx-v3-prebuilt-190-attempt1/`（官方v3创建及白名单绑定核对）、`sbx-v3-foreground-190-attempt1/`（完整前台服务与停止证明）。官方registry元数据/digest及session/策略资料在 `sbx-v3-builder-facts-190/`。原失败与后续通过分开保留，临时环境均已停止。

#190到此完成可用性验证；后续#191的产品授权、状态、日志、创建/继续/停止及真实模型结果独立记录在[PROJECT_RUNTIME.md](PROJECT_RUNTIME.md)，不能用PoC代替Agent验收。一次性命令与常驻服务分别持久化，任务完成不能隐式重复启动服务，后端失联先观察原环境。`sbx`及kit接口属于官方Early Access/experimental能力，当前固定已验版本与镜像；不据PoC宣称任意GitHub项目均可运行。普通文档/源码提交不自动进入已冻结0.4.1。
