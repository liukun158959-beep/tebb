# Windows安装生命周期

对应#174，按本批统一推荐授权使用官方Tauri NSIS，不另建安装框架。安装器与便携ZIP由同一受审计候选目录生成，并逐文件核对内容；版本、二进制、Atlas来源和各自SHA256记在分发目录。普通源码提交和验证构建不发布新版本，冻结0.4.1资产不变。

## 程序与数据

默认当前用户安装至Tauri选择的用户程序目录，开始菜单入口和可选桌面快捷方式由标准安装器提供，不要求管理员。运行环境继续放在EXE旁的runtime目录；程序升级可替换它。模型、聊天、笔记、日程、素材、模型权重仍在原 `%LOCALAPPDATA%/DeskCompanion`，旧便携版默认数据直接沿用。视频登录和侧浏览器使用原应用标识的本机数据目录；这些目录不随覆盖安装删除。

缺少WebView2时按Tauri官方默认流程下载bootstrapper并联网安装；不是离线WebView2包，不自动重启。当前不包含代码签名或应用内自动更新。

安装及卸载在复制/删除程序前同时检测当前用户的前台程序与后台运行权；自定义DESK_COMPANION_DATA_DIR若由启动环境传入也检查原运行权。后台正常退出可能仍在收尾，请等待；安装器不会强杀任务。无法核对、权限失败或仍在运行时停止。标准卸载移除随包文件，未随包文件及后来生成的缓存可能留在程序目录；不递归删除任意安装目录。程序目录不得与原用户数据根混用。

同一Windows会话中的安装和卸载还通过应用标识派生的命名互斥量串行执行文件变更，句柄保留至实际进程退出。普通NSIS卸载会启动临时副本，原启动器先退出；此时安装或再次卸载须等待实际卸载完成，不能只根据原启动器退出码或一瞬间的文件缺失认定结束。

## 可选知识库扩展

退出tebb后运行程序目录的「安装知识库扩展.cmd」。依赖装进用户目录的extensions层，按Python ABI和实际基础依赖清单指纹分层；runtime-packages.txt保留实际安装清单，runtime-constraints.txt另存源码约束。基础依赖改变时旧层仍保留，但不会混用；旧便携版直接装在runtime内的扩展需重装一次，旧文件不自动复制或删除。

安装复用pip与基础依赖约束：先从官方CPU源只下载唯一torch wheel并验证CPU标记，torch及其它依赖通过正常pip源按原基础约束解析；CPU源不当作setuptools等依赖的完整镜像。sentence-transformers复用本地CPU wheel和精确torch约束，新一代依赖安装和CPU导入核对成功后才原子切换。失败保留旧兼容代及新失败回执。安装期间持有原后台运行权，阻止客户端并发启动；单次依赖进程有界等待。用户安装扩展仍需要下载依赖，合成扩展验证不代表已实测完整Torch，真实安装另行记录。

扩展路径使用16位ABI/基础依赖摘要、16位随机代号和短目录p，避免把调试用长名称带入每个依赖文件路径。pip临时目录t放在同一代内，通过子进程TEMP/TMP/TMPDIR传入；Windows的pip目标、临时路径及实际加载路径使用原生扩展路径命名空间，不改系统环境或长路径策略。安装关闭pip预编译缓存，避免长pycache文件名与跨盘复制放大路径问题。只使用当前未发布布局，不添加早期实验布局的适配或迁移分支，原实验文件不会自动删除。

只读状态：随包Python执行 `-c "from desk_companion.runtime_extensions import main; main()" status`；当前用户数据目录须与客户端相同。显式调用现有CLI函数，避免启动hook已导入模块后再以-m重载同一模块。result.json只保存代际、状态和异常类别，不保存pip正文、密钥或模型内容。

## 卸载

默认保留全部用户数据；升级卸载与静默/被动卸载始终保留。交互卸载只有勾选清除并再次明确确认，才由受限适配器处理两个约定根：DeskCompanion及cn.deskcompanion.client。自定义数据根、外部项目、系统lark/gh登录和Docker环境不会被猜测删除。

