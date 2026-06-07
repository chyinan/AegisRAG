---
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md
  - _bmad-output/planning-artifacts/architecture.md
project_name: 本地化多源知识增强 RAG + Agent 问答系统
workflow: bmad-create-epics-and-stories
currentStep: 4
status: complete
completedAt: 2026-05-27
validation:
  frCoverage: complete
  storyCount: 39
  epicCount: 6
  placeholdersRemaining: false
---

# 本地化多源知识增强 RAG + Agent 问答系统 - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for 本地化多源知识增强 RAG + Agent 问答系统, decomposing the requirements from the PRD and Architecture requirements into implementable stories.

## Requirements Inventory

### Functional Requirements

FR1: 授权用户可以上传 PDF、DOCX、TXT、Markdown 文件，并可为文档设置 `tenant_id`、source metadata 和 `acl`；上传请求必须返回 `document_id`、`version_id`、`job_id` 和初始状态，不等待 embedding 完成。

FR2: 系统必须支持 PDF、DOCX、TXT、Markdown parser，并输出统一的 `ParsedDocument` 和 `Section`；parser 错误必须转化为领域异常并写入 job 状态。

FR3: 系统必须提供 `Chunker` 协议，至少实现 FixedSizeChunker，并预留 SemanticChunker 和 HierarchicalChunker；chunk metadata 必须包含 document/version/chunk/tenant/source/page/token/acl/checksum 等治理字段。

FR4: 系统必须记录文档版本，删除默认软删除，并支持按 `document_id` 和可选 `version_id` 重建、删除或标记索引。

FR5: 系统必须提供 `EmbeddingProvider` 抽象，支持 batch embedding、timeout、retry、rate limit 和 Fake provider；测试默认不得真实调用外部 LLM API。

FR6: 每个 embedding job 和向量记录必须记录 `embedding_provider`、`embedding_model`、`embedding_version` 和 `embedding_dim`；维度不一致或模型变更时不得静默复用旧索引。

FR7: 系统必须提供 `VectorStore` 协议，支持 upsert、search、delete_by_document、metadata filter、tenant filter、ACL filter、soft delete、top_k 和 score threshold；默认实现为 pgvector，预留 FAISS/Milvus。

FR8: 系统必须通过 `EmbeddingProvider` 和 `VectorStore` 执行 Dense Retrieval，并在查询阶段应用 `tenant_id`、metadata 和 `acl` 过滤。

FR9: 系统必须支持 BM25 或 PostgreSQL full text / OpenSearch Sparse Retrieval，用于条款、编号、错误码、人名、产品型号等关键词召回；MVP 不允许只依赖纯向量检索。

FR10: 系统必须支持 RRF 或加权融合，将 dense 和 sparse 结果合并、去重，并保留融合来源或原因；低于 score threshold 的结果不得进入上下文。

FR11: 系统必须提供 `Reranker` 协议，支持 Fake reranker 和后续 cross-encoder / LLM rerank adapter；rerank 前后必须记录分数和 latency。

FR12: 每次 retrieval 必须记录可复盘日志，至少包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、query 摘要、dense top_k、sparse top_k、RRF 结果、rerank score、latency、error_code。

FR13: 系统必须支持 Context Packing，包括 token budget、chunk 去重、按 rerank 分数排序、相邻 chunk 合并和父子上下文补齐；未授权 chunk 不得进入 context packer。

FR14: 系统必须提供 PromptBuilder，明确上下文边界、citation 要求、无答案策略和 prompt injection 防护；route 层不得拼接 prompt。

FR15: 系统必须提供 `LLMProvider` 协议，支持 generate 和 stream，并可接 OpenAI、Qwen、DeepSeek、本地 vLLM、Ollama；业务代码不得直接依赖单一厂商 SDK。

FR16: 最终问答结果必须包含 answer 和 citations；citation 至少包含 `document_id`、`chunk_id`、`source`、`page` 或页码范围，且不得伪造来源。

FR17: 系统必须支持 SSE streaming，事件类型至少包括 `token`、`citation`、`tool_call`、`tool_result`、`error`、`final`；流式错误必须结构化返回。

FR18: 系统必须提供 `POST /upload`、`POST /retrieve`、`POST /query`、`POST /chat`、`POST /sources/resolve`、`POST /agent/run`；所有 API 支持 `request_id`、`user_id`、`tenant_id`，`session_id` 可选，并返回统一 data/error/metadata 结构。

FR19: 系统必须支持 chat session 和 chat message 持久化，禁止用全局变量保存用户会话；会话上下文进入 prompt 前必须经过 token budget 和安全过滤。

FR20: MVP 首选通过 Open WebUI 兼容 chat adapter 接入，由后端 `/chat` 承载 RAG、citation、SSE 和权限治理；最小自定义 sidecar 仅用于 Source Inspector、上传/job 状态、日志和 eval 入口，且不挤占 RAG 核心开发优先级。

FR21: 系统必须定义统一 AuthContext，包含 `user_id`、`tenant_id`、`roles`、`department`、`permissions`；缺少 tenant 或 user 的业务请求必须被拒绝。

FR22: 系统必须在 retrieval 阶段执行 tenant、RBAC 和 ACL filter，禁止先检索全量再在答案中过滤；无权限文档 chunk 不得进入 rerank、context packing 或 prompt。

FR23: 系统必须记录关键业务行为审计，包括上传、删除、检索、问答、Agent run 和 tool call；审计日志必须包含 request/trace/tenant/user/action/resource/latency/status/error_code。

FR24: 系统必须支持文档软删除、版本追踪、日志保留策略和配置化清理；软删除文档默认不可检索。

FR25: 系统必须提供 Tool Registry；工具定义必须包含 name、description、input_schema、output_schema、permission、timeout、rate_limit、handler，并在执行前校验 schema 和 permission。

FR26: 系统必须支持 `rag_search`、`calculator`、`file_reader`，并可后续扩展 `web_search`；`rag_search` 复用 retrieval 权限过滤，`file_reader` 只能读取 allowlist 范围。

FR27: 系统必须支持 ReAct 起步的 Agent Runtime，并预留 Planner-Executor 和 LangGraph 风格状态图；runtime 必须具备 max_steps、max_tool_calls、timeout 和 repeated action detection。

FR28: 每次工具调用必须记录审计日志，包含 agent_run_id、tool_name、参数摘要、结果摘要、latency、status、error_code、tenant_id、user_id，且不得记录敏感全文或密钥。

FR29: 系统必须支持维护可执行 RAG eval dataset，用于 retrieval、citation、no-answer、ACL 隔离和 prompt injection 回归；Phase 2 至少包含 20 条可执行 synthetic eval query，占位样例不能满足 smoke gate。

FR30: 系统必须使用结构化日志记录 request、retrieval、generation、tool 和 error；字段至少包含 request_id、trace_id、tenant_id、user_id、latency、model、token usage、retrieval top_k、rerank score、tool calls、error_code。

FR31: 系统必须预留 Prometheus、OpenTelemetry 和 Grafana 接入；MVP 至少暴露 health/readiness 和关键 latency 指标，并能观测 worker queue backlog。

FR32: 系统必须支持 Docker Compose 启动核心服务；Compose 至少包含 api、worker-ingestion、worker-embedding、postgres、redis、minio，opensearch/milvus/prometheus/grafana 可选。

### NonFunctional Requirements

NFR1: 安全边界必须默认不信任用户输入、文档内容、Web 内容和 Tool output；系统规则必须高于检索内容和工具输出。

NFR2: 权限逻辑必须在后端策略执行，严禁放入 prompt 或交给 LLM 判断；`tenant_id`、RBAC、ACL 必须从数据模型和检索查询阶段贯穿。

NFR3: Prompt injection 防护必须覆盖文档诱导忽略系统提示、泄露密钥、越权读取文件、Web 页面诱导危险工具等场景。

