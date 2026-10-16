# 30 分钟深度剖析：从可运行到可验证

本页是面试讨论提纲。只把自己的实际贡献写入简历；不预设团队人数、开发
周期、上线客户、流量、个人职责或量化收益。代码与可复现验证比技术名词更重要。

## 1. 业务边界与可替换组件（约 4 分钟）

应用层依赖 LLM、Embedding、检索、向量存储和工具执行端口。原生实现是
默认值，LangChain 与 LangGraph 是可选适配器。模型身份仍由 provider/model/
version 描述，adapter 名称不是模型身份。

可讨论的代码：
- `packages/llm/ports.py`、`packages/llm/decisions.py`
- `packages/llm/adapters/langchain.py`
- `packages/embeddings/adapters/langchain.py`
- `apps/api/factories/agent.py`

三个开关独立选择 LLM、Embedding 和 Agent 调度器。另一个开关选择 JSON
或原生工具决策。原生工具模式调用真实 `ChatOpenAI.bind_tools`，每一步只接受
一个函数调用；最终答案也只是声明，不能绕过现有最终答案校验。

追问：“为什么不直接用一个框架 Agent Executor？”

因为执行权限、限流、预算、引用和审计已经是应用契约。再引入一个执行循环
可能产生重复工具调用和两套预算。这里复用框架组件，但执行仍经过 Registry。
这不是反对框架，也不是声称所有模型端点都已兼容。

## 2. 检索、权限和引用（约 4 分钟）

Dense 与 Sparse 检索通过 RRF 融合，再由可选 reranker 排序。讨论不同召回
信号的互补性，而不是保证混合检索对所有语料都更好。精确编号、日期、多跳
问题等应进入有版本的测试集；真实质量改善必须用固定语料、模型和评估条件证明。

权限过滤在模型上下文形成前执行。证据保留 document/version/chunk 身份，
而不是只返回一段无法追踪的文本。恢复 Agent 时，还需重新检查文档、版本、
chunk 的删除状态、ACL、当前版本和内容指纹。过去能访问不代表现在还能访问。

可讨论的代码：
- `packages/retrieval/application.py`
- `packages/rag/query.py`、`packages/rag/access.py`
- `packages/agent/adapters/document_evidence.py`
- `tests/review/test_durable_evidence_review.py`

历史 README 中的 judge 分数和延迟只属于对应历史样本，不是本次框架工作的
新结果。Faithfulness=1.00 不是零幻觉保证；不同版本、数据集和指标不能混用。
本轮离线 Ragas/MockTransport 契约只能证明集成行为，不能证明真实模型质量。

## 3. 框架生态接口与隐私（约 4 分钟）

`AegisRetriever`、`AegisQueryRunnable` 和 `AegisStructuredTool` 都是实际
LangChain 扩展点，可以接入真实 RunnableSequence 或 LangGraph 图。

设计约束：
- 构造时绑定认证上下文，输入或 RunnableConfig 不能替换租户/用户
- 只提供异步调用，避免给拥有异步数据库会话的服务偷偷套线程/事件循环
- 检索输出是经过授权的有限 excerpt 与规范引用，工具输出不含 prompt_context
- 显式及环境 callback 不跨边界传播；外层调用者自己开启的 tracing 仍由其负责
- 同一会话的导出操作串行化；流的打开、读取、关闭在同一个隔离 Task 内执行

最后一点来自真实问题：每次 `anext` 创建新 Task 会破坏跨 yield 持有的
`asyncio.timeout` 等资源。测试应覆盖消费者暂停期间的异常与提前关闭，
而不只是正常读完整个 token 流。

可讨论的代码：
- `packages/integrations/langchain_boundary.py`
- 各业务包的 `adapters/langchain.py`
- `tests/integration/framework_exports/test_framework_consumers.py`
- `tests/review/test_framework_exports_decisions_review.py`

## 4. 流式生命周期与最终结果（约 4 分钟）

