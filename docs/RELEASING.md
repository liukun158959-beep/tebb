# Windows 发布构建

自动检查与手动候选构建见 [CI/CD](CI_CD.md)。`release-candidate.yml` 默认只产生 Actions 候选资产；正式发布继续需要独立指令，不覆盖历史版本。

当前产物是独立 x64 便携 ZIP。发布前需执行 Python、前端、Rust 和隔离运行时验证；发布脚本拒绝个人配置与日志。不要把整个开发工作目录压缩上传。

主品牌统一为 `tebb`。下一次发布包名为 `tebb-<version>-windows-x64.zip`，入口为 `tebb.exe`；先按新版本维护 manifest、版本号、更新说明与主题图，避免覆盖已有 v0.2.3 发布。新 manifest 增加 `product_name: "tebb"`，供飞书卡片使用；没有该字段的历史 manifest 继续使用旧品牌。历史 Release、旧 ZIP 文件名和已发送卡片保持原记录。

## 构建

PowerShell，在仓库根目录运行（output 必须是新的绝对目录）：

```powershell
python scripts/prepare_runtime.py --atlas ../Atlas --output C:/Build/tebb-runtime
python -m unittest discover -s tests -v
cd client
pnpm install --frozen-lockfile
pnpm test
pnpm exec tsc --noEmit
cd src-tauri
cargo test
cd ..
$env:DESK_RELEASE = '1'
pnpm tauri build --no-bundle
cd ..
python scripts/smoke_release.py C:/Build/tebb-runtime
python scripts/bundle_release.py --runtime-package C:/Build/tebb-runtime --output C:/Build/tebb-release
```

运行时取 [Python 官方 3.13.16 x64 嵌入包](https://www.python.org/downloads/release/python-31316/)，校验官方 SHA256 后解压。依赖版本固定在 `scripts/runtime-lock.txt`，包内记录 `runtime-packages.txt` 和 `runtime-provenance.json`。

Atlas 取源码库的固定正式提交 `47cf8e60af0a08c1e5c6b5465f310fdddf3dd404`，包含统一模型契约、采样与思考回调、严格完成校验、工具增量、客户端所有权与延迟加载模型 SDK 接口。直接归档提交源码，不附加开发机补丁；`runtime-provenance.json` 记录真实 Atlas commit。

修改 Atlas 固定提交后，先通过与构建脚本共用的归档入口，验证真实归档源码及协作适配器的同一套行为契约（不构建完整运行时、不调用真实模型）：

```powershell
$env:DESK_TEST_ATLAS_SOURCE = 'E:/path/to/Atlas'
python -m unittest discover -s tests -p test_atlas_runtime.py -v
```

未设置 `DESK_TEST_ATLAS_SOURCE` 时此集成测试明确跳过；它不能替代后续发布包的 `smoke_release.py` 验证。

`DESK_RELEASE=1` 禁止 Vite 扫描开发机的 public 目录，只复制公开的启动脚本。Core、形象、个人模型权重、用户配置和历史不会嵌入 exe。公开包仅附入口卡片；用户可把自己的素材放入引导显示的 assets 目录。

`smoke_release.py` 使用发布包 Python，从空白用户目录启动真实后端，以本地模拟的 OpenAI 兼容 HTTP 服务完成模型保存、测试、流式回复、采样参数、历史记录和引导完成验证。不会连接作者的模型服务或读取个人配置。可再把 ZIP 解压到带空格或中文的新目录运行同一验证，检查可移动性。

## 发布

按仓库规则一刀一个 issue，提交 `Fixes #n: 中文说明。` 并直推 main。确认构建与测试后，创建对应版本 tag，将 ZIP 和 `SHA256SUMS.txt` 上传到 GitHub Release。发布说明需明确 Windows / WebView2 要求、配置引导、Live2D 素材和知识库扩展的准备条件，以及实机界面验收的范围。

本包没有安装器、自动更新器或代码签名。更新替换程序目录即可，用户数据仍在 `%LOCALAPPDATA%/DeskCompanion`。

## 版本内容和图文通知

`desk_companion/ui/release.json` 是版本介绍和飞书更新卡片的共同内容源。每版同步修改 package.json、Cargo.toml / Cargo.lock、tauri.conf.json、pyproject.toml，填写发布日期和 3～5 条真实重点，生成同版本主题图，再同步前端图片 import。图片和说明随客户端打包，启动不依赖网络。图像生成方式和最终提示词记在 `docs/VERSION_POSTER_PROMPT.md`。

正式发布前先完成主题图和卡片审阅，建议先创建 draft 并上传 ZIP、校验文件和主题图。草稿、预发布和普通提交不发送通知。确认后将 GitHub Release 正式发布，`.github/workflows/release-feishu.yml` 自动发送 Card 2.0；桌宠不必保持运行。

仓库 Actions Secrets：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_RELEASE_CHAT_ID`。使用机器人应用身份，密钥不放入源码、客户端、发布包、Release 回执或日志。应用需开启机器人能力、加入目标群，拥有 `im:message:send_as_bot`、图片上传权限。自动查询群历史核实结果还需要 `im:message:readonly`（或 `im:message`）及 `im:message.group_msg`；缺少时停止自动补发，允许人工查群后选择核实结果。

通知回执以非敏感隐藏注释写入相应 Release 正文，按版本和目标群去重，跨 Actions runner 保留。状态 sent 直接跳过；状态 pending 在飞书幂等窗口内使用相同 UUID 重试，窗口到期后先完整查询群消息。查询失败或分页不完整时不重复发送。检查更新时隐藏这些内部注释。

仓库已更名为 `liukun158959-beep/tebb`。通知 API 使用新地址，UUID 继续使用原仓库命名空间，避免更名改变 pending 重试标识；不要随仓库名称修改此常量。Release 正文中的已有回执继续有效。工作流检出对应发布 tag，因此历史 tag 内的旧脚本可能拒绝更名后的 Release URL；不要改写历史 tag 或直接反复补跑，需先核实回执与群消息，再使用经验证的兼容脚本处理相应历史版本资料。

通知失败不回滚已发布版本，Actions 明确报失败。在 Actions → Release to Feishu → Run workflow，填写相同正式 tag，默认 reconcile=auto。若 pending 回执因权限不足无法自动核实，先人工检查群：确认存在卡片选择 confirmed_sent（只登记回执）；确认不存在才选择 confirmed_absent（补发）。其他状态不接受人工核实选项，避免把未经核实的失败当成成功。

本地无副作用预览：`python scripts/notify_release.py --dry-run`。手动执行发送或核实时需要相同 Secrets 对应环境变量、`GH_TOKEN`（该仓库 contents:write），以及 `--tag vX.Y.Z`。不要把密钥放入命令参数或提交记录。
