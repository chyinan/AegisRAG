---
stepsCompleted:
  - step-01-document-discovery
  - step-02-prd-analysis
  - step-03-epic-coverage-validation
  - step-04-ux-alignment
  - step-05-epic-quality-review
  - step-06-final-assessment
includedFiles:
  - type: PRD
    path: _bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md
  - type: Architecture
    path: _bmad-output/planning-artifacts/architecture.md
  - type: Epics
    path: _bmad-output/planning-artifacts/epics.md
  - type: UX Experience
    path: _bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md
  - type: UX Design
    path: _bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md
---

# Implementation Readiness Assessment Report

**Date:** 2026-05-27
**Project:** 本地化多源知识增强 RAG + Agent 问答系统

## Step 1: Document Discovery

### Documents Selected for Assessment

| Type | File | Size | Modified |
| --- | --- | ---: | --- |
| PRD | `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md` | 36,194 bytes | 2026-05-27 00:58:23 |
| Architecture | `_bmad-output/planning-artifacts/architecture.md` | 38,324 bytes | 2026-05-27 00:58:40 |
| Epics / Stories | `_bmad-output/planning-artifacts/epics.md` | 58,748 bytes | 2026-05-27 01:07:36 |
| UX Experience | `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md` | 15,106 bytes | 2026-05-27 01:07:59 |
| UX Design | `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md` | 8,578 bytes | 2026-05-26 23:30:35 |

### Discovery Inventory

#### PRD Files Found

**Whole Documents:**
- None in `_bmad-output/planning-artifacts`.

