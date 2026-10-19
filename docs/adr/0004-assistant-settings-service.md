# 模型与人设配置通过服务组合复用

设置 RPC 原先从 HeadlessApp 经 App 继承获取模型、人设规则，配置改动因而依赖桌面类。按用户确认的分批边界，本批将 9 个入口集中到 AssistantSettingsService；它只依赖 ConversationService 的公共状态与重建/绑定接口，并复用既有配置存储、模型清单和网络策略。配置服务从同一个 ConversationService 获取 UserState，不复制 Agent、忙碌或配置状态，也不接收整个 Host。

Bridge 直接调用配置服务，旧 App 保留兼容方法，两个入口使用同一套规则。保留 RPC、持久化格式和原有保存、绑定及错误时序；模型网络设置已有独立实现，不再包一层。旧 save_model 价格校验前写入 .env 的事务缺陷单列为 [#115](https://github.com/liukun158959-beep/tebb/issues/115)，不在此次解耦中更改。会话、看板、笔记仍待后续拆分，HeadlessApp 整体的 GUI 继承尚未移除；本批只保证配置服务可在禁止 GUI 导入时独立使用。
