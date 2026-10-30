# CI/CD

关联 #175。用户已授权按推荐方案实施，保留直接提交 main 的方式，不修改分支保护，不自动正式发布。

## 日常 CI

ci.yml 在 main 提交、PR 或手动触发。Frontend、Python / Atlas、Rust 使用独立 runner，避免本机同时编译引起测试计时竞争。前端使用 Node24/pnpm12.5.1，验证测试、类型和发布构建；Rust 在 Windows 执行锁定测试；Python 使用 Windows/Python3.13.16、完整 runtime-lock 与 prepare_runtime 固定的 Atlas 提交，检查版本及全量回归。

普通运行可被同分支新提交取消。日志和 backend.json 保留14天，每个用例记录耗时，失败、错误、跳过和预期失败分别统计，attempts=1，不自动重跑。最终 GitHub 结论仍由命令退出码决定。条件跳过不算通过；发布 gate 还会检查所有必要 job 都成功。

Atlas 已于 2026-10-30 改为公开仓库。CI 和发布候选直接读取固定的公开提交，不需要配置 ATLAS_READ_TOKEN；Python 回归也在 PR 中执行。checkout 使用 Actions 默认的只读 GITHUB_TOKEN，且不持久化凭据；没有向 PR 注入个人 Secret 或扩大权限。若依赖以后重新变为私有，应单独确认最小授权与不受信任 PR 的执行边界，不能将读取失败默认为检查通过。

## 发布候选

Release candidate 仅手动运行。sha 必须是 main 中可达的完整40位提交，且该提交最新 push CI 记录和三个必要 job 均成功。构建绑定这个提交，不能借用其他提交的绿色结果；保留每次尝试的 Actions 记录。

运行时构建、前端/原生编译、空白配置 smoke、打包审计、中文/空格路径迁移均复用本地脚本。默认只上传候选 ZIP/SHA 到 Actions artifacts，不创建标签或 Release，不通知飞书。发布任务独立并发组且不自动取消。

明确勾选 upload_draft 才会上传新 draft Release；校验包内 build-info 和 SHA256，已有版本或标签直接拒绝，不能覆盖 v0.4.0。不会自动将草稿转正式。正式发布仍需独立发版指令，沿用现有 release-feishu.yml 的通知与回执。#174 完成前不冒充已有 NSIS 产物。

## 性能诊断与未来质量评测

手动 CI 可选择 diagnostics：运行已有的本地 SSE/一次性 worker 基准，输出独立 JSON。它不调用付费模型，也不是 #173 的语义任务质量评测。未来 #173 的实现可增加独立 job，依赖同一提交的基础检查，将模型/提示词/预算/任务结果写入独立评测产物；不得把当前 smoke 或性能数字当作任务成功率。

## 本地入口

```powershell
python scripts/ci_checks.py version
$env:DESK_TEST_ATLAS_SOURCE='E:/path/to/Atlas'
python scripts/ci_checks.py test --output output/ci
python scripts/release_audit.py C:/path/to/package.zip
python scripts/ci_checks.py smoke-zip C:/path/to/package.zip 'C:/new/中文路径 with spaces'
```

ci_checks test 必须有 Atlas 契约环境，不会因为未设置变量就默认跳过它。需要先将固定 Atlas 源码加入 PYTHONPATH 或按开发说明安装。后端测试仍依赖相应 Windows 组件；WSL/沙箱等专项仅按明确环境运行，实际跳过原因留在 JSON。自动检查不替代原生窗口的视觉与点击验收。

故障定位按 job/步骤查看：Atlas 源码读取、依赖安装、版本校验、测试失败、打包审计、迁移 smoke、上传分别保留实际阶段。不得把授权或下载失败当作“未找到 Release”后继续创建，也不因为构建失败自动发送发布通知。
