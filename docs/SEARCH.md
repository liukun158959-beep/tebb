# 联网搜索（#155）

搜索由 `search_service` 拥有，与聊天模型分离。默认 Exa 官方公开 MCP，Bing RSS 备用；普通 Atlas 对话、协作伙伴、视频工具及资讯采集共同使用 `web_search.search_sources(query)`。格式化只提供候选来源的标题、摘要和链接，不表示读取全文。设置 → 连接 → 联网搜索可以查看、保存和测试渠道。

搜索沿用模型网络设置的系统代理／直连／自定义代理及证书信任根，不修改全局环境。公开渠道不接收模型密钥；只有用户明确选择“当前模型私有接口”，才向已保存的当前模型地址发送其密钥。聊天 API 兼容不代表支持 `/v1/tool/search`。

一次逻辑搜索共享30秒时限、1MB响应上限。主渠道临时连接、超时或部分5xx至多重试一次，剩余时间不足时直接尝试备用。HTTP404/405/501视为不支持并冷却5分钟；429按 Retry-After 冷却（最长15分钟）。Exa 的 HTTP200＋MCP `_meta.ai.exa/rateLimited` 也会冷却，缺少明确时长时采用30秒。冷却状态跨进程共享，只保存渠道与时间。认证和证书校验失败明确报错，不由备用掩盖；取消、空结果、协议错误与网络故障分别记录。不自动改写用户查询或把日期条件去掉以填充结果。

Exa 请求使用当前实测协议的 `query/objective/numResults`，验证 JSON-RPC ID、版本、业务错误、SSE/JSON 和有界正文。N/A 不作为日期；明显损坏的摘要丢弃，保留真实来源链接。Bing 是候选备用，结果不保证相关；日期必须去原文核对。

## AI 调试与验收

认证 RPC：`load_search_settings()`；`save_search_settings({payload:{provider:"exa"}})`（另选 `model-api`）；`test_search({query:"Python asyncio TaskGroup official documentation"})`。复用客户端 `rpc(info,method,args)`，info 为现有后台端口与 token；不新增无认证入口。测试回执包含 provider、elapsed_ms、results 和每次 attempts 的渠道、次数、耗时、阶段、错误类别及 HTTP 状态。

所有调用同时将脱敏 `search {...}` 诊断写入现有后端日志，包含渠道、重试、冷却、错误类型和耗时，不写查询、密钥或原始错误 HTML。伙伴任务的实际队列事件和 evidence 保留更完整的逐次回执，复用 AGENT_DEBUG 的任务查询。主搜索失败的 HTML 不混入回答。

```powershell
python -m unittest tests.test_web_search tests.test_news tests.test_model_network tests.test_regression -v
# 明确启用外部只读请求；临时目录保存冷却与配置，不改变用户数据。
python scripts/probe_search.py --live --repeat 3 --output E:/Learn_Project/output/search-probe.json
```

2026-10-28 最终实测：6类查询（中英文技术、实体、时效）各重复3次，18/18取得可用来源，全部走Exa；中位2562ms、P95 3375ms，每次均包含预期官方域名候选。首轮为17/18，并发现 HTTP200 业务限流；修复分类后保留了首轮记录，没有删除失败样本。断网、404、429（含200业务限流）、认证、证书、坏JSON、错误ID、空结果、截断、响应过大、总时限、取消和密钥隔离另以故障注入验证。

质量与可用性分别验收：抽查 TaskGroup、React cleanup、飞书更新日程及 Claude 子任务结果的标题/摘要与主题相符；中文时效查询返回中科院2026-10原文，打开原文核对；英文时效查询返回官方新闻及发布说明，不能用新闻索引页自身的旧日期证明每条新闻时效。部分 React 结果标题为N/A，中文 Python 摘要有上游乱码，已对乱码做过滤；不能据此声称所有片段质量完美。18次是本机短时样本，不是公开服务长期 SLA。

本机验收记录：`E:/Learn_Project/output/search-orchestration-2026-10-28/search-live.json`（首轮）、`search-live-final.json`（最终）、`search-final-unit.log`、`flow-rpc.log`、`flow-ui.log`。

## 公开正文与 Fake-IP DNS（#158）

搜索摘要与网页正文是两个阶段。正文由 `web_sources.fetch_page(url)` 提取并保留来源、正文级别、字符数、截断、哈希和抓取时间；搜索成功不代表正文读取成功。发现链接校验、正文来源核对和引用规则继续生效。

2026-10-29 本机复现：公开域名系统解析到 `198.18.0.0/15`，原实现将其作为保留 IP 拦截。仅当普通公开域名的全部系统地址都在该网段时，`public_dns` 使用固定 Cloudflare HTTPS DNS 获取真实公网地址；不会直接连接 Fake-IP，不修改系统 DNS、代理或模型配置。DNS 请求仅发送域名和 A/AAAA 类型，不携带网页路径、查询参数、用户凭据或模型密钥。HTTPS DNS 使用已有模型网络配置与证书校验；其服务协议见 [Cloudflare 官方文档](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)。

私网/回环、IP 字面量、局域域名、混合公网私网或混合 Fake-IP/私网答案仍拒绝，不为普通 DNS 故障一概切换。备用解析校验响应问题、状态、CNAME 链和地址族，实际连接锁定已检查公网 IP，TLS 校验原主机名；每次重定向重新检查，不存在检查后再次解析目的地址的窗口。备用解析共享最多5秒与正文剩余预算，响应不超过64KB、不跟随 DNS HTTP 跳转；失败明确终止，不放行保留地址。`web_dns` 脱敏日志记录触发原因、解析器、公开域名和地址数量，不记录整条网页 URL。

```powershell
python -m unittest tests.test_public_dns tests.test_web_sources -v
# 隔离数据、复用当前网络设置，无模型或飞书写入；仅执行外部公开读取。
python scripts/probe_web_sources.py --live --repeat 2 --output E:/Learn_Project/output/web-body-live.json
```

本机两轮“AI资讯／KIMI 3.1”真实搜索共4/4取得候选，随后12次正文读取8次成功：Google官方博客与Kimi文章/文档均读到正文；4次OpenAI网页返回HTTP403。没有一次再以保留IP拒绝这些公开域名。验收材料为 `E:/Learn_Project/output/web-body-live-2026-10-29.json`，保留失败样本。只代表这次网络与正文可读性，不证明内容时效、特定模型规格或长期可用性；HTTP403、验证码、JS和登录限制仍可能导致某个站点无法读取，不能把摘要升级成正文或声称已核实。

另用真实 mimo-v2.6-flash 执行器读取用户提供的Kimi官方文章：2次模型调用、1次真实web_fetch，取得4690字符正文且引用检查通过，未调用搜索或其他业务工具。数据/用量写入临时目录，更新读取用替身隔离，无旧用户任务重放。记录为 `E:/Learn_Project/output/web-body-worker-live-final-2026-10-29.json`；首次手写探针缺少collab_path，零模型调用即失败，修正探针后通过，首轮记录仍保留。该样本验证真实模型到正文工具的链路，不等同整套调度或每项内容事实均已验证。

最终完整后端712项运行完成（698通过、14跳过），本轮受影响定向111项通过；前端177项、类型检查、生产构建及真实浏览器补充/续接验收通过。完整记录 `E:/Learn_Project/output/clarification-dns-full.log`，未发布安装包。
