# 就业与产品市场调研驱动的项目优化方案

研究日期：2026-05-26  
输入依据：`AGENTS.md`、`docs/TECHNICAL_PREFERENCES.md`  
研究主题：面向就业展示与产品化落地的本地化多源知识增强 RAG + Agent 问答系统

## 1. 结论摘要

本项目不应定位为“做一个 RAG demo”，而应定位为“企业私有知识场景下的可治理 RAG + Agent 应用平台”。就业展示侧，最有价值的信号不是接了多少模型，而是能证明候选人理解真实企业 AI 系统的工程边界：权限过滤、文档版本、异步 ingestion、hybrid retrieval、rerank、citation、评估、审计、可观测和可部署。产品侧，最清晰的切入点是企业内部知识问答、制度/合同/规范检索、研发知识库、售前/客服知识增强，而不是一开始做泛用 Agent 平台。

公开资料显示，AI 和大数据仍是就业技能增长的核心方向，企业生成式 AI 采用率持续提升，但 Agent 项目同时面临成本、治理和业务价值不清的问题。因此本项目的优化方向应是：用工程化 RAG 打底，用受控 Tool Registry 和状态机式 Agent 做增强，用 eval 和 observability 证明质量，而不是过早追求“全自动多 Agent”。

本方案对现有技术偏好进行细化，不改变原有分层架构和 Provider 抽象原则。

## 2. 就业定位优化

### 2.1 目标岗位

优先面向以下岗位包装项目：

| 岗位方向 | 项目需要证明的能力 | 关键交付物 |
| --- | --- | --- |
| AI 应用工程师 | 把 LLM、RAG、工具调用变成可维护应用 | `/query`、`/chat`、SSE、citation、provider abstraction |
| RAG 工程师 | 文档处理、检索融合、rerank、上下文组织、评估 | ingestion pipeline、hybrid retrieval、eval dataset、检索日志 |
| Agent 工程师 | 工具注册、权限、max_steps、状态机、审计 | Tool Registry、ReAct runtime、Planner-Executor skeleton |
| 后端 AI 平台工程师 | FastAPI、SQLAlchemy、任务队列、缓存、部署、观测 | Docker Compose、Alembic、worker、structured logging、metrics |
| LLMOps / AI 质量工程师 | 质量指标、回归评测、prompt injection 防护 | RAG eval、groundedness checks、OWASP LLM risk mapping |

### 2.2 简历叙事

推荐简历项目描述：

> 设计并实现企业级私有知识 RAG + Agent 问答系统，支持多源文档 ingestion、PostgreSQL + pgvector / FAISS 可插拔向量检索、BM25 + dense hybrid retrieval、cross-encoder rerank、citation 可追踪回答、SSE streaming、RBAC 过滤、Tool Registry 和可观测审计链路。

推荐用量化证据支撑，而不是只列技术栈：

- ingestion：支持 PDF、DOCX、TXT、Markdown，文档版本和 chunk checksum 可追踪。
- retrieval：dense + BM25 + RRF merge，召回、rerank、过滤各阶段可观测。
- security：tenant_id、ACL、RBAC 在检索阶段生效，禁止让 LLM 判断权限。
- quality：有一套小型 eval dataset，记录 hit rate、faithfulness、citation coverage、no-answer rate。
- deploy：Docker Compose 一键启动 API、worker、PostgreSQL、Redis、MinIO、OpenSearch 可选。

### 2.3 面试展示优先级

面试中最容易拉开差距的展示顺序：

1. 架构图：API -> Application Service -> Domain -> Infrastructure -> Storage。
2. 一条完整链路：upload -> parse -> chunk -> embedding job -> hybrid retrieval -> rerank -> context packing -> citation answer。
3. 权限证明：同一问题在不同 tenant / ACL 下返回不同可见结果。
4. 质量证明：展示 eval 指标和失败样例复盘。
5. Agent 证明：工具调用被 registry、permission、timeout、audit log 约束。

## 3. 产品市场定位

### 3.1 建议产品定义

产品定义：

> 面向企业内部文档、制度、合同、研发资料和业务知识的私有化 RAG + 受控 Agent 问答系统，重点解决“找不到、信不过、不可追责、不能越权、难集成”的知识使用问题。

不建议一开始定义为：

- 通用 ChatGPT 替代品。
- 泛用多 Agent 自动办公平台。
- 只支持上传文件问答的轻量 demo。
- 以 prompt 模板为核心卖点的低门槛工具。