NFR4: 外部 provider 调用必须配置 timeout、retry budget 和错误映射；不得硬编码 API key、数据库地址、模型密钥、文件绝对路径、tenant_id 或 user_id。

NFR5: 日志、eval artifact、retrieval log 和 audit log 不得记录 API key、access token、企业机密全文或用户敏感原文。

NFR6: `/upload` API 路径不得等待大批量 embedding 完成，必须异步返回 job id 和状态。

NFR7: `/query` 和 `/chat` 必须支持 SSE，优先优化 first-token latency，并记录 retrieval、rerank、context packing、generation 分阶段耗时。

NFR8: worker 任务必须支持失败状态、重试、错误原因、attempt_count 和 next_retry_at；文档索引状态必须可追踪。

NFR9: 数据删除默认软删除，避免误删后不可恢复；软删除数据默认不参与检索。

NFR10: 核心模块必须可单元测试；测试默认禁止真实调用外部 LLM API，必须使用 Fake Provider 或 mock。

NFR11: Provider、VectorStore、Reranker、Tool Registry 和 Agent Runtime 必须有 fake 或 mock 测试实现。

NFR12: 默认实现优先 PostgreSQL + pgvector，但 VectorStore 接口必须允许 FAISS 和 Milvus 接入。

NFR13: LLM 和 Embedding Provider 必须可配置切换，业务代码不得绑定单一厂商 SDK。

NFR14: Agent 工作流先支持 ReAct，后续可扩展 Planner-Executor 和 LangGraph 风格状态图；没有 max_steps、timeout、audit 的 Agent 不允许进入主线。

NFR15: API route 必须保持薄层职责，只处理 schema、认证上下文注入、service 调用和响应封装，不得直接调用 LLM、向量数据库或复杂业务逻辑。

NFR16: 系统必须按 API Layer、Application Service Layer、Domain Layer、Infrastructure Layer、Storage Layer 分层，Domain 不能依赖 FastAPI、SQLAlchemy、Redis、MinIO 或外部 SDK。

NFR17: Docker Compose 本地部署必须包含 health check、migration、worker retry 和核心依赖服务；生产环境必须考虑 graceful shutdown、secret management、backup/restore 和 queue backlog monitoring。

NFR18: MVP 性能目标以可观测和可调优为主；在缺少真实并发和文档规模前，不设置硬性 p95 SLA，但必须保留指标采集和分阶段 latency 证据。

### Additional Requirements

- **Starter Template:** 架构明确选择 `Custom uv + FastAPI Monorepo`，而不是直接采用 FastAPI Full Stack Template；第一条实现故事应建立 `uv` 管理的 Python monorepo、FastAPI API 入口和符合 `AGENTS.md` 的 `apps/`、`packages/`、`tests/`、`docs/`、`docker/` 结构。

- 初始化命令基线为 `uv init --package .`，随后加入 FastAPI、Pydantic settings、SQLAlchemy、Alembic、asyncpg/psycopg、Redis、RQ、httpx、structlog、python-multipart，以及 pytest、pytest-asyncio、ruff、mypy。

- 后端运行时基线为 Python 3.11+，FastAPI + Pydantic v2 + SQLAlchemy 2.x + Alembic；所有接口和内部 DTO 必须使用类型标注。

- PostgreSQL 18 系列 + pgvector 是默认 metadata、关系数据和向量检索承载层；pgvector 版本基线为 0.8.x。

- PostgreSQL full text search 是 MVP sparse retrieval 默认方案；OpenSearch 作为 adapter 后置增强，不作为第一阶段阻塞依赖。

- Redis + RQ 是 MVP 异步任务默认方案；RQ 队列参数必须使用 JSON 可序列化的原始类型或显式安全 serializer，避免不可信 pickle 对象风险。

- MinIO/S3-compatible ObjectStorage 必须通过端口抽象接入，用于保存 raw files 和 normalized artifacts。

- 目录边界必须遵循：`apps/*` 只放可运行进程，`packages/*` 放可导入模块；`packages/*/domain` 不得导入 FastAPI、SQLAlchemy、Redis、MinIO 或外部 SDK。

- storage model 和 domain DTO 必须分离；SQLAlchemy model 不得直接传入 retrieval、rag、agent domain 逻辑。

- API 必须实现统一 response envelope：`request_id`、`data`、`error`、`metadata`；expected domain errors 必须映射到稳定错误码。

- 必须额外提供 `GET /health` 和 `GET /ready`，作为 Docker Compose、本地开发和生产 readiness 的基础。

- SSE payload 必须包含 `request_id`，相关场景还要包含 `trace_id`；citation event 必须携带 document/version/chunk/source/page 信息。

- job status 必须采用稳定状态集合：`uploaded`、`parsing`、`parsed`、`chunking`、`chunked`、`embedding`、`indexing`、`retrieval_ready`、`failed_retryable`、`failed_terminal`、`deleted`。

- application service 必须显式接收 AuthContext 或 RequestContext；AuthContext 的策略 builder 将 RBAC/ACL 转换成 tenant、ACL、metadata filters。

- MVP 认证方案固定为开发/测试模拟 AuthContext + 轻量 JWT adapter。模拟 AuthContext 只能在开发、测试或显式本地配置中启用；JWT adapter 是 API/Open WebUI 集成默认路径；两者必须产出同一 `AuthContext` DTO，并统一进入 RBAC、ACL、tenant filter 和 audit。

- retrieval 默认数据流为 optional query rewrite -> dense + sparse retrieval with ACL filters -> RRF merge + dedup -> rerank -> threshold -> context packing。

- RAG 默认数据流为 context packing -> prompt build -> LLM generate/stream -> citation extraction -> audit/retrieval log。

- Agent 默认数据流为 AuthContext + policy check -> AgentRuntime -> LLM step -> ToolRegistry validation -> permission/timeout/rate_limit -> tool handler -> tool audit -> final answer validation。

- Database schema 至少覆盖 users、tenants、roles、documents、document_versions、chunks、embedding_jobs、retrieval_logs、chat_sessions、chat_messages、agent_runs、tool_calls；所有表包含 `id`、`created_at`、`updated_at`，关键业务表包含 `tenant_id`、`created_by`、`status`。

- CI/CD 初始流水线必须包含 install、ruff、pytest unit、pytest integration mock、alembic migration check、docker build；retrieval eval smoke test 从 Epic 3 加入，RAG citation/no-answer eval 在 Epic 5 扩展。

- eval 和 observability 不是后置文档工作；从 retrieval 阶段开始需要同步建立可执行 eval dataset、retrieval logs 和 structured logging。

- Open WebUI 首个集成路径固定为兼容 chat adapter backed by `/chat`，但不是权限治理边界；前端不得判断权限、补造 citation 或推断 retrieval result。

- FR trace tooling 必须把 `FR1` 和 `FR-1` 视为同一编号，文档展示可保持各自风格，但自动校验必须 normalize hyphen。

### UX Design Requirements

UX artifacts are included in the planning set:

- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md`

Story acceptance criteria must preserve the UX rules for Source Inspector, Knowledge Admin, Retrieval Diagnostics, Eval Reports, Agent Run Review, WCAG 2.2 AA, keyboard focus, `aria-live`, alert regions, non-color-only status, and long identifier wrapping/truncation when any custom UI or sidecar is implemented.

### FR Coverage Map

FR1: Epic 2 - 文档上传创建可异步处理的知识资产。
FR2: Epic 2 - 多格式 parser 输出统一文档结构。
FR3: Epic 2 - chunker 协议和 FixedSizeChunker 生成可索引 chunk。
FR4: Epic 2 - 文档版本治理、软删除和按版本索引管理。
FR5: Epic 2 - Embedding Provider 抽象和 fake/testing 支撑。
FR6: Epic 2 - embedding provider/model/version/dim 元数据记录和兼容性校验。
FR7: Epic 2 - VectorStore 统一接口和 pgvector 默认实现边界。
FR8: Epic 3 - Dense Retrieval 带 tenant、metadata、ACL 过滤。
FR9: Epic 3 - BM25/PostgreSQL full text Sparse Retrieval 带相同权限过滤。
FR10: Epic 3 - RRF 或加权融合、去重和阈值过滤。
FR11: Epic 3 - Reranker 协议、fake 实现、分数和 latency 记录。
FR12: Epic 3 - retrieval 可复盘日志。
FR13: Epic 4 - context packing 预算、去重、排序、相邻合并和父子补齐。
FR14: Epic 4 - PromptBuilder 上下文边界、citation、无答案和 prompt injection 防护。
FR15: Epic 4 - LLMProvider generate/stream 抽象和 fake provider。
FR16: Epic 4 and Epic 6 - answer + citations 结果结构、citation extraction 和 Agent final answer validation。
FR17: Epic 4 - SSE streaming 事件协议。
FR18: Epic 1, Epic 4, and Epic 6 - 核心 API、`POST /sources/resolve`、`POST /agent/run`、统一响应 envelope 和 route 薄层契约。
FR19: Epic 4 - chat session/message 持久化和安全会话上下文。
FR20: Epic 4 - Open WebUI 兼容 chat adapter、Source Inspector 和轻量前端集成路径。
FR21: Epic 1 - AuthContext 和缺失认证上下文拒绝。
FR22: Epic 3 and Epic 6 - retrieval 阶段 tenant、RBAC、ACL 过滤和 Agent final answer 权限校验。
FR23: Epic 1 and Epic 6 - 上传、删除、检索、问答、Agent run、tool call 审计日志基础。
FR24: Epic 2 - 文档软删除、版本追踪、日志保留和配置化清理。
FR25: Epic 6 - Tool Registry 定义、schema、permission、timeout、rate_limit、handler。
FR26: Epic 6 - `rag_search`、`calculator`、`file_reader` 工具。
FR27: Epic 6 - ReAct Agent Runtime、max_steps、max_tool_calls、timeout、重复动作检测。
FR28: Epic 6 - tool call audit log。
FR29: Epic 3 and Epic 5 - retrieval eval 前置、RAG eval dataset 和质量回归。
FR30: Epic 1 - request、retrieval、generation、tool、error 结构化日志。
FR31: Epic 1 - health/readiness、latency metrics 和 observability 预留。
FR32: Epic 1 - Docker Compose 本地核心服务启动。

## Epic List

### Epic 1: 可运行且可治理的平台基础

平台负责人可以本地启动系统，获得统一 API 契约、认证上下文、结构化错误、审计日志、健康检查和基础可观测能力，为后续知识接入、检索、问答和 Agent 提供安全底座。

**FRs covered:** FR18, FR21, FR23, FR30, FR31, FR32

### Epic 2: 知识文档接入到可检索资产

知识库管理员可以上传 PDF、DOCX、TXT、Markdown 文档，系统异步完成解析、清洗、chunk、embedding、索引写入、版本治理和软删除，使文档进入 `retrieval_ready` 状态。

**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR7, FR24

### Epic 3: 授权 Hybrid Retrieval 与检索复盘

企业员工可以只在授权范围内检索知识，系统同时使用 dense retrieval 和 BM25 sparse retrieval，经 RRF merge、dedup、rerank、threshold filter 输出可追踪结果；平台工程师可以复盘每次检索质量。

**FRs covered:** FR8, FR9, FR10, FR11, FR12, FR22, FR29

### Epic 4: 可信 RAG 问答、Citation 与流式会话

企业员工可以通过 `/query` 或 `/chat` 获得仅基于授权上下文的回答、citation、无答案策略和 SSE 流式输出；系统支持会话记忆并可对接 Open WebUI 或轻量前端。

**FRs covered:** FR13, FR14, FR15, FR16, FR17, FR18, FR19, FR20

### Epic 5: RAG 质量评估与回归证据

平台工程师可以维护 eval dataset，验证 retrieval hit rate、citation coverage、no-answer correctness、ACL 隔离和 prompt injection 回归，避免 RAG 质量只能靠人工感觉判断。

**FRs covered:** FR29

### Epic 6: 受控 Agent 工具执行

交付顾问或授权用户可以运行受控 Agent，通过 Tool Registry 调用 `rag_search`、`calculator`、`file_reader`，并受 schema、permission、timeout、rate limit、max_steps、max_tool_calls 和 audit log 约束。

**FRs covered:** FR16, FR18, FR22, FR23, FR25, FR26, FR27, FR28

## Epic 1: 可运行且可治理的平台基础

平台负责人可以本地启动系统，获得统一 API 契约、认证上下文、结构化错误、审计日志、健康检查和基础可观测能力，为后续知识接入、检索、问答和 Agent 提供安全底座。

### Story 1.1: Set up initial project from starter template（初始化生产级 FastAPI Monorepo）

**Requirements covered:** FR18, FR30, FR32

As a 平台工程师,
I want 一个符合架构规则的 `uv + FastAPI` monorepo 骨架,
So that 后续 ingestion、retrieval、RAG 和 Agent 能按清晰边界开发。

**Acceptance Criteria:**

**Given** 一个新仓库
**When** 开发者初始化项目
**Then** 必须创建 `apps/api`、`apps/worker`、`packages/common`、`packages/auth`、`packages/data`、`tests/unit`、`tests/integration`、`docs`、`docker`
**And** 根目录包含 `pyproject.toml`、`.env.example`、pytest 配置和 ruff 配置

**Given** 项目依赖已同步
**When** 开发者执行 `uv run pytest`
**Then** 至少一个基础 smoke test 通过
**And** 测试不得调用真实外部 LLM、embedding provider 或向量数据库

**Given** 后续开发者添加业务模块
**When** import 关系被检查
**Then** `packages/*/domain` 不依赖 FastAPI、SQLAlchemy、Redis、MinIO 或外部 SDK
**And** route 代码只能位于 `apps/api/routes`

### Story 1.2: 统一 API Envelope 与健康检查

**Requirements covered:** FR18, FR31

As a API 调用方,
I want 所有非流式 API 使用统一响应结构,
So that 前端、Open WebUI adapter 和测试都能稳定解析成功与错误响应。

**Acceptance Criteria:**

**Given** API 服务已启动
**When** 调用 `GET /health`
**Then** 返回统一 envelope，包含 `request_id`、`data`、`error`、`metadata`
**And** `error` 在成功响应中为 `null`

**Given** API 服务已启动
**When** 调用 `GET /ready`
**Then** 返回 readiness 状态和依赖摘要
**And** 未就绪依赖必须以结构化数据表达，不依赖日志文本解析

**Given** 任意 route 返回业务数据
**When** 响应被序列化
**Then** 必须使用 Pydantic v2 schema
**And** route 不直接执行复杂业务逻辑

### Story 1.3: RequestContext 与 AuthContext 注入

**Requirements covered:** FR18, FR21

As a 后端开发者,
I want API 层统一生成 `RequestContext` 和 `AuthContext`,
So that 所有业务服务都能显式接收 `user_id`、`tenant_id`、roles 和 permissions。

**Acceptance Criteria:**

**Given** 请求缺少 `tenant_id` 或 `user_id`
**When** 访问需要认证上下文的业务 endpoint
**Then** API 返回结构化错误 `AUTH_CONTEXT_REQUIRED`
**And** application service 不会被调用

**Given** 请求包含 JWT bearer token、开发模拟上下文 header 或测试 fixture 上下文
**When** route 调用 application service
**Then** service 接收到类型化 `RequestContext`
**And** `AuthContext` 至少包含 `user_id`、`tenant_id`、`roles`、`department`、`permissions`
**And** JWT adapter 与模拟 AuthContext parser 必须产出相同的 `AuthContext` DTO

**Given** 后续 retrieval 或 tool policy 需要权限过滤
**When** 调用 auth policy builder
**Then** 能从 `AuthContext` 生成 tenant、RBAC、ACL 和 metadata filter 的基础结构
**And** 不把权限规则拼进 prompt

### Story 1.4: 结构化错误、日志与审计基础

**Requirements covered:** FR23, FR30

As a 平台运维人员,
I want 预期错误、请求日志和审计事件结构化记录,
So that 后续上传、检索、问答和 Agent 行为可以追踪与排障。

**Acceptance Criteria:**

**Given** application service 抛出预期领域错误
**When** API 捕获错误
**Then** 返回稳定 `error.code`、`error.message`、`error.details`
**And** 不暴露原始异常类名、堆栈或敏感字段

**Given** 任意 API 请求完成
**When** 日志被写出
**Then** 日志包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`latency`、`error_code`
**And** 不记录 API key、access token、企业机密全文或用户敏感原文

**Given** 业务服务记录审计事件
**When** 调用 audit port
**Then** 审计事件包含 action、resource、tenant_id、user_id、status、latency、error_code
**And** 先提供 fake/in-memory audit adapter 供测试使用

### Story 1.5: 最小数据库迁移与基础治理表

**Requirements covered:** FR21, FR23

As a 平台工程师,
I want Alembic 管理基础身份、租户和审计表,
So that 后续业务数据能够从第一天开始携带治理字段。

**Acceptance Criteria:**

**Given** 数据库为空
**When** 运行 Alembic migration
**Then** 创建 `tenants`、`users`、`roles`、基础用户角色关系表和 `audit_logs`
**And** 所有表包含 `id`、`created_at`、`updated_at`

**Given** 关键业务表被创建
**When** 检查 schema
**Then** 表必须包含适用的 `tenant_id`、`created_by`、`status`
**And** migration 不依赖应用启动时自动建表

**Given** 测试环境运行 storage smoke test
**When** 插入一条租户、用户和审计事件
**Then** repository 返回类型化 DTO
**And** domain 层不接收 SQLAlchemy model

### Story 1.6: Docker Compose 本地启动核心依赖

**Requirements covered:** FR31, FR32

As a 平台负责人,
I want 使用 Docker Compose 启动 API、worker、PostgreSQL、Redis 和 MinIO,
So that 本地环境可以稳定复现后续 RAG 开发链路。

**Acceptance Criteria:**

**Given** 开发者配置 `.env`
**When** 执行 Docker Compose 启动命令
**Then** `api`、`worker-ingestion`、`worker-embedding`、`postgres`、`redis`、`minio` 服务被定义
**And** `api` 提供 health check

**Given** PostgreSQL、Redis 或 MinIO 未就绪
**When** 调用 `GET /ready`
**Then** readiness 返回明确的未就绪状态
**And** 日志包含可排障的 dependency 状态摘要

**Given** worker 服务启动
**When** 查看 worker 配置
**Then** ingestion 和 embedding worker 使用不同 queue name
**And** queue payload 只包含 JSON 可序列化 ID 和参数摘要

## Epic 2: 知识文档接入到可检索资产

知识库管理员可以上传 PDF、DOCX、TXT、Markdown 文档，系统异步完成解析、清洗、chunk、embedding、索引写入、版本治理和软删除，使文档进入 `retrieval_ready` 状态。

### Story 2.1: 授权文档上传与异步 Ingestion Job

**Requirements covered:** FR1

As a 知识库管理员,
I want 上传文档后立即获得文档版本和 job 状态,
So that 大文件 embedding 不会阻塞上传体验。

**Acceptance Criteria:**

**Given** 授权用户提交 PDF、DOCX、TXT 或 Markdown 文件
**When** 调用 `POST /upload`
**Then** API 返回 `document_id`、`version_id`、`job_id`、`status`
**And** 初始状态为 `uploaded` 或 `parsing`

**Given** 上传请求缺少文档管理权限
**When** 调用 `POST /upload`
**Then** API 返回结构化权限错误
**And** 不写入 object storage、document metadata 或 queue job

**Given** 上传成功
**When** metadata 被保存
**Then** `documents` 和 `document_versions` 包含 `tenant_id`、`created_by`、`source_type`、`source_uri`、`acl`、`checksum`、`status`
**And** worker queue payload 只包含 IDs，不包含文件内容

**Given** documents 和 document_versions 表首次引入
**When** Alembic migration 生成
**Then** 两张表包含 id、created_at、updated_at、tenant_id、created_by、status、acl、checksum 和 source metadata
**And** 支持按 tenant_id、document_id、version_id、status 查询

### Story 2.2: Parser 协议与 Markdown/TXT 解析

**Requirements covered:** FR2

As a 知识库管理员,
I want Markdown 和 TXT 文档被标准化为统一解析结构,
So that 后续 chunking 和 indexing 不关心原始格式差异。

**Acceptance Criteria:**

**Given** Markdown 文档包含多级标题
**When** Markdown parser 解析文档
**Then** 输出 `ParsedDocument`、`Section` 和标题层级
**And** 每个 section 保留 `title_path` 和 source metadata

**Given** TXT 文档无显式标题
**When** TXT parser 解析文档
**Then** 输出至少一个默认 section
**And** parser 不丢失 source_uri、tenant_id、document_id、version_id

**Given** parser 遇到非法编码或空文件
**When** ingestion worker 捕获错误
**Then** 错误被转换为领域异常
**And** job 状态更新为 `failed_retryable` 或 `failed_terminal`，包含 error_code

### Story 2.3: PDF/DOCX Parser 与页码 Metadata

**Requirements covered:** FR2, FR16

As a 知识库管理员,
I want PDF 和 DOCX 文档被解析并尽量保留页码或结构信息,
So that 后续 citation 能追溯到原文位置。

**Acceptance Criteria:**

**Given** PDF 文档包含多页文本
**When** PDF parser 解析文档
**Then** section 或 block metadata 包含 `page_start` 和 `page_end`
**And** 解析结果可被 chunker 消费

**Given** DOCX 文档包含标题和段落
**When** DOCX parser 解析文档
**Then** 输出保留标题层级的 `Section`
**And** 没有页码时必须显式设置页码为空而不是伪造页码

**Given** PDF 或 DOCX parser 失败
**When** worker 更新 job
**Then** 失败原因可从 job 状态中查询
**And** 日志只记录错误摘要，不记录企业机密全文

### Story 2.4: Cleaner 与 Dedup

**Requirements covered:** FR3

As a 知识库管理员,
I want 解析后的文档先被清洗和去重,
So that 后续 chunking 不会把页眉页脚、重复 section 或噪声内容写入索引。

**Acceptance Criteria:**

**Given** `ParsedDocument` 包含重复空白、页眉页脚或重复 section
**When** cleaner 和 dedup 执行
**Then** 输出稳定、可测试的清洗结果
**And** checksum 能用于识别重复内容

**Given** 清洗过程删除或合并内容
**When** 输出 cleaned document
**Then** 保留 document_id、version_id、tenant_id、source_uri、title_path 和页码范围
**And** 不丢失后续 chunk metadata 所需字段

**Given** cleaner 或 dedup 单测运行
**When** 输入重复段落、空白、页眉页脚和空文档
**Then** 覆盖正常、边界和异常场景
**And** 不调用 embedding、LLM 或 vector store

### Story 2.5: FixedSizeChunker

**Requirements covered:** FR3

As a 知识库管理员,
I want 清洗后的文档按固定大小切成可检索 chunk,
So that MVP 有稳定、可测试的默认 chunk 策略。

**Acceptance Criteria:**

**Given** 文档进入 FixedSizeChunker
**When** 使用默认策略切分
**Then** chunk token_count 默认落在 500 到 800 token 目标范围内
**And** overlap 支持 10% 到 20% 配置

**Given** 文档包含标题层级和页码
**When** chunker 切分跨 section 内容
**Then** chunk 保留 title_path、page_start、page_end 和原始 section 关联
**And** 单测覆盖 overlap、标题路径、页码和 token_count

**Given** token 估算器不可用或文本异常
**When** chunker 执行
**Then** 返回领域错误或安全降级的 token_count
**And** 不产生缺少治理字段的 chunk

### Story 2.6: Chunk Metadata Contract 与持久化

**Requirements covered:** FR3, FR4, FR16, FR22

As a 平台工程师,
I want chunk metadata 和数据库迁移明确落地,
So that retrieval、citation、ACL 和版本治理能共享同一可信数据契约。

**Acceptance Criteria:**

**Given** chunk 被生成
**When** 检查 metadata
**Then** 必须包含 `document_id`、`version_id`、`chunk_id`、`tenant_id`、`source_type`、`source_uri`、`title_path`、`page_start`、`page_end`、`token_count`、`acl`、`checksum`
**And** metadata schema 使用 typed DTO，不直接暴露 SQLAlchemy model

**Given** chunks 表首次引入
**When** Alembic migration 生成
**Then** `chunks` 包含 id、created_at、updated_at、tenant_id、document_id、version_id、chunk_id、status、acl、checksum、source metadata 和页码字段
**And** 建立 tenant/document/version/chunk 查询所需索引

**Given** 检索、citation 或 Source Inspector 使用 chunk
**When** 从 storage 转换为 domain DTO
**Then** 保留权限和来源字段
**And** 未授权 chunk 不得进入 retrieval、context packing、prompt 或 source detail response

### Story 2.7: EmbeddingProvider 抽象与 Embedding Job

**Requirements covered:** FR5, FR6

As a 平台工程师,
I want embedding 通过 Provider 抽象批量执行并可测试,
So that 系统可以切换 OpenAI、Qwen、DeepSeek、本地 vLLM 或 Ollama embedding 实现。

**Acceptance Criteria:**

**Given** chunk 已生成
**When** embedding worker 处理 job
**Then** 通过 `EmbeddingProvider.embed_texts` 批量生成向量
**And** provider 调用包含 timeout、retry budget 和 rate limit 配置

**Given** 测试环境运行 embedding 单测
**When** 调用 embedding service
**Then** 使用 `FakeEmbeddingProvider`
**And** 不发生真实外部 API 调用

**Given** embedding_jobs 表首次引入
**When** Alembic migration 生成
**Then** 表包含 id、created_at、updated_at、tenant_id、created_by、status、document_id、version_id、provider、model、version、dim、attempt_count、next_retry_at、error_code
**And** job 状态可表达 retryable 和 terminal failure

**Given** provider 返回维度、模型或版本信息
**When** embedding record 被保存
**Then** 记录 `embedding_provider`、`embedding_model`、`embedding_version`、`embedding_dim`
**And** provider 超时更新 job 为可重试失败状态

### Story 2.8: VectorStore 协议与 pgvector 写入

**Requirements covered:** FR6, FR7

As a 平台工程师,
I want 向量写入通过统一 `VectorStore` 接口完成,
So that 默认使用 pgvector，同时保留 FAISS 和 Milvus 的替换边界。

**Acceptance Criteria:**

**Given** embedding vectors 已生成
**When** 调用 `VectorStore.upsert`
**Then** vectors 与 chunk metadata 一起写入默认 pgvector adapter
**And** 写入记录包含 tenant、ACL、document/version/chunk、embedding model 和 dim

**Given** embedding_dim 与目标索引维度不一致
**When** 执行 upsert
**Then** 系统拒绝写入并返回 `INDEX_DIMENSION_MISMATCH`
**And** 不产生部分写入的 retrieval-ready 状态

**Given** 开发者实现新的 vector store adapter
**When** 运行 contract tests
**Then** adapter 必须支持 `upsert`、`search`、`delete_by_document`
**And** 支持 metadata filter、tenant filter、ACL filter、soft delete、top_k、score threshold

### Story 2.9: 文档版本、软删除与索引状态闭环

**Requirements covered:** FR4, FR7, FR24

As a 知识库管理员,
I want 看到文档版本、索引状态和删除状态,
So that 知识库不会出现不可追踪的覆盖、误删或半索引状态。

**Acceptance Criteria:**

**Given** 同一文档再次上传
**When** 上传内容 checksum 或 source 版本变化
**Then** 系统创建新的 `version_id`
**And** 旧版本不被静默覆盖

**Given** ingestion、embedding 和 indexing 全部成功
**When** job 完成
**Then** document version 状态变为 `retrieval_ready`
**And** 可以查询 chunk 数量、embedding model、embedding_dim 和索引状态

**Given** 管理员删除文档或指定版本
**When** 调用删除服务
**Then** 文档默认软删除
**And** `VectorStore.delete_by_document(document_id, version_id)` 删除或标记指定版本索引，使其默认不可检索

## Epic 3: 授权 Hybrid Retrieval 与检索复盘

企业员工可以只在授权范围内检索知识，系统同时使用 dense retrieval 和 BM25 sparse retrieval，经 RRF merge、dedup、rerank、threshold filter 输出可追踪结果；平台工程师可以复盘每次检索质量。

### Story 3.1: Retrieval 请求模型与权限过滤构建

**Requirements covered:** FR8, FR9, FR22

As a 后端开发者,
I want retrieval service 接收标准请求和 AuthContext 派生过滤条件,
So that 所有召回路径从查询阶段就执行 tenant、RBAC 和 ACL 限制。

**Acceptance Criteria:**

**Given** 调用方提交 retrieval query
**When** application service 创建 `RetrievalRequest`
**Then** 请求包含 query、top_k、metadata_filter、score_threshold、request_id、trace_id
**And** `AuthContext` 不允许为空

**Given** 用户属于某 tenant 且只有部分文档 ACL
**When** policy builder 生成 filters
**Then** filters 包含 tenant filter、ACL filter、metadata filter
**And** filter 可同时传给 dense 和 sparse retriever

**Given** 测试尝试跨租户检索
**When** retrieval service 执行
**Then** 不返回其他 tenant 的 chunk
**And** 无权限 chunk 不进入 rerank、context packing 或 prompt

### Story 3.2: Dense Retrieval 召回

**Requirements covered:** FR8

As a 企业员工,
I want 系统通过语义召回找到授权知识片段,
So that 即使用词与原文不同也能找到相关内容。

**Acceptance Criteria:**

**Given** 用户提交自然语言 query
**When** dense retriever 执行
**Then** 通过 `EmbeddingProvider` 生成 query embedding
**And** 调用 `VectorStore.search` 时携带 tenant、ACL、metadata filter

**Given** vector store 返回候选结果
**When** dense retriever 归一化结果
**Then** 每条结果包含 `chunk_id`、`document_id`、`version_id`、`source`、`page_start`、`page_end`、`title_path`、`score`、`retrieval_method`、`tenant_id`、`acl`
**And** `retrieval_method` 包含 `dense`

**Given** embedding provider 超时
**When** dense retriever 捕获错误
**Then** 返回结构化 provider timeout 错误或按配置降级
**And** 记录 latency 和 error_code

### Story 3.3: BM25 Sparse Retrieval 召回

**Requirements covered:** FR9, FR22

As a 企业员工,
I want 系统能通过关键词、编号、条款和错误码召回文档,
So that 纯向量召回不漏掉精确匹配问题。

**Acceptance Criteria:**

**Given** chunk 已写入 sparse index 或 PostgreSQL full text 字段
**When** 用户查询制度编号、产品型号或错误码
**Then** sparse retriever 返回关键词相关候选
**And** 使用与 dense retriever 相同的 tenant 和 ACL filter

**Given** 中文或混合文本 query
**When** sparse retriever 解析 query
**Then** 不因空 token 或特殊符号导致 500 错误
**And** 对无法解析的 query 返回空候选或结构化错误

**Given** sparse retrieval 单测运行
**When** 测试关键词精确召回
**Then** 至少覆盖条款编号、错误码、人名或产品型号场景
**And** 未授权文档不得被召回

### Story 3.4: RRF Merge、去重与阈值过滤

**Requirements covered:** FR10

As a 企业员工,
I want dense 和 sparse 结果被稳定融合,
So that 系统能综合语义相似和关键词精确匹配的优势。

**Acceptance Criteria:**

**Given** dense 和 sparse 返回重叠 chunk
**When** RRF merge 执行
**Then** 相同 `chunk_id` 合并为一个候选
**And** 保留来源方法、原始排名、融合分数和融合原因

**Given** 候选分数低于配置阈值
**When** threshold filter 执行
**Then** 候选不得进入最终 retrieval result
**And** filter 结果记录到 retrieval trace

**Given** 相同输入候选列表
**When** 多次执行 RRF merge
**Then** 排序结果 deterministic
**And** 单测覆盖 tie-breaker 行为

### Story 3.5: Reranker 接口与降级策略

**Requirements covered:** FR11

As a 平台工程师,
I want rerank 能通过统一接口替换实现并可降级,
So that MVP 可以用 fake reranker 测试，后续接 cross-encoder 或 LLM rerank。

**Acceptance Criteria:**

**Given** merge 后候选列表
**When** 调用 `Reranker.rerank`
**Then** 返回带 rerank_score 和排序位置的候选列表
**And** 不改变 tenant、ACL 和 citation metadata

**Given** 测试环境运行 rerank 单测
**When** 使用 FakeReranker
**Then** 不调用外部模型
**And** rerank 前后分数、latency 被记录到 trace

**Given** reranker provider 失败
**When** 降级策略配置为 fallback
**Then** 系统使用 merge 排序继续返回结果并记录 `RERANK_DEGRADED`
**And** 降级不得引入未授权候选

### Story 3.6: `/retrieve` API 与检索复盘日志

**Requirements covered:** FR12, FR18

As a 平台工程师,
I want 每次检索都能通过日志复盘召回、融合、rerank 和过滤过程,
So that 质量问题可以定位到具体阶段。

**Acceptance Criteria:**

**Given** 授权用户调用 `POST /retrieve`
**When** retrieval service 完成
**Then** API 返回统一 envelope 和 retrieval results
**And** route 不直接调用 vector store、embedding provider 或 reranker

**Given** retrieval log 被保存
**When** 平台工程师按 `request_id` 查询
**Then** 能看到 query 摘要、dense top_k、sparse top_k、RRF 结果、rerank score、latency、tenant_id、user_id、error_code
**And** 日志不保存用户敏感原文或企业机密全文

**Given** retrieval_logs 表首次引入
**When** Alembic migration 生成
**Then** 表包含 id、created_at、updated_at、request_id、trace_id、tenant_id、user_id、status、latency、top_k、rerank_score、error_code 和安全 query 摘要
**And** 支持按 request_id、tenant_id、created_at 查询

**Given** retrieval 阶段发生 expected domain error
**When** API 返回错误
**Then** error envelope 包含稳定 code
**And** audit/log 中能关联 request_id 和 trace_id

### Story 3.7: Retrieval Eval Fixtures 与 Smoke Runner

**Requirements covered:** FR12, FR29, FR30

As a 平台工程师,
I want retrieval 阶段就有可执行 eval fixtures 和 smoke runner,
So that hybrid retrieval 质量不会等到 RAG 回答完成后才被验证。

**Acceptance Criteria:**

**Given** Epic 3 开始实现 retrieval
**When** 初始化 eval fixtures
**Then** 至少包含 20 条可执行 synthetic retrieval eval cases，而不是仅有 schema 或样例占位
**And** case schema 支持 query、tenant_id、user_id、permissions、expected_documents、expected_chunks、answerable、attack_type
**And** 初始集合至少包含制度、产品手册、FAQ、技术文档四类样例，并包含至少两个 ACL 隔离、两个 no-answer 和两个 prompt injection 回归场景

**Given** 执行 retrieval eval smoke runner
**When** 使用 fake provider、fake vector store 或本地 fixtures
**Then** 加载并执行全部 20 条初始 retrieval eval cases，输出 retrieval hit rate、ACL isolation result、top_k、request_id、trace_id 和 latency 摘要
**And** 不调用真实外部 LLM 或 embedding API

**Given** eval case 包含 ACL 隔离或 prompt injection 场景
**When** runner 运行
**Then** 未授权 chunk 不得计入命中
**And** 失败 report 标明失败阶段为 dense、sparse、merge、rerank、threshold 或 permission

## Epic 4: 可信 RAG 问答、Citation 与流式会话

企业员工可以通过 `/query` 或 `/chat` 获得仅基于授权上下文的回答、citation、无答案策略和 SSE 流式输出；系统支持会话记忆并可对接 Open WebUI 或轻量前端。

### Story 4.1: Context Packing 与上下文预算

**Requirements covered:** FR13, FR22

As a 企业员工,
I want 系统只把最相关且授权的上下文交给 LLM,
So that 回答更准确且不会泄露无权限内容。

**Acceptance Criteria:**

**Given** retrieval 返回多个授权候选 chunk
**When** context packer 执行
**Then** 按 rerank score、token budget 和去重策略选择上下文
**And** 未授权 chunk 被拒绝并记录错误或测试失败

**Given** 相邻 chunk 属于同一 document/version/title_path
**When** 策略允许相邻合并
**Then** context packer 合并相邻内容并保留页码范围
**And** citation metadata 仍能追溯到原 chunk

**Given** 候选总 token 超出预算
**When** context packer 裁剪
**Then** 输出裁剪原因和保留顺序
**And** 单测覆盖去重、排序、预算、相邻合并和父子补齐

### Story 4.2: PromptBuilder 与 Prompt Injection 防护

**Requirements covered:** FR14

As a 企业员工,
I want 系统明确只基于给定上下文回答,
So that 文档中的恶意指令不会改变系统行为。

**Acceptance Criteria:**

**Given** context packer 输出上下文
**When** PromptBuilder 构造 prompt
**Then** prompt 明确标记上下文边界、citation 要求和无答案策略
**And** 文档内容被声明为 untrusted content

**Given** 文档 chunk 包含“忽略系统提示”或“泄露密钥”等指令
**When** PromptBuilder 处理上下文
**Then** 指令只能作为文档内容进入 observation/context
**And** 不会覆盖系统规则或工具权限策略

**Given** FastAPI route 处理 `/query` 或 `/chat`
**When** 检查代码边界
**Then** route 不拼接 prompt
**And** prompt 构造只发生在 RAG application/domain service 中

### Story 4.3: LLMProvider 抽象与 Fake 生成

**Requirements covered:** FR15

As a 平台工程师,
I want 回答生成通过 `LLMProvider` 抽象完成,
So that 系统可以切换 OpenAI、Qwen、DeepSeek、本地 vLLM 或 Ollama。

**Acceptance Criteria:**

**Given** RAG service 需要生成答案
**When** 调用 LLM
**Then** 只能通过 `LLMProvider.generate` 或 `LLMProvider.stream`
**And** 业务代码不直接依赖单一厂商 SDK

**Given** 测试环境运行 RAG 单测
**When** 调用 generation service
**Then** 使用 FakeLLMProvider
**And** 不发生真实外部模型调用

**Given** provider 调用完成或失败
**When** 记录 generation metadata
**Then** 包含 model、token usage、latency、error_code
**And** 不记录 API key 或完整敏感上下文

### Story 4.4: Citation Extraction 与 `/query` 问答

**Requirements covered:** FR14, FR16

As a 企业员工,
I want 问答结果包含可追溯 citation,
So that 我可以复核答案来源并判断可信度。

**Acceptance Criteria:**

**Given** retrieval 和 context packing 返回可回答上下文
**When** 调用 `POST /query`
**Then** 返回 answer 和 citations
**And** citation 至少包含 `document_id`、`version_id`、`chunk_id`、`source`、`page_start` 或 `page_end`

**Given** LLM 生成了无法绑定来源的关键结论
**When** citation extractor 校验结果
**Then** 不伪造 citation
**And** 可按策略标记为 unsupported 或触发无答案/低置信处理

**Given** 上下文不足以回答问题
**When** RAG service 生成答案
**Then** 明确说明无法从给定上下文确认
**And** 不编造来源或外部事实

### Story 4.5: SSE Streaming 回答事件

**Requirements covered:** FR17

As a 前端调用方,
I want `/query` 或 `/chat` 可以流式返回 token、citation 和 final 事件,
So that 用户能更快看到回答并获得完整 metadata。

**Acceptance Criteria:**

**Given** 客户端请求流式回答
**When** LLMProvider.stream 产生 chunk
**Then** API 发送 `token` SSE event
**And** 每个事件 payload 包含 `request_id`

**Given** citation 已可用
**When** streaming pipeline 输出来源
**Then** 发送 `citation` event
**And** citation event 包含 document/version/chunk/source/page 信息

**Given** generation 发生 expected error
**When** stream 尚未结束
**Then** 发送结构化 `error` event
**And** 最终发送 `final` event 或明确终止状态

### Story 4.6: Chat Session Memory 与安全上下文

**Requirements covered:** FR19, FR22

As a 企业员工,
I want 多轮会话记住必要历史,
So that 我可以围绕同一授权知识主题连续追问。

**Acceptance Criteria:**

**Given** 用户创建 chat session
**When** 发送多轮消息
**Then** `chat_sessions` 和 `chat_messages` 保存 `tenant_id`、`user_id`、created_at、updated_at
**And** 不使用全局变量保存用户会话

**Given** chat memory 表首次引入
**When** Alembic migration 生成
**Then** `chat_sessions` 和 `chat_messages` 包含 id、created_at、updated_at、tenant_id、user_id、status 和必要 session/message metadata
**And** 支持按 tenant_id、user_id、session_id 查询

**Given** 会话历史进入 prompt 前
**When** RAG service 构造上下文
**Then** 历史消息经过 token budget 和安全过滤
**And** 不能绕过当前请求的 tenant、RBAC 或 ACL filter

**Given** 用户跨 session 或跨 tenant 请求会话
**When** memory service 查询历史
**Then** 返回权限错误或空结果
**And** 不暴露会话存在性细节给未授权用户

### Story 4.7: Open WebUI Chat Adapter、Source Detail 与轻量前端契约

**Requirements covered:** FR16, FR17, FR18, FR20

As a 企业员工,
I want 通过 Open WebUI 兼容 chat adapter 和轻量 sidecar 使用查询、citation、source drilldown 和 job 状态,
So that MVP 可以展示可信企业 RAG 闭环而不是只暴露裸 API。

**Acceptance Criteria:**

**Given** 前端或 Open WebUI adapter 调用后端
**When** 使用 `/chat`、`/query` 或兼容 adapter
**Then** 后端返回足够展示 answer、citation、request_id、session_id、final metadata 的结构
**And** 前端不得补造 citation 或判断权限

**Given** 用户点击 citation
**When** 前端调用 `POST /sources/resolve`
**Then** 后端重新校验 AuthContext、tenant、RBAC、ACL、soft delete 和 version visibility
**And** 只返回授权片段、document/version/chunk/page/source metadata、安全摘要和 request_id

**Given** 管理员查看文档处理状态
**When** 查询 job 或 document version
**Then** 能看到 uploaded/parsing/parsed/chunking/chunked/embedding/indexing/retrieval_ready/failed_retryable/failed_terminal/deleted 等状态
**And** 错误只显示安全摘要

**Given** 第一阶段自定义前端存在
**When** 验收 MVP
**Then** 只要求上传、查询、citation、job 状态和日志入口
**And** 不阻塞 ingestion、retrieval、citation、RBAC、eval 的主线开发

**Given** Source Inspector、Knowledge Admin、Diagnostics、Eval Reports 或 Agent Review 进入自定义 UI
**When** 验收 UI 行为
**Then** 满足 WCAG 2.2 AA、键盘聚焦、`aria-live`、alert region、drawer/sheet 焦点恢复、非纯颜色状态表达
**And** 长 document_id、version_id、chunk_id、request_id、trace_id 必须换行或截断并提供完整值读取方式

## Epic 5: RAG 质量评估与回归证据

平台工程师可以维护 eval dataset，验证 retrieval hit rate、citation coverage、no-answer correctness、ACL 隔离和 prompt injection 回归，避免 RAG 质量只能靠人工感觉判断。

### Story 5.1: 可执行 Eval Dataset 结构与初始用例

**Requirements covered:** FR29

As a 平台工程师,
I want 用结构化数据维护 RAG eval cases,
So that retrieval、citation、无答案和权限隔离能被稳定回归。

**Acceptance Criteria:**

**Given** eval dataset 目录为空
**When** 初始化 Phase 2 eval fixtures
**Then** 至少定义 20 条可执行 synthetic eval cases，而不是仅有 schema 或样例占位
**And** 每条 case 支持 query、tenant_id、user_id、permissions、expected_documents、expected_chunks、answerable、attack_type、expected_citations 或 no-answer expectation

**Given** eval case 覆盖业务场景
**When** 检查初始集合
**Then** 包含制度、产品手册、FAQ、技术文档样例类别
**And** 包含至少两个 ACL 隔离、两个 no-answer 和两个 prompt injection 回归场景

**Given** eval smoke gate 运行
**When** 使用默认 fake providers 或本地 fixtures
**Then** 所有 20 条初始 cases 都能被 runner 加载并执行
**And** 失败时 report 标明失败阶段，不能用 placeholder 通过验收

**Given** eval 数据中包含敏感样例
**When** 写入 repo
**Then** 只能保存脱敏内容或 synthetic fixtures
**And** 不包含真实 API key、access token 或企业机密全文

### Story 5.2: Retrieval 与 Citation Eval Runner

**Requirements covered:** FR12, FR16, FR29

As a 平台工程师,
I want 自动运行 eval 并输出核心质量指标,
So that 检索质量和 citation 质量可以量化。

**Acceptance Criteria:**

**Given** eval dataset 已存在
**When** 运行 eval runner
**Then** 输出 retrieval hit rate、citation coverage、no-answer correctness、ACL isolation result
**And** 每个 case 记录 request_id、trace_id、top_k、rerank score、latency 摘要

**Given** eval runner 使用测试 providers
**When** 执行默认测试
**Then** 不调用真实外部 LLM API
**And** 可使用 fake retriever、fake reranker 或本地 fixtures

**Given** 某 case 失败
**When** 生成 report
**Then** report 标识失败阶段为 retrieval、rerank、context packing、generation、citation 或 permission
**And** 不把完整企业机密上下文写入报告

### Story 5.3: Eval 回归与 CI Smoke Gate

**Requirements covered:** FR29, FR30

As a 项目维护者,
I want 在 CI 或本地命令中运行轻量 RAG eval smoke test,
So that 核心 RAG 质量不会被无意破坏。

**Acceptance Criteria:**

**Given** 开发者修改 retrieval、rag 或 auth 模块
**When** 执行 eval smoke 命令
**Then** 至少运行一组快速 synthetic eval cases
**And** 失败时返回非零退出码

**Given** CI 执行质量门
**When** 运行 lint、unit、integration mock、eval smoke
**Then** 报告产物写入 `tests/eval/reports` 或配置的输出目录
**And** 报告包含 commit/time/config 摘要

**Given** eval 阈值尚处于 MVP 校准阶段
**When** 指标低于配置阈值
**Then** 输出明确告警和失败 case
**And** 阈值可配置，不硬编码在业务代码中

## Epic 6: 受控 Agent 工具执行

交付顾问或授权用户可以运行受控 Agent，通过 Tool Registry 调用 `rag_search`、`calculator`、`file_reader`，并受 schema、permission、timeout、rate limit、max_steps、max_tool_calls 和 audit log 约束。

### Story 6.1: Tool Registry 与工具治理模型

**Requirements covered:** FR25

As a 平台工程师,
I want 所有 Agent 工具通过 Tool Registry 注册和校验,
So that LLM 不能绕过后端策略直接调用任意 Python 函数。

**Acceptance Criteria:**

**Given** 开发者定义工具
**When** 注册到 Tool Registry
**Then** tool definition 必须包含 name、description、input_schema、output_schema、permission、timeout、rate_limit、handler
**And** schema 使用 Pydantic v2 或等价结构化 schema

**Given** Agent 请求调用未注册工具
**When** runtime 查询 registry
**Then** 调用被拒绝并返回 `TOOL_NOT_REGISTERED`
**And** 记录 audit 事件

**Given** 工具入参不符合 input_schema
**When** Tool Registry 校验请求
**Then** 调用被拒绝并返回结构化 validation error
**And** handler 不会执行

### Story 6.2: `rag_search` 工具

**Requirements covered:** FR22, FR26

As a 交付顾问,
I want Agent 能通过受控 `rag_search` 工具查询授权知识库,
So that Agent 的知识检索复用已有 retrieval 权限和 citation 能力。

**Acceptance Criteria:**

**Given** Agent Runtime 调用 `rag_search`
**When** tool handler 执行
**Then** 必须复用 retrieval service 和 AuthContext filter
**And** 无权限 chunk 不得返回给 Agent observation

**Given** `rag_search` 返回结果
**When** Agent 读取 observation
**Then** observation 包含 chunk 摘要、document_id、version_id、chunk_id、source、page、score
**And** 不包含超出权限的文档全文

**Given** retrieval service 返回错误
**When** `rag_search` tool 捕获错误
**Then** tool result 使用结构化错误输出
**And** tool audit 记录 status、error_code、latency

### Story 6.3: `calculator` 与受限 `file_reader` 工具

**Requirements covered:** FR26

As a 授权用户,
I want Agent 可以执行安全计算并读取 allowlist 文件,
So that 常见辅助任务可自动化但不会越权访问本地文件。

**Acceptance Criteria:**

**Given** Agent 调用 `calculator`
**When** 输入为受支持表达式或结构化计算请求
**Then** 返回确定性计算结果
**And** 不访问网络、文件系统或外部 provider

**Given** Agent 调用 `file_reader`
**When** 请求路径不在 allowlist 内
**Then** 调用被拒绝并返回 `FILE_ACCESS_DENIED`
**And** 不泄露真实绝对路径或目录结构

**Given** 请求路径在 allowlist 内
**When** `file_reader` 读取文件
**Then** 返回大小受限的内容摘要或内容片段
**And** 记录 tenant_id、user_id、tool_name、参数摘要、latency、status

### Story 6.4: ReAct Agent Runtime 限制与重复动作检测

**Requirements covered:** FR27

As a 平台负责人,
I want Agent Runtime 有明确步数、工具次数、timeout 和重复动作限制,
So that Agent 不会无限循环、越权或失控消耗资源。

**Acceptance Criteria:**

**Given** 用户发起 Agent run
**When** runtime 初始化
**Then** run 配置包含 max_steps、max_tool_calls、timeout
**And** 默认值来自配置，不硬编码在 prompt 中

**Given** Agent 达到 max_steps 或 max_tool_calls
**When** runtime 准备下一步
**Then** 必须停止执行并返回结构化终止状态
**And** 不继续调用 LLM 或工具

**Given** Agent 重复执行相同工具和相同参数
**When** repeated action detector 命中阈值
**Then** runtime 停止或要求模型换策略
**And** audit log 记录 repeated_action_detected

### Story 6.5: `/agent/run` API 与 Agent Run Persistence

**Requirements covered:** FR18, FR23, FR27

As a 管理员,
I want Agent run 的 API、状态和持久化先独立落地,
So that 后续 tool audit 和 final answer validation 可以基于可追踪 run 执行。

**Acceptance Criteria:**

**Given** 授权用户调用 `POST /agent/run`
**When** API 创建 Agent run
**Then** 返回 `agent_run_id`、request_id 和执行状态
**And** route 不直接调用任意工具 handler

**Given** agent_runs 表首次引入
**When** Alembic migration 生成
**Then** 表包含 id、created_at、updated_at、tenant_id、user_id、created_by、status、request_id、trace_id、max_steps、max_tool_calls、timeout、error_code
**And** 支持按 tenant_id、user_id、agent_run_id、request_id 查询

**Given** Agent run 达到 max_steps、max_tool_calls 或 timeout
**When** runtime 停止
**Then** agent_run 状态持久化为明确终止状态
**And** 审计日志可关联 request_id、trace_id、tenant_id、user_id

### Story 6.6: Tool Call Audit Persistence

**Requirements covered:** FR23, FR28

As a 管理员,
I want 每次工具调用都被独立审计和脱敏持久化,
So that Agent 行为可以复盘但不会泄露敏感内容。

**Acceptance Criteria:**

**Given** Agent 调用任意工具
**When** tool call 完成
**Then** `tool_calls` 记录 agent_run_id、tool_name、参数摘要、结果摘要、latency、status、error_code、tenant_id、user_id
**And** 参数摘要和结果摘要不包含密钥或企业机密全文

**Given** tool_calls 表首次引入
**When** Alembic migration 生成
**Then** 表包含 id、created_at、updated_at、tenant_id、user_id、agent_run_id、tool_name、permission、status、latency、error_code、request_id、trace_id、arguments_summary、result_summary
**And** 支持按 agent_run_id、tool_name、status 和 created_at 查询

**Given** 工具调用被拒绝、超时或 schema validation 失败
**When** audit 记录写入
**Then** status 和 error_code 可区分 permission、timeout、rate_limit、validation 和 handler error
**And** handler 未执行的情况也有审计事件

### Story 6.7: Agent Final Answer Validation

**Requirements covered:** FR16, FR22, FR27, FR28

As a 授权用户,
I want Agent 最终回答在返回前经过权限、citation 和工具错误校验,
So that Agent 不会输出未授权来源、伪造引用或忽略失败工具结果。

**Acceptance Criteria:**

**Given** Agent 生成最终回答
**When** final answer validation 执行
**Then** 检查是否包含未授权来源、伪造 citation 或工具错误未处理
**And** 失败时返回结构化错误或安全降级回答

**Given** 最终回答引用了 `rag_search` 结果
**When** validator 校验 citation
**Then** citation 必须来自本次 run 的授权 tool observation 或 RAG result
**And** 不允许 LLM 自行编造 document_id、version_id、chunk_id 或 page

**Given** tool call 中存在失败、超时或权限拒绝
**When** final answer 试图使用该结果
**Then** validator 标记 unsupported 或返回安全错误
**And** audit log 记录 final_answer_validation status、latency 和 error_code