清除前核对全部根及后代，拒绝符号链接、junction和其他重解析点；先隔离全部目标，再删除。后一个目录忙碌时先恢复已隔离目录，不提前删除第一个。若删除阶段失败，保留剩余隔离目录并返回逐根状态；报告路径显示在安装器中，不能把部分结果当清除完成。标准模板的未校验递归清除被替换；取消清除仍保留数据。

## AI构建与验证

先按RELEASING构建独立运行时及release EXE，再生成共同便携候选：

```powershell
python scripts/bundle_release.py --runtime-package C:/Build/runtime-package --prepare-only
python scripts/bundle_installer.py --runtime-package C:/Build/runtime-package --output C:/Build/distribution
python scripts/release_audit.py --require-licenses C:/Build/distribution/tebb-0.4.1-windows-x64.zip
python -m unittest discover -s tests -p test_installation.py -v
```

以上版本名是当前源码的内部候选示例，不得覆盖或上传到已发布0.4.1。Tauri 2.11.5会把包类型UNK改为NSS写入NSIS，完成后恢复原便携EXE，故两种EXE有官方标记差异。标准NSIS的编译期!execute通过CreateProcess调用只读捕获工具，保留实际输入并分别记录binary_sha256、nsis_binary_sha256。捕获工具仅在内存归一化标记来比较原编译输入，不修改任何EXE；其它内容有变化则失败。共同载荷其余文件逐项一致，便携ZIP保留原EXE，安装验收核对实际NSIS输入摘要。回执只接受当前v2，不适配先前错误假设的回执。单独使用bundle_release的--output仍可生成便携候选，双产物流程使用--prepare-only。输出应是新目录；已有候选、旧摘要或打包时载荷变化均拒绝交付。CARGO_TARGET_DIR若设置必须是绝对路径。安装候选构建复用锁定的Tauri CLI和标准模板，只增加必要hooks；模板进程检查约定变化会停止构建。

本机原生验收必须先在独立检出中构建验证标识，例如productName/mainBinaryName为tebb-verify174-<编号>、identifier为cn.deskcompanion.verify174.<编号>，并设置独立窗口标题。对应bundle_installer增加 `--verification-profile <明确的合成数据绝对目录>`；验证产物会标记verification，Draft入口禁止上传。验证安装器禁用系统默认数据清除，清除适配器另用合成目录验证。Python数据、WebView及注册表/快捷方式须与真实用户实例隔离，禁止用真实用户数据测试清除。

普通安装前置查询可用随包Python执行 `-m desk_companion.installation check`。清除入口有明确确认开关，无任意数据库/数据路径参数，未加入RPC；仅安装器明确选择才调用。自动测试通过公开服务函数向临时用户根注入合成数据，验证运行权、越界、junction、隔离回滚及保留。

验证安装器生成后，生命周期入口只接受verification回执和独立标识，不接受正式安装器；例如：

```powershell
python scripts/verify_installer.py install --installer C:/Build/verification/tebb-0.4.1-windows-x64-setup.exe --work C:/Build/new-installer-probe
python scripts/verify_installer.py launch --work C:/Build/new-installer-probe
python scripts/verify_installer.py probe-native --work C:/Build/new-installer-probe
python scripts/verify_installer.py probe-busy --installer C:/Build/verification/tebb-0.4.1-windows-x64-setup.exe --work C:/Build/new-installer-probe
# 通过桌宠菜单“退出”，随后核对原生和后台均已退出
python scripts/verify_installer.py check-exited --work C:/Build/new-installer-probe
python scripts/verify_installer.py probe-background --installer C:/Build/verification/tebb-0.4.1-windows-x64-setup.exe --work C:/Build/new-installer-probe
python scripts/verify_installer.py seed-extension --work C:/Build/new-installer-probe
python scripts/verify_installer.py snapshot --work C:/Build/new-installer-probe
python scripts/verify_installer.py upgrade --installer C:/Build/verification-next/tebb-0.4.2-windows-x64-setup.exe --work C:/Build/new-installer-probe
python scripts/verify_installer.py probe-restored --work C:/Build/new-installer-probe
python scripts/verify_installer.py uninstall --work C:/Build/new-installer-probe
# 同时验证实际卸载进行时，新的安装器明确拒绝，不并行写文件
python scripts/verify_installer.py uninstall --work C:/Build/another-owned-probe --installer C:/Build/verification/tebb-0.4.1-windows-x64-setup.exe
```

