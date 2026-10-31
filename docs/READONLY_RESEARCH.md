# 只读研究运行时与验证

#171按[ADR0034](adr/0034-atlas-readonly-research-workers.md)将Windows上新建、独立collect分工的工具循环交给固定Atlas fdafaecab45303314829f4f5af4fee10365c6dc0。生命周期、来源、权限、实际回执、独立审阅、Ledger仍由产品拥有。旧任务与不匹配角色保持兼容。任务不是因Atlas返回success就完成。

## 提交与观测

沿用[AGENT_DEBUG](AGENT_DEBUG.md)的认证RPC和协作action/send、状态与任务查询。新建独立研究分工并授权联网搜索/网页读取后，调度器自动选择；不新增任意命令入口。只读工具也不能越过角色、附件与来源边界。`Collaboration(readonly_runtime='legacy')`是隔离验证的依赖注入选择，生产默认atlas，不修改用户配置。

run的`execution_runtime`记录实际legacy或atlas_readonly；`runtime_observations`保留有界运行观察，含Atlas的loop_status和run_id。按父子run、task、call ID关联原任务事件、模型调用、来源、feedback_check、task_budget；查询错误与取消保持原语义。取消正在收发模型、阻塞读取和期限耗尽均经TaskManager回收实际工作进程；Windows父退出由Job收尾。远端推理是否同时停止不能从本地退出推断。Linux暂未验证父退出等价保护，默认不启用且对应对照明确跳过。

## 统一有限评测

在仓库根目录，使用含固定Atlas的Python环境。新输出目录不可覆盖旧失败：

```powershell
python scripts/eval_agent.py --case readonly_serial --case readonly_legacy_parallel --case readonly_atlas_parallel --limit 3 --samples 1 --timeout 90 --output ../output/research-fixture-new
```

这是明确的合成文档与假模型，不构成真实语义能力结论。三个案例都走实际路由、计划、两个子任务、负责人汇总和独立审阅；旧串行仅1工作进程，另外两种允许2个。统一报告契约v17。

真实模型另加`--live --research-corpus <固定实际文档目录>`，时间限额至多300秒/案；需要验证的生成上限绑定可显式传`--output-limit-bindings <SQLite快照>`。只复制至隔离数据目录，不改用户默认配置。运行前冻结三个案例各1样本1次尝试、模型、来源哈希和有限预算，保留失败、取消、跳过与未知费用；不自动重跑补分。模型调用真实；网页读取替换为冻结的实际来源，不能声称本轮实时HTTP读取或网络性能对照。无飞书写入或沙箱创建。

corpus需manifest.json：version=1、scope=frozen_real_public_sources，sources恰含RFC8259与RFC7159官方txt的url/file/sha256/characters/fetched_at。文件须UTF-8且路径在目录内，哈希及字符数匹配。可先通过既有fetch_page保留真实正文；合成快照禁止用于live。报告保存出处与哈希，全文不上传源码。

机器验收实际两个无依赖子任务、运行时、时间区间是否重叠、退出、独立审阅、实际读取的归档哈希、编号/年份及子任务和汇总引用。只核对文档头部，不冒充全文阅读。`lifecycle.research_failure`给出具体失败维度；呼叫用量不完整、价格未知分别保留，不能记零。时延与小样本质量分别报告，不承诺Atlas一定更快。

定向回归入口：`python -m unittest discover -s tests -p test_readonly*.py -v`，另有test_research_tools、test_research_evals、test_feedback_worker与test_collaboration。原生取消/期限/兄弟任务失败/父退出测试使用明确替身，不调用真实供应商。实际通过数量以对应日志为准。
