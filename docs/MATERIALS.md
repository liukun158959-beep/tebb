# 资料工作区

关联 #122，设计见 [ADR 0036](adr/0036-material-identities-and-index-versions.md)。本批需求按用户统一推荐授权实施。

知识库的「资料工作区」将来源登记、分页预览、手动核对、明确入库、单来源刷新和移除索引串在一起。「已索引资料」保留原有目录与飞书浏览入口；附件仍是用户某次选择后的只读快照。

来源编号持续存在，位置可由用户重新定位。正文版本按实际读取内容保存，索引引用其所用版本。重新定位不移动文件，移除索引不删除来源、原文件或历史版本。当前首版不提供原文件删除、全盘扫描、后台正文同步或新的向量引擎。

来源状态以最近实际核对的时间和版本为准。已登记本地文件的元数据或已知授权变化会使旧索引失效；这不等于自动重读正文。远端状态表示上次读取结果，不保证实时感知远端权限或内容变化。未知历史索引保留可用性并标注未核对；一旦明确核对出失效，不再冒充当前资料参与新检索。

旧数据留在全局范围，原文档编号不变，不自动分配到当前项目。新项目来源及检索在后端按范围筛选；本地项目来源还绑定目录授权版本，撤销或切换授权后需明确重新定位。项目查询不回退全局。旧入口和新登记同位置的索引会在明确操作时衔接，避免重复加入。

刷新先读取并保存正文版本，再使用既有知识模型和切块设置计算索引。来源、授权、原文件元数据、索引或配置在计算期间变化，或者嵌入失败/取消，均不覆盖旧索引。计算阶段不持来源锁；最终锁顺序为来源登记到索引，使用既有CAS提交。全库重建作为显式维护操作，会检查全部已索引来源的现有授权并整体提交。

笔记保存来源编号、索引使用的正文版本和范围。侧边引用始终展示保存片段，核对当前来源的内容另行显示；不同版本明确提示。旧引用缺少版本或来源编号时标为未知，不补造历史证据。

## AI 调试接口

复用既有认证 RPC：方法 `material_operation`，参数为 `{ "payload": { ... } }`。WebSocket沿用现有 `type=rpc`、请求 `id`、`method` 与 `args` 包装，以及外层协议和内层业务两个 `ok`。不要使用真实数据目录构造第二个后台。

| action | 输入 | 输出/含义 |
| --- | --- | --- |
| list | project_id，空串为全局 | 来源/观察与索引版本/核对时间/原操作状态；项目配置选项 |
| register | snapshot_id 或飞书locator、title、project_id | 只登记，返回稳定source_id；本地snapshot_id来自既有选择/暂存接口 |
| check | source_id、project_id、operation_id | 实际读取并记录可用性/正文版本，不自动更新索引 |
| refresh | 同上 | 单来源计算并CAS提交索引 |
| rebind | source_id、project_id、snapshot_id、revision | 明确重新定位，保留来源编号并核对现有授权 |
| cancel | source_id、project_id、operation_id | 取消指定原操作，提交前检查；不承诺已提交的索引回滚 |
| read_current | source_id、project_id、operation_id、offset/limit可选 | 当前授权来源的本次读取快照及版本 |
| read_version | source_id、project_id、version、offset/limit | 读取指定不可变正文版本；后续页继续使用首次返回版本 |
| remove_index | source_id、project_id | 仅删除派生索引 |
| retrieve | question、project_id、可选doc_ids | 本地检索片段与版本引用，不调用生成模型 |

来源和操作编号均为32位小写十六进制；正文版本为SHA-256。单页最多24000字符，默认12000。再次使用当前展示的原操作号不会隐式重放，使用list核对原状态。长操作期间仍可list、cancel和read_version；新写操作复用知识服务的忙碌约束。

`unconfirmed`表示写入结果需重新查询，`committed`表示业务已提交但后续刷新可能失败，`partial`需同时核对来源观察和`index_change_state`，取消不等于回滚。遇到断连或未知回执先查询，不自动创建新操作来掩盖原结果。

离线验证命令（仓库根目录，按开发环境配置Atlas）：

```powershell
python -m unittest tests.test_material_sources tests.test_material_service -v
python -m unittest tests.test_local_api.LocalApiTests.test_material_rpc_real_state_scope_and_authentication -v
cd client
node --test tests/materials.test.cjs tests/local-sources.test.cjs tests/workspace.test.cjs
```

测试使用临时源文件、假嵌入和真实本地认证WebSocket；无付费模型或真实飞书读写。配置/模型缺失按既有知识库规则报错，不自动下载模型或改走其他检索方式。

## 交付验证

95249f5 的[精确 CI 41208838109](https://github.com/liukun158959-beep/tebb/actions/runs/41208838109)全部通过：1088 项后端无失败/错误、12 项显式沙箱opt-in跳过，前端/类型/生产构建/Rust通过。源码已快进main并独立核对，#122完成；本轮未修改冻结发布包。真实浏览器在隔离示例中确认索引历史版本与当前原文分别读取，读取不会自动重建索引。