work须是新的绝对目录。安装器静默使用独立注册标识及中文空格程序路径，不创建快捷方式；交互快捷方式仍须单独验证。launch使用独立Python数据及WebView路径，probe-native核对EXE及创建时间、真实子后台身份和认证RPC，再复用原发布smoke的假模型流式链；不保存进程token，不调用付费模型。probe-busy实际调用验证安装器并检查原生进程及程序文件保持，不以服务前置拒绝冒充NSIS已拒绝。安装超出等待范围时保留PID与pending状态，不强停或再次安装；inspect只核对原进程、文件和数据，未知退出码仍是未知。

每次命令在work保存独立报告，原失败不覆盖。原生退出通过正常菜单触发，不提供强杀作为完成路径；check-exited核对原PID及创建时间，避免PID复用误判。默认卸载核对全部随包文件及卸载器移除、合成数据不变，清除单独通过受限适配器测试。卸载等待到期保留原PID和pending状态，不强停；inspect-uninstall只核对原进程与文件结果，不重放原操作，未知退出码保持未知。

卸载验证采用[NSIS官方同步调用方式](https://nsis.sourceforge.io/When_I_use_ExecWait_uninstaller.exe_it_doesn%27t_wait_for_the_uninstaller%3F)：在验证work中创建摘要一致的卸载副本，以末尾未加引号的 `_?=<原安装目录>` 启动，观察实际执行卸载的PID与退出码。副本只决定执行位置，默认保留用户数据的产品行为不变。可选 `--installer` 先核对同一验证标识和候选摘要，再观察卸载实际持有互斥量，要求并发安装返回73；不以模拟前置检查代替真实NSIS拒绝。

后台专用smoke预期引导已完成；完整原生界面可保存仍在进行的引导，所以probe-native恢复检查以合成文件中的实际进度作为预期，仍核对保存的模型、网络和历史，不把进行中的引导当升级丢数据。后台PID在调用前落盘，断言失败也保留创建时间与具体失败位置。

probe-background只启动已安装的真实Python后台，以本地合成来源保存笔记和日程，再实际启动NSIS并核对运行权拒绝、程序文件不变及原后台仍在；最后使用原认证shutdown正常退出。阶段检查点记录PID/创建时间和已返回的合成资料ID，不保存token；失败原样保留，不重放未知外部写入。该路径不调用模型或飞书。

seed-extension只在无活动扩展的验证数据目录建立明确标记的纯Python合成代，并用实际随包解释器检查启动hook；后续probe-native会再次核对保留与导入。它不代表pip下载、完整Torch或真实向量模型验证。

probe-restored在先前probe-background已有合成资料回执后使用：启动真实已安装后台，通过认证RPC读取原模型、网络、历史、引导进度、笔记ID与日程操作回执，核对合成扩展加载及程序文件摘要，最后正常shutdown；模型调用数为0，不再次创建任何资料。适用于升级或默认卸载后重装的读取恢复验收，不冒充原生窗口验证。

验收分别记录安装、原生Tauri启动、认证后台/假模型、运行中拒绝、正常退出、升级数据与扩展、默认卸载/重装、受限清除、中文空格路径和实际PID/文件摘要。Python smoke只验证后台，不冒充原生安装。干净Windows10/11及无WebView2必须以实际环境验证；当前本机已有WebView2，未具备的环境保持未验证。