**Sharded Documents:**
- Folder: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/`
  - `PRD.md`
  - `.decision-log.md`

#### Architecture Files Found

**Whole Documents:**
- `_bmad-output/planning-artifacts/architecture.md`

**Sharded Documents:**
- None.

#### Epics & Stories Files Found

**Whole Documents:**
- `_bmad-output/planning-artifacts/epics.md`

**Sharded Documents:**
- None.

#### UX Design Files Found

**Whole Documents:**
- None in `_bmad-output/planning-artifacts`.

**Sharded Documents:**
- Folder: `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/`
  - `EXPERIENCE.md`
  - `DESIGN.md`
  - `.decision-log.md`

### Discovery Notes

- No blocking duplicate whole/sharded document conflicts were found inside `_bmad-output/planning-artifacts`.
- Root-level `PRD.md` was discovered outside the configured planning artifact directory and is not selected for this assessment.
- Existing same-date readiness report was reset for this fresh assessment run because selected artifacts have newer modified timestamps than the previous report inventory.

## Step 2: PRD Analysis

### Functional Requirements

FR-1: 文档上传与接入。授权用户可以上传 PDF、DOCX、TXT、Markdown 文件，并可为文档设置 `tenant_id`、source metadata 和 `acl`。上传请求返回 `document_id`、`version_id`、`job_id` 和初始状态，不等待 embedding 完成；未授权上传返回结构化权限错误；文档 metadata 必须包含 `tenant_id`、`created_by`、`status`、`source_type`、`source_uri` 和 `checksum`。

FR-2: Parser 标准化输出。系统支持 PDF、DOCX、TXT、Markdown parser，并输出统一的 `ParsedDocument` 和 `Section`。每种 parser 至少有正常文件和异常文件测试；Markdown parser 保留标题层级；PDF parser 尽量保留页码；parser 错误必须转化为领域异常并写入 job 状态。

FR-3: 可插拔 Chunker。系统提供 `Chunker` 协议，至少支持 FixedSizeChunker，并预留 SemanticChunker 和 HierarchicalChunker。FixedSizeChunker 支持默认 500 到 800 token chunk 和 10% 到 20% overlap；chunk metadata 必须包含 `document_id`、`version_id`、`chunk_id`、`tenant_id`、`source_type`、`source_uri`、`title_path`、`page_start`、`page_end`、`token_count`、`acl`、`checksum`；chunker 单测覆盖 overlap、标题路径、页码和 token_count。

FR-4: 文档版本和软删除。系统必须记录文档版本，删除默认软删除，支持按版本重建索引。同一文档再次上传产生新的 `version_id`，旧 chunk 不被静默覆盖；删除文档后检索默认排除软删除文档；`delete_by_document(document_id, version_id)` 可以删除或标记指定版本索引。

FR-5: Embedding Provider 抽象。系统提供 `EmbeddingProvider` 协议，支持 batch embedding、timeout、retry、rate limit 和 fake provider。单元测试默认使用 FakeEmbeddingProvider，不真实调用外部 LLM API；provider 请求必须有 timeout 配置；provider 错误必须可重试或进入明确失败状态。

FR-6: Embedding 元数据记录。每个 embedding job 和向量记录必须记录 `embedding_provider`、`embedding_model`、`embedding_version` 和 `embedding_dim`。embedding_dim 与目标索引维度不一致时拒绝写入；embedding_model 变化时必须触发新索引或重建流程，不能静默复用旧索引。

FR-7: Vector Store 统一接口。系统提供 `VectorStore` 协议，支持 upsert、search、delete_by_document、metadata filter、tenant filter、ACL filter、soft delete、top_k 和 score threshold。pgvector adapter 是默认实现；FAISS adapter 可作为本地轻量方案；Milvus adapter 不属于 MVP 必交付，但接口边界不得阻塞后续接入。

FR-8: Dense Retrieval。系统可以通过 `EmbeddingProvider` 和 `VectorStore` 执行语义召回，并支持 `tenant_id`、metadata 和 `acl` 过滤。dense search 请求必须包含 AuthContext 派生的 tenant 和 ACL filter；检索结果必须包含 `chunk_id`、`document_id`、`version_id`、`source`、页码、`title_path`、score、retrieval_method、`tenant_id` 和 `acl`。

FR-9: BM25 Sparse Retrieval。系统支持 BM25 或 PostgreSQL full text / OpenSearch sparse retrieval，用于条款、编号、错误码、人名、产品型号等关键词场景。sparse retrieval 单测覆盖关键词精确召回；sparse retrieval 与 dense retrieval 使用相同的 tenant 和 ACL filter；MVP 不允许只依赖纯向量检索。

FR-10: Hybrid Merge。系统支持 RRF 或加权融合，将 dense 和 sparse 结果合并并去重。同一 `chunk_id` 出现在多个召回源时合并为一个候选项，并保留 retrieval_method 列表或融合原因；RRF merge 有确定性排序测试；merge 结果低于 score threshold 时不得进入上下文。

FR-11: Reranker 接口。系统提供 `Reranker` 协议，支持 fake reranker 和后续 cross-encoder / LLM rerank adapter。rerank 前后记录分数和 latency；reranker 失败时必须有明确降级策略或结构化错误；单元测试不真实调用外部模型。

FR-12: Retrieval Log。每次 retrieval 必须记录可复盘日志。日志字段至少包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、query 摘要、dense top_k、sparse top_k、RRF 结果、rerank score、latency、error_code；日志禁止记录 API key、access token、企业机密全文和用户敏感原文。

FR-13: Context Packing。系统支持 token budget、chunk 去重、按 rerank 分数排序、相邻 chunk 合并和父子上下文补齐。context packer 不接收未授权 chunk；候选 chunk 超过 token budget 时按策略裁剪并保留裁剪原因；单测覆盖去重、排序、预算和相邻合并。

FR-14: Prompt Builder。系统提供 PromptBuilder，明确上下文边界、citation 要求、无答案策略和 prompt injection 防护。prompt 明确要求仅基于给定上下文回答；文档内容中“忽略系统提示”等指令必须被视为不可信内容；route 中不得拼接 prompt。

FR-15: LLM Provider 抽象。系统提供 `LLMProvider` 协议，支持 generate 和 stream，并可接 OpenAI、Qwen、DeepSeek、本地 vLLM、Ollama。业务代码不得直接依赖单一厂商 SDK；单元测试使用 FakeLLMProvider；每次调用记录 model、token usage、latency 和 error_code。

FR-16: Citation Answer。系统最终问答结果必须包含 answer 和 citations。citation 至少包含 `document_id`、`chunk_id`、`source`、`page` 或页码范围；关键结论尽量绑定 citation；无法绑定来源的结论不得伪造 citation；citation extractor 单测覆盖多来源、页码缺失和无答案场景。

FR-17: SSE Streaming。系统支持流式回答，优先使用 SSE。SSE 事件类型至少包括 `token`、`citation`、`tool_call`、`tool_result`、`error`、`final`；流式错误必须以结构化 error 事件返回；final 事件包含完整 answer metadata。

FR-18: 核心 API。系统提供 `POST /upload`、`POST /retrieve`、`POST /query`、`POST /chat`、`POST /sources/resolve`、`POST /agent/run`。所有 API 请求支持 `request_id`、`user_id`、`tenant_id`，`session_id` 可选；所有 API 返回统一 data/error/metadata 结构；route 层不得直接调用 LLM、向量数据库或复杂业务逻辑；`POST /sources/resolve` 必须重新校验 AuthContext、tenant、RBAC 和 ACL，只返回授权片段、document/version/chunk/page/source metadata 和安全摘要；无权限或不存在时不得泄露资源存在性。

FR-19: 多轮会话记忆。系统支持 chat session 和 chat message 持久化，禁止用全局变量保存用户会话。`chat_sessions` 和 `chat_messages` 记录 `tenant_id`、`user_id`、created_at、updated_at；会话上下文不得绕过权限过滤；会话历史进入 prompt 前必须经过 token budget 和安全过滤。

FR-20: 前端集成路径。MVP 首选通过 Open WebUI 兼容 chat adapter 接入，由后端 `/chat` 承载 RAG、citation、SSE 和权限治理；最小自定义 sidecar 仅用于 Source Inspector、上传/job 状态或日志入口，不让复杂前端挤占 RAG 核心开发优先级。API 文档足以让 Open WebUI 或轻量前端调用；自定义前端第一阶段只展示上传、查询、citation、job 状态和日志入口；若 Source Inspector、Knowledge Admin、Diagnostics 进入 MVP，必须满足 UX 文档中的可访问性和长 ID 展示要求。

FR-21: AuthContext。系统定义统一 AuthContext，包含 `user_id`、`tenant_id`、`roles`、`department`、`permissions`。application service 必须显式接收 AuthContext 或 RequestContext；缺少 tenant 或 user 的业务请求被拒绝；测试覆盖跨租户访问拒绝。

FR-22: RBAC 与 ACL 检索过滤。系统在 retrieval 阶段执行 tenant、RBAC 和 ACL filter，禁止先检索全量再在答案中过滤。同一 query 在不同 tenant 下不能返回对方文档；无权限文档 chunk 不进入 rerank、context packing 或 prompt；permission leakage rate 必须为 0。

FR-23: Audit Log。系统记录关键业务行为审计，包括上传、删除、检索、问答、Agent run 和 tool call。审计日志包含 request_id、trace_id、tenant_id、user_id、action、resource、latency、status、error_code；审计日志不记录企业机密全文和敏感 token。

FR-24: 数据保留和软删除策略。系统支持文档软删除、版本追踪、日志保留策略和配置化清理。软删除文档默认不可检索；日志保留周期可配置。

FR-25: Tool Registry。系统提供 Tool Registry，工具定义必须包含 name、description、input_schema、output_schema、permission、timeout、rate_limit、handler。未注册工具不可调用；工具入参必须按 schema 校验；工具执行前必须校验 permission。

FR-26: 必备工具。系统支持 `rag_search`、`calculator`、`file_reader`，`web_search` 可后续扩展。`rag_search` 复用 retrieval 权限过滤；`calculator` 不访问外部资源；`file_reader` 只能读取 allowlist 范围，不能读取任意路径。

FR-27: Agent Runtime。系统支持 ReAct 起步，并预留 Planner-Executor 和 LangGraph 风格状态图。Agent Runtime 必须有 max_steps、max_tool_calls、timeout 和 repeated action detection；到达限制后 Agent 必须停止并返回结构化状态；Agent 不能让 LLM 决定用户是否有权限。

FR-28: Tool Call Audit。每次工具调用必须记录审计日志。`tool_calls` 记录 agent_run_id、tool_name、参数摘要、结果摘要、latency、status、error_code、tenant_id、user_id；参数摘要不得包含敏感全文或密钥。

FR-29: RAG Eval Dataset。系统支持维护可执行 eval dataset，用于 retrieval、citation、no-answer、ACL 隔离和 prompt injection 回归。Phase 2 至少包含 20 条可执行 synthetic eval query，覆盖 expected_documents、expected_chunks、answerable、ACL 和 attack_type；占位样例不能满足 smoke gate；eval 结果输出 retrieval hit rate、citation coverage、no-answer correctness 和 ACL 隔离结果。

FR-30: Structured Logging。系统使用结构化日志记录 request、retrieval、generation、tool 和 error。日志字段至少包含 request_id、trace_id、tenant_id、user_id、latency、model、token usage、retrieval top_k、rerank score、tool calls、error_code；禁止使用 `print` 代替日志。

FR-31: Metrics 与 Dashboard 预留。系统预留 Prometheus、OpenTelemetry 和 Grafana 接入。MVP 至少暴露 health/readiness 和关键 latency 指标；worker queue backlog 可观测。

FR-32: Docker Compose 本地部署。系统支持 Docker Compose 启动核心服务。Compose 至少包含 api、worker-ingestion、worker-embedding、postgres、redis、minio；opensearch、milvus、prometheus、grafana 可选；启动流程包含 migration 和 health check。

Total FRs: 32

### Non-Functional Requirements

NFR-1: 安全输入边界。用户输入、文档内容、Web 内容和 Tool output 都是不可信输入，文档或工具输出不得提升为系统指令。

NFR-2: 后端权限策略。权限逻辑必须在后端策略执行，不得放在 prompt 中；检索和工具执行必须使用 `tenant_id`、RBAC 和 ACL 过滤。

NFR-3: Prompt Injection 防护。防护范围必须覆盖文档诱导忽略系统提示、泄露密钥、越权读取文件、Web 页面诱导危险工具等场景。

NFR-4: Provider 安全配置。外部 provider 调用必须配置 timeout，不得硬编码 API key。

NFR-5: 敏感日志控制。日志不得记录 API key、access token、企业机密全文或用户敏感原文。

NFR-6: 上传性能。`/upload` 返回 job id 的 API 路径不得等待大批量 embedding 完成。

NFR-7: 流式性能。`/query` 和 `/chat` 支持 SSE，优先优化 first-token latency。

NFR-8: 分阶段性能观测。retrieval 记录 dense、sparse、merge、rerank 和 context packing 分阶段耗时。

NFR-9: SLA 策略。MVP 性能目标以可观测和可调优为主；具体 p95 SLA 需在真实数据规模确认后设定。

NFR-10: Worker 可靠性。worker 任务支持失败状态、重试和错误原因记录。

NFR-11: 索引状态可靠性。文档索引状态必须可追踪，不能出现用户以为已可检索但索引实际未完成的静默状态。

NFR-12: 删除可靠性。数据删除默认软删除，避免误删后不可恢复。

NFR-13: 可测试性。核心模块必须可单元测试，测试默认禁止真实调用外部 LLM API。

NFR-14: Fake / Mock 支撑。Provider、VectorStore、Reranker、Tool Registry 和 Agent Runtime 必须有 fake 或 mock 测试实现。

NFR-15: 存储扩展性。默认实现优先 PostgreSQL + pgvector，但 VectorStore 接口必须允许 FAISS 和 Milvus 接入。

NFR-16: 模型扩展性。LLM 和 Embedding Provider 必须可配置切换。

NFR-17: Agent 扩展性。Agent 工作流先支持 ReAct，后续可扩展 Planner-Executor 和 LangGraph 风格状态图。

Total NFRs: 17

### Additional Requirements

- MVP 范围包括 FastAPI 分层骨架、`AuthContext`、`RequestContext`、结构化错误、配置加载、PostgreSQL + pgvector、Redis worker、MinIO 或本地对象存储接口、四类 parser、FixedSizeChunker、Provider 抽象、Hybrid Retrieval、Context Packing、Prompt Builder、Citation Extraction、核心 API、SSE、retrieval logs、structured logs、基础 eval dataset 和 Docker Compose。
- MVP 明确排除 Milvus 生产级部署、Graph RAG、多 Agent 协作、复杂 Web crawler、完整 Observability Dashboard、自研复杂前端、Agent 敏感写操作和真实企业 SSO 深度集成；MVP auth 已决策为开发/测试模拟 AuthContext + 轻量 JWT adapter。
- 成功指标包括 permission leakage rate = 0、answerable eval citation coverage >= 90%、initial eval retrieval hit rate >= 80%、unanswerable eval no-answer correctness >= 85%、ingestion traceability = 100%、tool audit coverage = 100%、expected domain errors structured error coverage = 100%。
- 决策日志确认：产品定位是企业私有知识库 RAG + 受控 Agent，不是通用聊天 demo；MVP 以可信 RAG 闭环为核心；Agent 后置于 Tool Registry；默认向量存储 PostgreSQL + pgvector；Sparse / BM25 是 MVP 必需；Open WebUI 或轻量前端作为早期集成目标。
- PRD 保留开放问题：Sparse retrieval 首选 PostgreSQL full text 还是 OpenSearch、队列框架 Celery 还是 RQ、初始 eval dataset 业务样例、`file_reader` allowlist 管理方式、Phase 1 parser 实现顺序。

### PRD Completeness Assessment

- PRD 的功能需求编号稳定，覆盖 ingestion、embedding、vector store、hybrid retrieval、RAG generation、API、会话、权限、审计、Agent、eval、可观测和部署，适合用于 epic traceability。
- PRD 已明确生产级边界和反 demo 约束，尤其是 Provider 抽象、retrieval 阶段权限过滤、citation、eval 和 audit log。
- PRD 中仍有若干 `[ASSUMPTION]` 和 5 个 Remaining Open Questions；这些不阻塞初始实现规划，但会影响队列、sparse backend、eval 数据集和 parser 顺序的实现确定性。

## Step 3: Epic Coverage Validation

### Epic FR Coverage Extracted

`epics.md` declares `validation.frCoverage: complete`, `epicCount: 6`, `storyCount: 39`, and contains an explicit `FR Coverage Map`. FR identifiers were normalized so `FR1` and `FR-1` are treated as the same requirement.

| FR Number | PRD Requirement | Epic Coverage | Status |
| --- | --- | --- | --- |
| FR-1 | 文档上传与接入 | Epic 2 | Covered |
| FR-2 | Parser 标准化输出 | Epic 2 | Covered |
| FR-3 | 可插拔 Chunker | Epic 2 | Covered |
| FR-4 | 文档版本和软删除 | Epic 2 | Covered |
| FR-5 | Embedding Provider 抽象 | Epic 2 | Covered |
| FR-6 | Embedding 元数据记录 | Epic 2 | Covered |
| FR-7 | Vector Store 统一接口 | Epic 2 | Covered |
| FR-8 | Dense Retrieval | Epic 3 | Covered |
| FR-9 | BM25 Sparse Retrieval | Epic 3 | Covered |
| FR-10 | Hybrid Merge | Epic 3 | Covered |
| FR-11 | Reranker 接口 | Epic 3 | Covered |
| FR-12 | Retrieval Log | Epic 3 | Covered |
| FR-13 | Context Packing | Epic 4 | Covered |
| FR-14 | Prompt Builder | Epic 4 | Covered |
| FR-15 | LLM Provider 抽象 | Epic 4 | Covered |
| FR-16 | Citation Answer | Epic 4 and Epic 6 | Covered |
| FR-17 | SSE Streaming | Epic 4 | Covered |
| FR-18 | 核心 API | Epic 1, Epic 4, and Epic 6 | Covered |
| FR-19 | 多轮会话记忆 | Epic 4 | Covered |
| FR-20 | 前端集成路径 | Epic 4 | Covered |
| FR-21 | AuthContext | Epic 1 | Covered |
| FR-22 | RBAC 与 ACL 检索过滤 | Epic 3 and Epic 6 | Covered |
| FR-23 | Audit Log | Epic 1 and Epic 6 | Covered |
| FR-24 | 数据保留和软删除策略 | Epic 2 | Covered |
| FR-25 | Tool Registry | Epic 6 | Covered |
| FR-26 | 必备工具 | Epic 6 | Covered |
| FR-27 | Agent Runtime | Epic 6 | Covered |
| FR-28 | Tool Call Audit | Epic 6 | Covered |
| FR-29 | RAG Eval Dataset | Epic 3 and Epic 5 | Covered |
| FR-30 | Structured Logging | Epic 1 | Covered |
| FR-31 | Metrics 与 Dashboard 预留 | Epic 1 | Covered |
| FR-32 | Docker Compose 本地部署 | Epic 1 | Covered |

### Missing Requirements

No missing PRD FR coverage was found in `epics.md`.

### Extra FR References

No FR numbers appear in `epics.md` that are outside the PRD range `FR-1` through `FR-32`.

### Coverage Statistics

- Total PRD FRs: 32
- FRs covered in epics: 32
- Coverage percentage: 100%
- Blocking FR coverage gaps: 0

## Step 4: UX Alignment Assessment

### UX Document Status

Found. UX artifacts included in this assessment:

- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/.decision-log.md`

