# tebb 品牌与兼容约定

2026-10-15，用户确认将产品从「知行 · ZhiXing」更名为 **tebb**。

## 名称与产品方向

- 主品牌统一写为 `tebb`，用于客户端、网站、启动引导、文档与后续发布包。此次只确认英文名，不固定中文音译。
- 面向极客，产品方向是云端常驻、可连接本机、可扩展工具和模型的 Agent。
- 当前源码以 Windows 本机工作台运行；云端调度、多用户账号及云沙箱属于后续演进，不作为本次更名已实现的功能。
- 凯尔希是可选的桌宠形象，不作为主品牌名称。

## 兼容边界

- 保留 `cn.deskcompanion.client` 客户端标识、`%LOCALAPPDATA%/DeskCompanion` 用户目录及现有环境变量、Python 模块和内部包名，继续读取原配置、聊天、记忆、登录态和界面偏好。
- 2026-10-20，GitHub 仓库更名为 [`liukun158959-beep/tebb`](https://github.com/liukun158959-beep/tebb)，源码、更新检查和下载入口同步使用新地址。旧仓库 `liukun158959-beep/ZhiXing` 链接由 GitHub 重定向；不要重新创建同名旧仓库，以免失去跳转。
- 已安装旧客户端的发布链接白名单固化为旧仓库地址，改名后可能无法检查更新；请从[新版 Releases](https://github.com/liukun158959-beep/tebb/releases) 手动下载。当前源码明确接受本仓库新旧两个发布路径，并继续校验正式版本和安全域名。
- 已发布的 v0.2.2、v0.2.3 及其海报、日志、ZIP 和入口名属于历史记录，保留原名。历史 v0.2.3 使用 `ZhiXing.exe`，v0.3.0 使用 `tebb.exe`。
- 后续新包使用 `tebb.exe` 及 `tebb-<version>-windows-x64.zip`。新发布 manifest 应写入 `product_name: "tebb"`；发布通知核实同时识别 tebb、ZhiXing、DeskCompanion，避免更名后重复发送。

## 视觉与发布

v0.3.0 采用用户确认的圆形小伴与伙伴小队主题图，低饱和铅笔漫画风格，复用于 README、版本介绍和飞书卡片。仓库更名沿用原有用户数据目录；更名本身不发布新包或发送通知，后续发布按既有流程执行。