### 3.2 目标客户分层

| 客户段 | 典型场景 | 购买/采用动机 | 产品必须证明 |
| --- | --- | --- | --- |
| 中小技术团队 | 研发文档、接口文档、故障手册问答 | 降低查文档成本 | 准确 citation、Git/Markdown 友好、部署简单 |
| 企业 IT / 数字化部门 | 多部门知识库、权限隔离、统一入口 | 内部提效和治理 | RBAC、审计、可观测、可集成 |
| 法务/合规/制度部门 | 合同、制度、规范条款检索 | 降低误读和越权风险 | page citation、无答案策略、版本追踪 |
| 客服/售前/交付团队 | 标准答案、产品资料、方案库 | 缩短响应时间 | 召回稳定性、常见问题 eval、低延迟 |
| 教育/培训组织 | 课程资料、手册、考试资料 | 个性化答疑 | 多轮记忆、来源可见、低成本本地部署 |

### 3.3 客户痛点

真实企业用户不会只问“能不能回答”。更关键的问题是：

- 资料分散在 PDF、Word、Wiki、本地文件夹、网页和 IM 中，难以统一治理。
- 纯向量检索对编号、制度条款、错误码、人名、产品型号不稳定。
- 大模型会编造答案，业务方要求能定位来源和页面。
- 权限不能事后过滤，必须在检索阶段过滤。
- 文档不断更新，chunk、embedding、索引版本必须可追踪。
- 工具调用和 Agent 自动化存在越权、误操作、成本失控风险。
- 管理者需要知道系统是否真的有用：命中率、无答案率、citation 覆盖率、用户满意度。

### 3.4 竞品与差异化

| 类别 | 代表 | 优势 | 对本项目的启发 | 差异化机会 |
| --- | --- | --- | --- | --- |
| 开源 LLM 应用平台 | Dify | workflow、知识库、应用搭建快 | 学习产品表面和 workflow 编排 | 更强调后端分层、RBAC、检索评估和审计 |
| 本地 Web UI | Open WebUI | 本地部署、模型接入、知识库入口 | 可作为第一阶段前端 | 后端提供企业级 RAG API 和权限治理 |
| Agent 框架 | LangGraph | 状态图、持久化、human-in-the-loop | 复杂 Agent 借鉴状态图设计 | 核心业务不锁死在框架内 |
| RAG 框架 | LlamaIndex / LangChain | 数据连接器、索引、Agent 生态 | 借鉴接口和模式 | 关键链路自研可测试抽象 |
| 向量数据库 | pgvector、Milvus、Qdrant | 向量检索能力成熟 | 做可插拔 VectorStore | 用 metadata、ACL、版本治理形成完整系统 |
| 企业套件 | Microsoft Copilot、Google Gemini Enterprise | 生态集成强 | 证明企业愿为知识增强买单 | 面向私有部署、模型可替换、本地化场景 |

本项目的核心差异化不是“功能更多”，而是“在可控范围内更可信”：

- 每个答案可追踪来源。
- 每次检索可复盘。
- 每次工具调用可审计。
- 每个模型、embedding 和索引版本可追踪。
- 每个 tenant、user、ACL 在后端强制执行。

## 4. 技术路线优化

### 4.1 技术原则排序

现有文档已经强调生产级设计。建议进一步明确优先级：

1. 正确性优先于功能数量：citation、ACL、no-answer 策略优先。
2. 可测试优先于框架速度：RAG 链路拆分成可单测模块。
3. 可观测优先于模型堆叠：先能解释失败，再增加 provider。
4. 简单可替换优先于深度绑定：Provider、VectorStore、Reranker、Tool 都必须是接口。
5. 产品闭环优先于技术炫技：先完成企业知识问答闭环，再上复杂 Agent。

### 4.2 推荐默认实现栈

第一阶段默认：

- API：FastAPI + Pydantic v2。
- DB：PostgreSQL + pgvector。
- Sparse：PostgreSQL full text search 起步，后续可切 OpenSearch。
- Queue：Redis + RQ 或 Celery，优先选团队更容易维护的一种。
- Object storage：MinIO。
- Embedding：Provider 抽象，Fake provider 用于测试，真实 provider 走配置。
- Rerank：先定义接口和 fake reranker，再接 cross-encoder。
- Frontend：先对接 Open WebUI 或做最小 React 控制台，不抢后端优先级。
- Observability：structured logging 起步，预留 OpenTelemetry trace_id。