### UX to PRD Alignment

- Aligned: UX positions Open WebUI as the first-stage shell and explicitly states that backend services own AuthContext, ingestion, hybrid retrieval, citation, SSE, audit, eval, Tool Registry and Agent runtime. This matches PRD FR-18, FR-20, FR-21, FR-22 and FR-25 through FR-28.
- Aligned: UX makes citation a primary interaction through citation chips and Source Inspector. This matches PRD FR-16 and FR-18, including the `POST /sources/resolve` reauthorization requirement.
- Aligned: UX requires upload/job status visibility with `uploaded -> parsing -> parsed -> chunking -> chunked -> embedding -> indexing -> retrieval_ready` and retry/failure states. This matches PRD FR-1, FR-2, FR-5, FR-6 and FR-24.
- Aligned: UX separates employee-facing source clarity from admin-facing retrieval diagnostics. This matches PRD UJ-3, FR-12, FR-29 and FR-30.
- Aligned: UX phase-gates Agent UI behind Tool Registry, max limits and audit. This matches PRD FR-25 through FR-28.
- Aligned: UX accessibility floor includes WCAG 2.2 AA, keyboard focus, `aria-live`, alert regions, drawer/sheet focus recovery, non-color-only state and long identifier wrapping/truncation. PRD FR-20 explicitly imports these requirements when custom UI surfaces enter MVP.

