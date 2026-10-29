# 对话中的参数、上下文与记忆（#159 / #162）

2026-10-29 最新设计确认：输入框下方提供参数、上下文和记忆三个文字入口；实际请求和调用参数在右侧栏查看，伙伴记忆使用独立弹窗。采用 Lucide 线性图标。主窗和浮动聊天共用组件；普通用户可查看或明确保存伙伴偏好。查看不调用模型，新偏好不改已启动任务的快照，不新增自动压缩或裁剪策略。

实际请求复用agent_debug网络发送边界快照，绝不从聊天列表重构。后端按当前conversation的主任务/子任务关联 `collab-<run_id>` 调用，默认最新主调用，可选择其他阶段；按需读取完整消息、工具定义及完整JSON。新记录保存阶段和模型容量快照；旧记录未标阶段就显示未标注，不猜测。暂停记录期间、删除会话后或外部MCP内部自行调用的模型，不能补造缺失记录。

伙伴记忆显示当前伙伴偏好；本次实际注入内容仍以请求为准。旧助手记忆调试页、导航及兼容事实标签移除，不删除事实文件或旧桌面依赖的后端服务。新弹窗经既有协作事务仅更新所选成员的 memory 字段，不从旧快照重写模型或权限；保存断连后先重新读取核对。鉴权头/Cookie和endpoint查询参数不入追踪；用户放入消息正文的秘密仍会原样保存，不能称为全量脱敏。记录留在本机，查看不上传模型。

模型窗口、输出预留及可选 tokenizer 目录保存在本机 `model-capacity.json`，按模型ID、名称和地址绑定，不改模型API请求或额度。输入优先服务 usage，同时独立显示本地实算结果；没有服务回报时可使用匹配分词器结果，两者皆不可用则显示未统计，无百分比。已配置容量但输入未知时也不推算剩余。不同伙伴与调用独立计算，不把整链累计用量当作单个窗口占用。

新调用捕获当时的配置，修改不改历史快照；未捕获配置的旧调用可参考当前匹配配置并标明。tokenizer 仅离线加载官方 fast tokenizer.json、配置及聊天模板，不下载模型权重，不运行自定义 Python 代码。使用 Hugging Face Fast tokenizer/apply_chat_template 计算角色、控制 token 和工具定义，不把 temperature 等 HTTP 参数当作输入。不支持的媒体、缺失工具模板、变更的分词资料明确未统计；不能把知识库分词器套给聊天模型。绑定资料需由用户核对与服务模型一致，服务端私有模板仍可能与本地不同。

发送前草稿参考值使用真实 tokenizer 对上次请求追加当前草稿后重新计数；**不是下一次尚未组装的准确请求**，不包含未来路由、工具结果和压缩变化。未知时不回退字符公式。观察到超限只提示，不自动改写或重放任务。

## AI 调试

复用现有本机认证RPC，info为当前端口/token：

```javascript
await rpc(info,'load_context_debug',{conversation:'原会话ID'});
await rpc(info,'load_context_debug',{conversation:'原会话ID',call_id:'已返回的调用ID',draft:'未发送草稿'});
await rpc(info,'save_context_capacity',{model_id:'已有模型ID',window:128000,output_reserve:16000});
await rpc(info,'save_context_capacity',{model_id:'已有模型ID',window:128000,output_reserve:16000,tokenizer_dir:'匹配模型的本地目录'});
await rpc(info,'collaboration_action',{action:{type:'partner_memory',conversation:'原会话ID',agent_id:'成员ID',memory:'新偏好',request_id:crypto.randomUUID()}});
```

容量数值示例需按实际模型配置核对，不是通用默认值。读取支持before游标，call_id必须属于所选会话；只读查询不等待共享模型写锁。保存失败/断连先重读核对，不自动重发。浏览器受限体验不开放完整上下文记录。

```powershell
python -m unittest tests.test_context_inspector tests.test_agent_debug -v
python -m unittest tests.test_model_tokenizer -v
python -m unittest tests.test_local_api.LocalApiTests.test_context_inspection_uses_authenticated_shared_rpc -v
cd client
node --test tests/context-debug.test.cjs
```

隔离浏览器验收：`E:/Learn_Project/output/context-report-2026-10-29/`。容量与请求样本均为夹具，不代表用户实际模型容量；真实模型验证范围另见REPORTS.md。

2026-10-29 #162 验收：全后端735项（721通过、14跳过）；最终受影响83项通过，包含认证RPC绑定真实 fast tokenizer、无 usage 时本地计数、新记忆写入与已启动任务快照隔离。前端183项、类型检查和构建通过。真实浏览器验证右侧占位、完整请求、独立弹窗编辑/保存/重读、焦点与 Escape、390px无横向溢出。资料在 `E:/Learn_Project/output/context-style-2026-10-29/`，日志在 output/context-style-*.log。

分词验收使用隔离构造的真实 tokenizer 资料，不代表已核实当前 MiMo/GLM 线上服务模板；未自动给这些模型绑定其他分词器。当前服务 usage 仍可展示实际回报。本轮没有真实模型调用、飞书写入或发布。首次 AutoTokenizer 触发额外模型组件导入，改用 Fast tokenizer；浏览器定位到保存后的焦点问题并修复。收尾另修复主按钮悬停被通用样式覆盖的问题，真实浏览器分别核对普通与悬停状态的背景、前景颜色。未把这些首次失败当作通过。