`POST /agent/run/stream` 输出 started、tool_call、tool_result 和终态事件。
进度不是答案；成功答案必须通过验证并完成数据库提交。断开连接取消所属
运行，并进行有界的终态清理。数据库不可用时不能声称取消已成功持久化。

可追问：
- SSE 响应头发出前是否检查权限？
- provider 自己抛 CancelledError 与客户端断开有什么区别？
- 消费者很慢、队列溢出、重复取消、提交失败时会发生什么？
- 部分文字是否可能被 UI 当成成功答案？

实现与验证：`packages/agent/service.py`、`packages/agent/streaming.py`、
`tests/unit/agent/test_streaming_service.py`、
`tests/integration/storage/test_agent_stream_persistence.py` 和独立 ASGI 回归。
SSE ID 仅属于连接，不承诺 Last-Event-ID 重放。

## 5. Durable Agent：状态、审批与副作用（约 7 分钟）

普通 Agent 历史行不是 checkpoint。独立 durable API 使用版本化私有业务
快照；原生与 LangGraph 调度共用同一状态转换。快照保存累计预算、原始截止
时间和重复动作计数，恢复不能重新获得一份完整预算。等待审批也消耗原始
墙钟时间，目前上限120秒，是明确的受限交互范围。

并发恢复需要修订号 CAS 和租约 fencing。取消变更版本并终止所有权，旧
worker 不得覆盖终态。审批绑定具体 run、动作参数、工具定义和有效期；
Registry 验证一次性 grant，直接从 StructuredTool 调用也不能绕过审批要求。

副作用的几个窗口必须区分：
1. 调度前确定拒绝：没有执行副作用，不应伪装成未知执行结果
2. 工具已执行、结果未提交：不能泛称 exactly-once；`never` 工具需人工介入
3. 完成账本已提交、进程退出：复用已有结果，不能再执行一次
4. 显式 read-only 工具的不确定调用可有限重试，但仍消耗调用预算

LangGraph 使用真实 SQL-backed checkpointer，但 channel 只包含 run reference、
revision 和 route。业务快照才是权威状态，框架 checkpoint 可以落后；恢复
必须处理这种差异。框架写入顺序、schema 变化与异步 saver 行为都需要实际
版本测试，不能靠打开一个 checkpointer 开关推断正确性。

可讨论的代码与证明：
- `packages/agent/durable/`、`packages/agent/storage/durable.py`
- `packages/agent/adapters/durable_langgraph.py`
- `tests/unit/agent/test_durable_execution.py`
- `tests/integration/framework/test_process_recovery.py`
- `tests/review/test_durable_security_review.py`

真实子进程硬退出测试证明应用进程重启后的恢复，不等于断电、磁盘损坏、
多机 PostgreSQL failover 或生产 side effect 已通过验收。

## 6. 运维、版本和证据（约 5 分钟）

兼容 CI 定义 locked、minimum 和 latest-compatible 三条路径。只有实际
执行过的版本才有通过证据；已写 CI 不是 CI 已通过。可选真实端点测试默认
跳过，需要显式目的地、凭据注入和费用授权，不能用 fake 输出替代真实验收。

migration0018 是新增表和 RLS 策略。只读 preflight 明确认识0016/0017/0018，
检查运行角色、准确的 tenant policy、向量约束、durable schema 与聚合状态。
它不读私有快照内容、不授权升级、不改权限、不清理队列。RLS 隐藏的零行
不能证明全租户无任务；恢复演练等人工项永远保留 UNVERIFIED。

当前投产前仍需：目标 PostgreSQL/RLS/并发验证、真实模型评估、故障注入、
备份恢复、保留策略与负载证据。Helm 仍是实验脚手架。项目的亮点应是这些
边界被明确实现和测试，而不是宣称“所有 Epics 完成，所以已经生产就绪”。

参考：[能力矩阵](../FRAMEWORK_ADAPTERS.md)、
[Durable 运维](../operations/DURABLE_AGENT.md)、
[升级清单](../operations/UPGRADE_CHECKLIST.md)。