### UX to Architecture Alignment

- Supported: Architecture fixes Open WebUI as chat-first entry and states it is not the governance boundary. This directly supports the UX decision log.
- Supported: Architecture defines `POST /sources/resolve` as the canonical Source Inspector endpoint with tenant, RBAC, ACL, soft-delete and version visibility rechecks.
- Supported: Architecture defines REST + SSE events (`token`, `citation`, `tool_call`, `tool_result`, `error`, `final`) and requires event payloads to include `request_id`.
- Supported: Architecture includes frontend contracts for scope, citations, job status, request_id and retrieval trace metadata; ordinary users see scope/source/no-answer, administrators can see dense/sparse/RRF/rerank/context packing traces.
- Supported: Architecture includes an Accessibility Contract matching UX requirements for custom Source Inspector, Knowledge Admin, Diagnostics, Eval Reports and Agent Review surfaces.
- Supported: Epics preserve the UX surface requirements in Story 4.7 and Agent review requirements in Epic 6.

### Alignment Issues

No blocking UX alignment issue was found.

### Warnings

- The exact MVP frontend packaging remains open: UX asks whether upload/job/log/eval should be handled by a minimal custom admin panel or entirely through Open WebUI plus backend APIs. Architecture allows a sidecar, but implementation planning should decide this before starting frontend work.
- UX notes that citation clicks may need original document page preview later. Architecture and PRD currently make `POST /sources/resolve` sufficient for MVP, so original page viewer should remain post-MVP unless explicitly reprioritized.
- Remediated after the original assessment: MVP auth is now fixed as development/test simulated AuthContext plus lightweight JWT adapter, both producing the same backend `AuthContext` DTO for scope and permission states.

