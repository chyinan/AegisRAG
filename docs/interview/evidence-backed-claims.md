# 项目与简历表述：能力、源码、证据一一对应

下面是可选表达，不是已经发生的个人经历。仅在自己实际参与、能够解释代码和
复现验证时采用。测试数量以所附最终报告为准，不把重叠的定向测试相加；不要
虚构用户数、性能提升、团队规模、线上事故或投产成果。

## 可选项目要点

- 围绕私有知识问答构建权限感知的 RAG 与受控 Agent 工作台，串联导入状态、
  流式回答、可解析引用、工具进度和审批恢复，保留当前身份下的证据访问校验
- 为原生与 LangChain/LangGraph 实现建立独立可选适配层，覆盖三个开关的八种
  业务组合；统一 API、检索、聊天、Embedding worker 与 Agent 的契约验证
- 设计顺序受控工具执行的 durable 状态协议，以版本化快照、修订号 CAS、租约、
  精确审批和工具结果账本处理进程退出、并发恢复与未知副作用结果
- 建立失败注入与独立审查回归：取消传播、终态写入失败、迟到提交、丢失初次
  响应、权限/证据变化、辅助审计超时与私有数据边界

“设计”“实现”“完善”等动词应与自己的实际贡献匹配。若只学习或复现该项目，
应明确写成“分析并复现”，不要改写为主导开发。

## 面试时可以直接打开的材料

| 论点 | 实现入口 | 可验证证据 |
| --- | --- | --- |
| 上传产生的证据贯穿问答、聊天历史与引用打开 | `packages/ingestion/service.py`、`packages/rag/chat.py`、真实 SQL 记忆服务 | `tests/integration/journey/test_document_to_citation.py`；实际上传与服务组合，SQLite/合成 provider，不是生产或模型质量验收 |
| 框架组合不绕过业务边界 | `packages/retrieval/adapters/langchain.py`、`packages/rag/adapters/langchain.py`、`packages/agent/adapters/langchain.py` | `tests/integration/framework`、`tests/integration/framework_exports` |
| 最终回答在校验与持久化后发布 | `packages/agent/service.py`、`packages/agent/streaming.py` | `tests/unit/agent/test_streaming_service.py`、`tests/review/test_sse_lifecycle_review.py` |
| 恢复保留原预算与精确审批 | `packages/agent/durable`、`packages/agent/storage/durable.py` | `tests/unit/agent/test_durable_execution.py`、`tests/integration/framework/test_process_recovery.py` |
| 初始响应丢失仍可只读恢复 | `DurableAgentService.start/recover` | `tests/integration/framework/test_keyed_start_process_recovery.py`、`tests/review/test_keyed_start_review.py` |
| 普通事务不会把丢失提交回执误报为成功 | `packages/agent/service.py` | `tests/unit/agent/test_persistence_phases.py`、`tests/integration/storage/test_agent_persistence_phases.py`、`tests/review/test_ordinary_persistence_review.py` |
| 辅助失败不污染必需事务 | `packages/agent/auxiliary_io.py`、`packages/data/storage/auxiliary_audit.py` | `tests/integration/storage/test_auxiliary_audit_isolation.py`、`tests/review/test_agent_auxiliary_review.py` |
| 可选 RAG 审计有独立资源与取消边界 | `packages/rag/auxiliary_audit.py`、`packages/common/auxiliary_io.py` | `tests/integration/storage/test_rag_auxiliary_audit_isolation.py`、`tests/review/test_rag_optional_audit_review.py` |
| 测量报告区分样本、失败、耗时与统计口径 | `evaluation/benchmark_pipeline.py`、`evaluation/load_test.py` | `tests/unit/evaluation/test_benchmark_pipeline.py`、`tests/unit/evaluation/test_load_test_contracts.py`；这里只验证测量逻辑，不证明服务性能 |
| 显式语义分块失败进入正常重试状态 | `packages/ingestion/service.py`、`packages/ingestion/chunkers/semantic.py` | `tests/unit/ingestion/test_semantic_ingestion_failures.py`、`tests/integration/storage/test_semantic_ingestion_lifecycle.py`；默认 worker 不启用语义分块 |
| UI 不自动重放批准或信任旧身份 | `apps/web/src/hooks/use-agent-workbench.ts` | `apps/web/src/components/agent-panel.test.tsx`、协议/状态测试 |

完整决策见 [ADR 0009](../adr/0009-governed-framework-and-recovery.md)，
演示顺序见 [10 分钟讲解](10min-walkthrough.md)，
工程取舍见 [可靠性案例](reliability-case-study.md)。

## 不应写出的结论

- “已规模化投产”“支持所有框架/provider”“外部副作用 exactly-once”
- “离线 Ragas 或固定模型输出证明零幻觉”
- “SQLite 并发测试通过，所以 PostgreSQL RLS/故障恢复已通过”
- “类型检查与生产构建通过，所以真实浏览器和部署已验收”
- 未实际测量的成本下降、延迟下降、吞吐提升或用户增长

更可信的结尾是：说明已实现的边界、给出可复现的测试、列出目标环境剩余验收。
这能让讨论聚焦在工程判断与可解释性，而不是不可核验的技术标签。