后续扩展：

- Milvus：仅在数据规模或并发明确超过 pgvector 能力时引入。
- LangGraph 风格工作流：Agent Runtime 稳定后再上状态图。
- Graph RAG：有实体关系和多跳问题数据后再做，不作为第一阶段必需项。

### 4.3 模块边界细化

| 模块 | 层级 | 职责 | 禁止事项 |
| --- | --- | --- | --- |
| `packages/ingestion` | Domain + Application | parse、clean、dedup、chunk、job 状态 | 不调用 LLM 生成答案 |
| `packages/embeddings` | Infrastructure | provider、batch、retry、rate limit、model version | 不写死厂商 SDK |
| `packages/vectorstores` | Infrastructure | upsert/search/delete、metadata filter、ACL filter | 不暴露数据库细节给 route |
| `packages/retrieval` | Domain + Application | dense、BM25、RRF、dedup、rerank、threshold | 不直接拼 prompt |
| `packages/rag` | Application + Domain | context packing、prompt building、generation、citation | 不做权限判断 |
| `packages/agent` | Domain + Application | Tool Registry、runtime、step limit、audit | 不让 LLM 任意调 Python 函数 |
| `packages/auth` | Domain + Infrastructure | AuthContext、RBAC、tenant、ACL policy | 不把权限逻辑写在 prompt |
| `packages/common` | Common | errors、logging、config、request context | 不引入业务依赖 |

## 5. 路线图优化

### Phase 0：工程骨架和质量门

目标：让后续功能不会长成 demo。

- 建立 monorepo 目录：`apps/`、`packages/`、`tests/`、`docs/`、`docker/`。
- 定义 `AuthContext`、`RequestContext`、结构化错误、配置加载。
- 配置 ruff、pytest、基础 CI。
- 写架构决策记录：Provider 抽象、pgvector 默认、队列选择、前端路线。

验收：

- `pytest` 可以运行。
- 一个 FastAPI health endpoint 可启动。
- 所有核心接口有类型定义。

### Phase 1：Ingestion 和文档治理

目标：把“多源文档”做成可追踪资产。

- 实现 RawDocument、ParsedDocument、Section、Chunk。
- 支持 PDF、DOCX、TXT、Markdown parser。
- 实现 FixedSizeChunker，预留 SemanticChunker、HierarchicalChunker。
- 文档版本、checksum、ACL、tenant_id、source_uri 全链路保留。
- 上传接口只创建 ingestion job，不同步等 embedding。

验收：

- parser、cleaner、chunker、dedup 单测。
- 文档版本和 chunk metadata 可查询。

### Phase 2：Hybrid Retrieval

目标：做出就业和产品都最有说服力的核心能力。

- DenseRetriever、SparseRetriever、HybridMerger、Reranker 协议。
- pgvector dense search。
- PostgreSQL full text 或 OpenSearch BM25 sparse search。
- RRF merge、dedup、score threshold、metadata filter、ACL filter。
- retrieval_logs 记录 query、top_k、scores、latency、tenant_id。

验收：

- Dense、Sparse、RRF、ACL filter、rerank interface 单测。
- 至少 20 条 eval query，能输出 recall / hit rate / citation coverage。

### Phase 3：RAG Answering

目标：从“能检索”变成“能可信回答”。

- Query rewrite 可选接口。
- Context packing：token budget、相邻 chunk 合并、父子上下文补齐。
- Prompt builder：上下文边界、无答案策略、prompt injection 防护。
- LLMProvider：generate 和 stream。
- Citation extraction：answer 与 chunk/page/source 绑定。
- SSE：token、citation、error、final。

验收：

- Prompt builder、context packer、citation extractor 单测。
- Fake LLM provider 覆盖无答案、引用、多轮上下文。

### Phase 4：受控 Agent

目标：让 Agent 成为可审计工具执行系统，而不是无限循环聊天。

- Tool Registry：schema、permission、timeout、rate_limit、handler。
- 工具：rag_search、calculator、file_reader。
- Agent Runtime：ReAct 起步，max_steps、max_tool_calls、repeated action detection。
- 每次 tool_call 记录 user_id、tenant_id、参数摘要、结果摘要、latency、error。

验收：