## Step 5: Epic Quality Review

### Review Scope

Reviewed all 6 epics and 39 stories in `_bmad-output/planning-artifacts/epics.md` against create-epics-and-stories standards:

- Epic user value and independence
- Story sizing and independent completion
- Forward dependency risk
- Database/entity creation timing
- Given/When/Then acceptance criteria quality
- Starter-template handling for greenfield implementation
- FR traceability preservation

### Epic Structure Validation

| Epic | User Value Focus | Independence | Quality Assessment |
| --- | --- | --- | --- |
| Epic 1: 可运行且可治理的平台基础 | Platform/operator value, infrastructure-heavy but valid for greenfield foundation | Stands alone as runnable platform base | Acceptable exception. It is technical-heavy, but greenfield projects require setup, environment, health/readiness, context and logging foundations before user workflows can run. |
| Epic 2: 知识文档接入到可检索资产 | Clear knowledge-admin value | Depends only on Epic 1 foundations | Good. Delivers upload, parse, chunk, embedding, vector write, versioning and soft delete without requiring future RAG answer features. |
| Epic 3: 授权 Hybrid Retrieval 与检索复盘 | Clear employee/admin value | Depends on Epic 1 and 2 outputs | Good, with one major eval-gate concern noted below. Retrieval can produce authorized results and logs without RAG generation. |
| Epic 4: 可信 RAG 问答、Citation 与流式会话 | Clear employee/front-end value | Depends on prior retrieval and document assets | Good. No forward dependency on Agent. |
| Epic 5: RAG 质量评估与回归证据 | Clear platform-engineer value | Depends on retrieval/RAG surfaces, acceptable sequence | Good as a quality epic, but should not be the first place where the PRD's 20-case eval gate becomes mandatory. |
| Epic 6: 受控 Agent 工具执行 | Clear authorized-user/admin value | Correctly follows Tool Registry and RAG foundations | Good. Agent is phase-gated behind registry, permissions, limits and audit. |

