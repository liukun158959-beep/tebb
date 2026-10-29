# 对话中的参数与上下文（#159）

2026-10-29 用户Q1/Q2/Q5确认：普通用户从主窗或浮动聊天输入框下方展开面板；实际请求、调用参数、伙伴记忆分别展示。查看只读，不调用模型；参数与已有手动整理入口保留，编辑只影响新任务，不新增自动压缩/裁剪策略。

实际请求复用agent_debug网络发送边界快照，绝不从聊天列表重构。后端按当前conversation的主任务/子任务关联 `collab-<run_id>` 调用，默认最新主调用，可选择其他阶段；按需读取完整消息、工具定义及完整JSON。新记录保存阶段和模型容量快照；旧记录未标阶段就显示未标注，不猜测。暂停记录期间、删除会话后或外部MCP内部自行调用的模型，不能补造缺失记录。

伙伴记忆显示当前伙伴偏好；本次实际注入内容仍以请求为准。旧助手的全局事实有独立兼容读取，原编辑页保留，本批不合并记忆引擎。鉴权头/Cookie和endpoint查询参数不入追踪；用户自行放入消息正文的秘密仍会原样保存，不能称为全量脱敏。记录留在本机，查看不上传模型。

模型窗口与输出预留保存在独立 `model-capacity.json`，按模型ID、名称、地址绑定，不改模型API请求或供应商额度。已知容量显示输入、输出预留与剩余参考空间；未知容量显示待配置，无百分比。输入优先服务usage，缺失时为明确标记的字符估算；媒体成本未知也提示。不同伙伴和不同调用各自计算，不把整链累计token当成某一个窗口占用。

新调用捕获当时的容量配置，修改配置不改历史快照；未捕获容量的旧调用允许参考当前匹配配置，标明当前配置。草稿的发送前参考值只按上次实际请求加草稿估算，**不是下一次尚未组装的准确请求**，不含未来路由、工具结果或压缩变化。观察到超限只提示，不自动改写或重放任务。

## AI 调试

复用现有本机认证RPC，info为当前端口/token：

```javascript
await rpc(info,'load_context_debug',{conversation:'原会话ID'});
await rpc(info,'load_context_debug',{conversation:'原会话ID',call_id:'已返回的调用ID',draft:'未发送草稿'});
await rpc(info,'save_context_capacity',{model_id:'已有模型ID',window:128000,output_reserve:16000});
```

容量数值示例需按实际模型配置核对，不是通用默认值。读取支持before游标，call_id必须属于所选会话；只读查询不等待共享模型写锁。保存失败/断连先重读核对，不自动重发。浏览器受限体验不开放完整上下文记录。

```powershell
python -m unittest tests.test_context_inspector tests.test_agent_debug -v
python -m unittest tests.test_local_api.LocalApiTests.test_context_inspection_uses_authenticated_shared_rpc -v
cd client
node --test tests/context-debug.test.cjs
```

隔离浏览器验收：`E:/Learn_Project/output/context-report-2026-10-29/`。容量与请求样本均为夹具，不代表用户实际模型容量；真实模型验证范围另见REPORTS.md。