- tool permission 测试。
- max_steps 和 repeated action 测试。
- file_reader 只能读 allowlist 范围。

### Phase 5：产品化和就业包装

目标：把项目变成可以展示的完整作品。

- Open WebUI 接入或 React 管理台。
- 管理页：文档、版本、ingestion job、检索日志、eval report。
- Demo 数据集：制度文件、产品手册、FAQ、技术文档。
- README：架构图、启动命令、API 示例、评估结果、权限演示。
- 面试讲解材料：3 分钟、10 分钟、30 分钟三个版本。

验收：

- Docker Compose 一键启动核心服务。
- 有一条完整 demo 脚本。
- 有失败案例和改进记录。

## 6. 质量与指标

### 6.1 产品指标

- answer success rate：用户问题返回可用答案的比例。
- no-answer correctness：无法确认时是否正确拒答。
- citation coverage：关键结论带 citation 的比例。
- first-token latency：流式首 token 延迟。
- retrieval latency：检索和 rerank 分阶段耗时。
- ingestion throughput：单位时间处理 chunk 数。
- permission leakage rate：越权检索必须为 0。

### 6.2 RAG Eval 指标

最低实现：

- retrieval hit rate。
- context precision。
- answer groundedness / faithfulness。
- citation accuracy。
- no-answer rate。

进阶实现：

- 基于黄金答案的 correctness。
- 多轮问答上下文一致性。
- prompt injection 测试集。
- ACL / tenant 隔离回归集。

## 7. 风险与取舍

| 风险 | 表现 | 处理 |
| --- | --- | --- |
| 过早做复杂 Agent | 功能多但不可控 | RAG 闭环和 Tool Registry 稳定后再做 Planner |
| 只做向量检索 | 编号、条款、专有名词召回差 | BM25 和 RRF 是第一阶段必需 |
| 缺少 eval | 无法证明系统质量 | 从 20 条人工 query 起步，逐步扩充 |
| 前端消耗过多 | 后端核心不完整 | 先接 Open WebUI 或做轻管理台 |
| 绑定单一模型 | 换模型成本高 | Provider 抽象和 fake provider 先行 |
| 合规后补 | 权限、审计难补 | tenant_id、ACL、audit log 从数据模型开始设计 |

## 8. 推荐立即修改的执行口径

从现在开始，每个功能都按以下顺序落地：

1. 先写接口和领域对象。
2. 再写 fake adapter 和单测。
3. 再接真实 infrastructure adapter。
4. 再写 API route。
5. 最后补文档、日志、权限和 eval。

任何跳过这条顺序的实现，都容易退化成一次性 demo。

## 9. 主要公开资料来源

- World Economic Forum, The Future of Jobs Report 2025: https://www.weforum.org/publications/the-future-of-jobs-report-2025/
- Stanford HAI, AI Index Report: https://aiindex.stanford.edu/report/
- Gartner, Agentic AI project cancellation risk: https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-cancelled-by-end-of-2027
- Grand View Research, Retrieval Augmented Generation Market Report: https://www.grandviewresearch.com/industry-analysis/retrieval-augmented-generation-rag-market-report
- Grand View Research, AI Agents Market Report: https://www.grandviewresearch.com/industry-analysis/ai-agents-market-report
- Dify Documentation: https://docs.dify.ai/
- Open WebUI Knowledge Documentation: https://docs.openwebui.com/features/workspace/knowledge/
- LangGraph Documentation: https://docs.langchain.com/oss/python/langgraph/overview
- LlamaIndex Documentation: https://docs.llamaindex.ai/
- OWASP Top 10 for LLM Applications 2025: https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/
- NIST AI Risk Management Framework: https://www.nist.gov/itl/ai-risk-management-framework
- pgvector: https://github.com/pgvector/pgvector
- PostgreSQL Full Text Search: https://www.postgresql.org/docs/current/textsearch.html
- Milvus Documentation: https://milvus.io/docs
- OpenSearch Hybrid Search Documentation: https://docs.opensearch.org/docs/latest/search-plugins/search-pipelines/normalization-processor/
- Ragas Documentation: https://docs.ragas.io/
- OpenTelemetry GenAI Semantic Conventions: https://opentelemetry.io/docs/specs/semconv/gen-ai/
- 国家互联网信息办公室，生成式人工智能服务管理暂行办法: https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm
- 中国人大网法律法规数据库: https://flk.npc.gov.cn/