### Story Quality Summary

- All 39 stories have explicit `As a / I want / So that` structure.
- All 39 stories have Given/When/Then acceptance criteria.
- Story sizing is generally implementable: most stories cover one coherent module or API contract, with 3 to 5 acceptance scenarios.
- Database creation timing is mostly correct: foundational identity/audit tables appear in Story 1.5; documents in 2.1; chunks in 2.6; embedding_jobs in 2.7; retrieval_logs in 3.6; chat memory in 4.6; agent_runs in 6.5; tool_calls in 6.6. No story creates all tables up front.
- FR traceability is preserved at story level, not only epic level.

### Critical Violations

None found.

No epic requires a future epic to be usable, no story explicitly depends on a later story to pass its own acceptance criteria, and no epic-sized story was identified as impossible to complete.

### Major Issues

#### Major-1: FR29 eval gate was split in a way that could weaken Phase 2 readiness

Original evidence before remediation:

- PRD FR-29 requires Phase 2 to include at least 20 executable synthetic eval queries, covering expected_documents, expected_chunks, answerable, ACL and attack_type; placeholders cannot pass the smoke gate.
- Epic 3 Story 3.7 originally required only a generic retrieval eval set and did not specify the 20-case minimum.
- Epic 5 Story 5.1 requires all 20 cases, but Epic 5 comes after Epic 4.

Impact:

- Hybrid Retrieval could be considered implemented in Epic 3 without satisfying the PRD's explicit Phase 2 eval threshold.
- The project could enter RAG answering before retrieval quality has enough regression evidence.

Recommendation:

- Remediation completed: Story 3.7 now requires 20 executable retrieval eval cases, including ACL isolation, no-answer and prompt injection regression scenarios.

#### Major-2: Authentication choice was unresolved and affected early UI, API and test fixtures

Original evidence before remediation:

- PRD, UX and Architecture originally left MVP auth open: simulated AuthContext, JWT or SSO-ready adapter.
- Stories assume AuthContext injection, scope badges, permission-denied states, tenant filters and user/role fixtures.

Impact:

- Implementation can still start with the AuthContext interface, but early API tests, Open WebUI adapter behavior, UX scope states and RBAC fixtures may diverge if the auth mode is decided late.

Recommendation:

- Remediation completed: MVP auth is now fixed as development/test simulated AuthContext plus lightweight JWT adapter, both producing the same `AuthContext` DTO; SSO remains deferred.

### Minor Concerns

#### Minor-1: Epic 1 is infrastructure-heavy and should remain framed as an enabling foundation

Epic 1 is acceptable because this is a greenfield backend platform and the architecture requires a starter-template setup story. However, it does not itself deliver employee-facing knowledge value. Implementation tracking should not treat Epic 1 completion as a product MVP; it is a foundation gate.

#### Minor-2: Some acceptance criteria are conditional on optional custom UI

Story 4.7 includes ACs such as "Given 第一阶段自定义前端存在". These are valid if custom UI enters MVP, but conditional ACs can be hard to test if the team chooses Open WebUI-only integration.

Recommendation:

- Split optional UI checks into a clearly labeled "if custom sidecar is in scope" checklist or decide the sidecar scope before Story 4.7 starts.

#### Minor-3: Several technical stories use developer/platform-engineer personas

This is acceptable for infrastructure, provider and eval work, but each story should continue to prove user or operator outcome through acceptance tests. Do not allow implementation tasks to degrade into untested scaffolding-only tickets.

### Dependency Analysis

- Epic sequence is coherent: platform foundation -> document assets -> retrieval -> RAG/chat -> eval hardening -> Agent.
- No forward dependency was found where Epic N requires Epic N+1 to pass.
- Agent stories correctly depend on Tool Registry first, and Agent UI/review is phase-gated behind runtime limits and audit.
- Within-epic sequencing is mostly sound. The only sequencing concern is Epic 6, where `/agent/run` persistence appears after some tool stories. This is not blocking because tools can be implemented and tested through the registry first, but teams may choose to move Story 6.5 earlier if API-driven vertical slicing is preferred.

### Database Creation Timing Review

Passed.

- No "create all tables up front" anti-pattern was found.
- Tables are introduced when first needed by the relevant capability.
- Story-level migrations include required governance fields (`id`, `created_at`, `updated_at`, `tenant_id`, `created_by`, `status`) where applicable.

### Best Practices Compliance Checklist

| Area | Result | Notes |
| --- | --- | --- |
| Epics deliver user/operator value | Pass with caution | Epic 1 is a greenfield foundation exception. |
| Epic independence | Pass | No future-epic dependency found. |
| Story sizing | Pass | 39 stories are generally coherent and implementable. |
| No forward dependencies | Pass | No blocking forward references found. |
| Database tables created when needed | Pass | Migration timing is incremental. |
| Clear acceptance criteria | Pass with caution | Story 3.7 eval count and Story 4.7 optional UI scope need tightening. |
| Traceability to FRs | Pass | Story-level FR references are present. |

### Epic Quality Verdict

The epic/story package is implementation-ready with conditions. It is structurally sound and has strong traceability, but the FR29 eval gate should be tightened before starting Hybrid Retrieval implementation, and MVP auth mode should be decided before Story 1.3.

## Step 6: Summary and Recommendations

### Overall Readiness Status

**READY FOR SPRINT PLANNING after remediation.**

The original assessment found two major planning risks. Both have now been remediated in the planning artifacts:

1. FR29's 20-case executable eval gate is now enforced in Epic 3 Story 3.7.
2. MVP authentication mode is now fixed as development/test simulated AuthContext plus a lightweight JWT adapter, both producing the same `AuthContext` DTO.

### Readiness by Area

| Area | Status | Assessment |
| --- | --- | --- |
| Document inventory | Ready | Required PRD, Architecture, Epics and UX artifacts exist in planning artifacts. |
| PRD completeness | Ready with open questions | 32 FRs and 17 NFRs are clear; sparse backend, queue choice, eval sample source and parser order remain open. |
| FR coverage | Ready | 32/32 PRD FRs are covered in epics. No extra FR numbers found. |
| UX alignment | Ready with warnings | UX, PRD and Architecture are aligned. Frontend packaging still needs implementation-time confirmation; auth-driven scope states now use the resolved AuthContext/JWT decision. |
| Epic/story quality | Ready with minor warnings | FR29 eval timing and MVP auth decision have been tightened; optional UI scope still needs implementation-time confirmation. |
| Architecture support | Ready | Architecture maps requirements to modules, layers, APIs, storage, ports, workers and observability. |

### Critical Issues Requiring Immediate Action

No critical blocking issue was found.

The artifacts do not contain missing PRD/architecture/epic/UX documents, missing FR coverage, forward dependency loops, or impossible epic-sized stories.

### Resolved Major Issues

1. **FR29 eval gate timing was too weak in Epic 3.**
   - Original state: Story 3.7 only required a generic retrieval eval set, while the PRD required at least 20 executable synthetic eval queries in Phase 2.
   - Remediation status: **Resolved**. `epics.md` Story 3.7 now requires 20 executable synthetic retrieval eval cases and coverage for ACL isolation, no-answer and prompt injection regression.

2. **MVP authentication mode was undecided.**
   - Original state: PRD/UX/Architecture allowed simulated AuthContext, JWT or SSO-ready adapter without choosing one implementation path.
   - Remediation status: **Resolved**. PRD, Architecture, Epics and UX now fix MVP auth as development/test simulated AuthContext plus lightweight JWT adapter, sharing the same `AuthContext` DTO.

### Warnings and Minor Issues

1. **Epic 1 is technical-heavy.** This is acceptable as a greenfield foundation exception, but it should not be counted as a user-facing product increment.
2. **Story 4.7 has optional UI acceptance criteria.** Decide whether MVP includes a custom sidecar/admin panel or Open WebUI-only integration before implementing it.
3. **Original document page viewer is post-MVP unless reprioritized.** `POST /sources/resolve` is enough for MVP citation inspection.

Operational note: `AGENTS.md` should remain present for coding-agent rules, and `project-context.md` should remain present for BMad persistent facts. Both now exist.

### Recommended Next Steps

1. Proceed to `[SP] Sprint Planning` with `bmad-sprint-planning`.
2. Decide whether Story 4.7 includes a minimal sidecar/admin UI in MVP; if not, mark the custom UI ACs as deferred during sprint planning.
3. Start implementation with Epic 1 Story 1.1 after sprint planning creates the execution sequence.
4. Keep `AGENTS.md` and `project-context.md` synchronized when project rules change.

### Final Note

This assessment originally identified **5 issues requiring attention** across **3 categories**. After remediation, the open issue count is:

- 0 critical blockers
- 0 unresolved major planning issues
- 3 minor warnings

The artifacts are now ready to enter sprint planning. The implementation plan has strong production-grade discipline around Provider abstraction, tenant/RBAC/ACL propagation, hybrid retrieval decomposition, citation, audit, eval and observability. Remaining warnings should be handled during sprint planning rather than blocking the next workflow.

Assessment completed on 2026-05-27 by Codex using `bmad-check-implementation-readiness`.

### Post-Assessment Remediation Log

- 2026-05-27: Updated PRD root copy and planning PRD to record the MVP auth decision and remove auth from Remaining Open Questions.
- 2026-05-27: Updated Architecture authentication section and gap analysis to reflect simulated AuthContext + JWT adapter as the decided MVP path.
- 2026-05-27: Updated `epics.md` Story 1.3 to validate JWT and simulated AuthContext consistency.
- 2026-05-27: Updated `epics.md` Story 3.7 to require 20 executable retrieval eval cases in Epic 3.
- 2026-05-27: Updated UX Experience and decision log to remove auth as an open question.
