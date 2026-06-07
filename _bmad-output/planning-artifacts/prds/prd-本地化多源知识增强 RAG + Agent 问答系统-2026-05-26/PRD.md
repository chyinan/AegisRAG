---
title: 本地化多源知识增强 RAG + Agent 问答系统
status: draft
created: 2026-05-26
updated: 2026-05-26
owner: 浅川枫
source_inputs:
  - AGENTS.md
  - docs/TECHNICAL_PREFERENCES.md
  - docs/EMPLOYMENT_PRODUCT_MARKET_OPTIMIZATION.md
  - _bmad-output/planning-artifacts/research/domain-enterprise-rag-agent-industry-research-2026-05-26.md
  - _bmad-output/planning-artifacts/research/market-enterprise-rag-agent-employment-product-research-2026-05-26.md
  - _bmad-output/planning-artifacts/research/technical-enterprise-rag-agent-architecture-research-2026-05-26.md
---

# PRD: 本地化多源知识增强 RAG + Agent 问答系统

## 0. 文档目的

本文档面向产品、架构、开发、测试和后续 BMAD 工作流使用者，定义“本地化多源知识增强 RAG + Agent 问答系统”的产品范围、能力边界、功能需求、非功能需求、MVP 验收指标和后续开放问题。本文档基于仓库现有 BMAD 调研、技术偏好和 `AGENTS.md` 规则生成，不重复技术实现细节，但会明确可验证的产品行为、权限边界、数据治理和质量要求。

本 PRD 使用稳定编号：用户旅程为 `UJ-*`，功能需求为 `FR-*`，成功指标为 `SM-*`。所有未由用户直接确认、但由现有文档推导出的内容以 `[ASSUMPTION: ...]` 标记，并在末尾索引。

## 1. 产品愿景

本产品是面向企业内部知识场景的私有化 RAG + 受控 Agent 问答系统。它帮助企业把分散在 PDF、DOCX、TXT、Markdown、Web 和本地文件夹中的知识资料，转化为可治理、可检索、可引用、可审计的问答能力。

产品的核心价值不是“能和大模型聊天”，而是解决企业真实落地中的五个信任问题：找不到、信不过、不能追责、不能越权、难以集成。系统必须在检索阶段执行 `tenant_id`、`user_id`、`acl` 和 RBAC 过滤，在回答阶段提供 citation，在运行阶段记录可观测日志和评估指标。

产品路线遵循“先可信 RAG，后受控 Agent”的策略。MVP 优先完成多源文档接入、文档治理、Hybrid Retrieval、RAG 回答、citation、SSE streaming、RBAC 过滤、eval 和基础可观测。Agent 必须建立在 Tool Registry、权限、timeout、rate limit、max_steps、max_tool_calls 和 audit log 之上，不能作为无边界自动化入口。

## 2. 目标用户

### 2.1 Jobs To Be Done

- 当企业员工需要从内部制度、合同、规范、产品手册、FAQ 或技术文档中找到可靠答案时，系统应能给出基于授权上下文的回答和可追溯来源。
- 当企业 IT / 数字化负责人需要上线内部 AI 知识应用时，系统应提供可私有部署、可配置模型、可审计、可观测、可扩展的后端能力。
- 当法务、合规、制度或 HR 团队需要降低误读和越权风险时，系统应支持页码级 citation、文档版本追踪、无答案策略和权限隔离。
- 当研发、售前、客服或交付团队需要复用标准知识时，系统应支持多源文档治理、关键词和语义混合召回、低延迟问答和检索日志复盘。
- 当开发者需要展示生产级 AI 应用工程能力时，系统应提供清晰的分层架构、Provider 抽象、测试、Docker Compose、eval 和可观测证据。

### 2.2 目标用户群

- 企业 IT / 数字化团队：负责私有知识问答系统的部署、集成、权限治理和运维。
- 业务知识团队：包括制度、HR、法务、客服、售前、研发、交付等知识密集团队。
- 系统管理员：负责租户、用户、角色、权限、文档源、索引状态和审计日志。
- 企业员工：通过 Open WebUI 或自定义前端查询授权范围内的知识。
- 开发者 / 作品集评审者：评估该项目是否具备生产级 RAG、Agent 和后端工程能力。

### 2.3 Non-Users (v1)

- 面向公开互联网内容的大规模搜索引擎用户。
- 需要全自动多 Agent 办公平台的复杂流程自动化团队。
- 需要千万级以上向量规模和多地域高并发部署的超大型企业。[ASSUMPTION: v1 先验证中小规模企业知识库场景，Milvus 和复杂分布式能力后置。]
- 只需要简单 ChatGPT 包装或一次性文档问答 demo 的用户。

### 2.4 关键用户旅程

- **UJ-1. 林敏查询制度条款并获得可追溯答案。**
  - **Persona + context:** 林敏是 HR 共享服务团队成员，需要回答员工关于休假制度的具体问题。
  - **Entry state:** 林敏已通过企业身份认证进入 Open WebUI 或自定义前端，拥有 HR 制度库权限。
  - **Path:** 她输入“试用期员工可以申请年假吗？”；系统带着 `tenant_id`、`user_id`、`roles` 和 `acl` 执行检索；Hybrid Retrieval 同时召回语义相似 chunk 和包含“试用期”“年假”的 BM25 chunk；rerank 后只保留高相关上下文；系统生成回答并附带文档、版本、页码和 chunk citation。
  - **Climax:** 林敏看到回答中的每个关键结论都绑定来源，可以点击 citation 复核原文。
  - **Resolution:** 她将答案发送给员工；如果上下文不足，系统明确说明无法确认而不是编造。
  - **Edge case:** 如果林敏无权访问某部门制度，检索阶段不返回相关 chunk，回答不得暴露存在性或内容。

- **UJ-2. 赵强上传产品手册并观察异步 ingestion 状态。**
  - **Persona + context:** 赵强是售前知识库管理员，需要将新版产品手册加入问答系统。
  - **Entry state:** 赵强已登录管理界面，拥有文档上传和索引管理权限。
  - **Path:** 他上传 PDF 和 Markdown 文件；系统写入 raw document metadata 和 object storage；创建 ingestion job；worker 解析、清洗、去重、切分 chunk；embedding job 批量生成向量；vector index 和 sparse index 更新；状态从 `uploaded` 变为 `retrieval_ready`。
  - **Climax:** 赵强能看到文档版本、chunk 数、checksum、embedding model、embedding dim、索引状态和失败原因。
  - **Resolution:** 新文档可以被授权用户检索；旧版本仍可追踪，不被静默覆盖。
  - **Edge case:** 如果 embedding provider 超时，job 进入可重试失败状态，上传接口本身不阻塞。

- **UJ-3. 陈宇复盘一次回答质量失败。**
  - **Persona + context:** 陈宇是 AI 平台工程师，收到业务反馈“答案引用不准确”。
  - **Entry state:** 他在管理后台打开 retrieval log 或 eval report。
  - **Path:** 他按 `request_id` 查到 dense top_k、sparse top_k、RRF 排名、rerank 分数、context packing 结果、模型名、token usage、latency 和 citation；他对比黄金答案或人工标注；他识别是 sparse 召回缺失还是 rerank 排序错误。
  - **Climax:** 陈宇能够定位失败阶段，而不是只看到最终答案。
  - **Resolution:** 他补充 eval case、调整阈值或修复检索实现，并通过回归测试确认。

- **UJ-4. 王珂运行受控 Agent 完成带工具的问答任务。**
  - **Persona + context:** 王珂是交付顾问，需要让 Agent 查询知识库并计算合同条款中的折扣金额。
  - **Entry state:** 王珂已登录，具备 `rag_search` 和 `calculator` 权限，不具备任意文件读取权限。
  - **Path:** 他发起 `/agent/run`；Agent Runtime 创建受限执行状态；LLM 只能选择 Tool Registry 中已注册工具；每次 tool call 都经过 input schema、permission、timeout、rate limit 和 audit log；达到 max_steps 或 max_tool_calls 后必须停止。
  - **Climax:** Agent 使用 `rag_search` 找到授权文档，再用 `calculator` 计算，并输出带 citation 的最终回答。
  - **Resolution:** 管理员可以审计每次工具调用；Agent 不能直接调用任意 Python 函数，也不能读取未授权路径。

## 3. 术语表

- **租户 (`tenant`)** — 企业或组织隔离单元。所有关键业务数据必须包含 `tenant_id`。
- **用户 (`user`)** — 系统访问主体。每次业务请求必须带 `user_id` 和认证上下文。
- **认证上下文 (`AuthContext`)** — 包含 `user_id`、`tenant_id`、`roles`、`department`、`permissions` 的权限输入。
- **访问控制列表 (`acl`)** — 文档、chunk 或工具的访问规则。检索和工具执行必须在后端根据 `acl` 校验。
- **原始文档 (`RawDocument`)** — 用户上传或系统接入的未解析文件及其原始 metadata。
- **解析文档 (`ParsedDocument`)** — 解析器输出的标准化文档结构。
- **章节 (`Section`)** — 从解析文档中提取的层级结构单元。
- **Chunk** — 可检索的最小文本单元，必须携带 `document_id`、`version_id`、`chunk_id`、`tenant_id`、`source_uri`、`title_path`、页码、token_count、acl 和 checksum。
- **文档版本 (`DocumentVersion`)** — 文档内容变更后的可追踪版本。chunk、embedding 和索引必须绑定 `version_id`。
- **Embedding Provider** — 负责把文本批量转换为向量的可替换抽象。
- **LLM Provider** — 负责生成和流式生成回答的可替换抽象。
- **Vector Store** — 向量数据库适配层，默认 PostgreSQL + pgvector，需预留 FAISS 和 Milvus。
- **Sparse Retriever** — 基于 BM25 或全文检索的关键词召回模块。
- **Hybrid Retrieval** — 结合 Dense Retrieval、Sparse Retrieval、RRF 或加权融合、rerank、阈值过滤和 context packing 的检索流程。
- **Reranker** — 对召回结果重新排序的接口，v1 可使用 fake 或 cross-encoder adapter。
- **Context Packing** — 在 token budget 内组织上下文、去重、排序、合并相邻 chunk 和补齐父子上下文的过程。
- **Citation** — 回答中绑定到 document、chunk、source、page 的来源引用。
- **Tool Registry** — Agent 可调用工具的唯一注册与治理入口，包含 schema、permission、timeout、rate_limit 和 handler。
- **Agent Runtime** — 执行 ReAct 或 Planner-Executor 等受控工具调用流程的运行时。
- **Eval Dataset** — 用于衡量 retrieval hit rate、citation coverage、no-answer correctness、faithfulness 和 ACL 隔离的测试集合。

## 4. 产品功能

### 4.1 多源文档接入与文档治理

**Description:** 系统必须把多源输入统一转化为 `RawDocument -> ParsedDocument -> Section -> Chunk`，并在整个生命周期保留租户、用户、版本、权限、来源和 checksum。上传和索引应异步执行，避免 API 请求同步等待大批量 embedding。

#### FR-1: 文档上传与接入

授权用户可以上传 PDF、DOCX、TXT、Markdown 文件，并可为文档设置 `tenant_id`、source metadata 和 `acl`。实现 UJ-2。

**Consequences (testable):**
- 上传请求返回 `document_id`、`version_id`、`job_id` 和初始状态，不等待 embedding 完成。
- 未授权用户上传文档时返回结构化权限错误。
- 文档 metadata 中必须包含 `tenant_id`、`created_by`、`status`、`source_type`、`source_uri` 和 `checksum`。

#### FR-2: Parser 标准化输出

系统支持 PDF、DOCX、TXT、Markdown parser，并输出统一的 `ParsedDocument` 和 `Section`。

**Consequences (testable):**
- 每种 parser 至少有正常文件和异常文件测试。
- Markdown parser 保留标题层级；PDF parser 尽量保留页码。
- parser 错误必须转化为领域异常并写入 job 状态。

#### FR-3: 可插拔 Chunker

系统提供 `Chunker` 协议，至少支持 FixedSizeChunker，并预留 SemanticChunker 和 HierarchicalChunker。

**Consequences (testable):**
- FixedSizeChunker 支持默认 500 到 800 token chunk 和 10% 到 20% overlap。
- chunk metadata 必须包含 `document_id`、`version_id`、`chunk_id`、`tenant_id`、`source_type`、`source_uri`、`title_path`、`page_start`、`page_end`、`token_count`、`acl`、`checksum`。
- chunker 单测覆盖 overlap、标题路径、页码和 token_count。

#### FR-4: 文档版本和软删除

系统必须记录文档版本，删除默认软删除，支持按版本重建索引。

**Consequences (testable):**
- 同一文档再次上传产生新的 `version_id`，旧 chunk 不被静默覆盖。
- 删除文档后，检索默认排除软删除文档。
- `delete_by_document(document_id, version_id)` 可以删除或标记指定版本索引。

### 4.2 Embedding 与索引管道

**Description:** 系统必须通过 Provider 抽象执行 embedding，并记录模型、维度、版本和索引状态。切换 embedding 模型时不能复用不兼容旧索引。

#### FR-5: Embedding Provider 抽象

系统提供 `EmbeddingProvider` 协议，支持 batch embedding、timeout、retry、rate limit 和 fake provider。

**Consequences (testable):**
- 单元测试默认使用 FakeEmbeddingProvider，不真实调用外部 LLM API。
- provider 请求必须有 timeout 配置。
- provider 错误必须可重试或进入明确失败状态。

#### FR-6: Embedding 元数据记录

每个 embedding job 和向量记录必须记录 `embedding_provider`、`embedding_model`、`embedding_version` 和 `embedding_dim`。

**Consequences (testable):**
- embedding_dim 与目标索引维度不一致时拒绝写入。
- embedding_model 变化时必须触发新索引或重建流程，不能静默复用旧索引。

#### FR-7: Vector Store 统一接口

系统提供 `VectorStore` 协议，支持 upsert、search、delete_by_document、metadata filter、tenant filter、ACL filter、soft delete、top_k 和 score threshold。

**Consequences (testable):**
- pgvector adapter 是默认实现。
- FAISS adapter 可作为本地轻量方案。[ASSUMPTION: FAISS 在 MVP 中可先提供接口或开发环境实现，生产默认仍为 pgvector。]
- Milvus adapter 不属于 MVP 必交付，但接口边界不得阻塞后续接入。

### 4.3 Hybrid Retrieval

**Description:** 检索是产品可信度的核心。系统必须拆分 dense retrieval、BM25 sparse retrieval、hybrid merge、dedup、rerank、threshold filter 和 context packing，禁止“问题 -> 向量 top_k -> 拼 prompt -> LLM”的 demo 写法。

#### FR-8: Dense Retrieval

系统可以通过 `EmbeddingProvider` 和 `VectorStore` 执行语义召回，并支持 `tenant_id`、metadata 和 `acl` 过滤。实现 UJ-1、UJ-3。

**Consequences (testable):**
- dense search 请求必须包含 AuthContext 派生的 tenant 和 ACL filter。
- 检索结果必须包含 `chunk_id`、`document_id`、`version_id`、`source`、页码、`title_path`、score、retrieval_method、`tenant_id` 和 `acl`。

#### FR-9: BM25 Sparse Retrieval

系统支持 BM25 或 PostgreSQL full text / OpenSearch sparse retrieval，用于条款、编号、错误码、人名、产品型号等关键词场景。

**Consequences (testable):**
- sparse retrieval 单测覆盖关键词精确召回。
- sparse retrieval 与 dense retrieval 使用相同的 tenant 和 ACL filter。
- MVP 不允许只依赖纯向量检索。

#### FR-10: Hybrid Merge

系统支持 RRF 或加权融合，将 dense 和 sparse 结果合并并去重。

**Consequences (testable):**
- 同一 `chunk_id` 出现在多个召回源时合并为一个候选项，并保留 retrieval_method 列表或融合原因。
- RRF merge 有确定性排序测试。
- merge 结果低于 score threshold 时不得进入上下文。

#### FR-11: Reranker 接口

系统提供 `Reranker` 协议，支持 fake reranker 和后续 cross-encoder / LLM rerank adapter。

**Consequences (testable):**
- rerank 前后记录分数和 latency。
- reranker 失败时必须有明确降级策略或结构化错误。
- 单元测试不真实调用外部模型。

#### FR-12: Retrieval Log

每次 retrieval 必须记录可复盘日志。

**Consequences (testable):**
- 日志字段至少包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、query 摘要、dense top_k、sparse top_k、RRF 结果、rerank score、latency、error_code。
- 日志禁止记录 API key、access token、企业机密全文和用户敏感原文。

### 4.4 RAG 回答、Citation 与 Streaming

**Description:** RAG Generation 必须基于可授权上下文回答，并在无法确认时拒答。Prompt building、context packing、generation 和 citation extraction 必须拆分为独立模块。

#### FR-13: Context Packing

系统支持 token budget、chunk 去重、按 rerank 分数排序、相邻 chunk 合并和父子上下文补齐。

**Consequences (testable):**
- context packer 不接收未授权 chunk。
- 当候选 chunk 超过 token budget 时，系统按策略裁剪并保留裁剪原因。
- context packing 单测覆盖去重、排序、预算和相邻合并。

#### FR-14: Prompt Builder

系统提供 PromptBuilder，明确上下文边界、citation 要求、无答案策略和 prompt injection 防护。

**Consequences (testable):**
- prompt 明确要求仅基于给定上下文回答。
- 文档内容中“忽略系统提示”等指令必须被视为不可信内容。
- route 中不得拼接 prompt。

#### FR-15: LLM Provider 抽象

系统提供 `LLMProvider` 协议，支持 generate 和 stream，并可接 OpenAI、Qwen、DeepSeek、本地 vLLM、Ollama。

**Consequences (testable):**
- 业务代码不得直接依赖单一厂商 SDK。
- 单元测试使用 FakeLLMProvider。
- 每次调用记录 model、token usage、latency 和 error_code。

#### FR-16: Citation Answer

系统最终问答结果必须包含 answer 和 citations。

**Consequences (testable):**
- citation 至少包含 `document_id`、`chunk_id`、`source`、`page` 或页码范围。
- 关键结论尽量绑定 citation；无法绑定来源的结论不得伪造 citation。
- citation extractor 单测覆盖多来源、页码缺失和无答案场景。

#### FR-17: SSE Streaming

系统支持流式回答，优先使用 SSE。

**Consequences (testable):**
- SSE 事件类型至少包括 `token`、`citation`、`tool_call`、`tool_result`、`error`、`final`。
- 流式错误必须以结构化 error 事件返回。
- final 事件包含完整 answer metadata。

### 4.5 API、会话与前端集成

**Description:** 系统提供后端 API，可对接 Open WebUI 或自定义 React / Next.js 前端。API route 只负责 schema、认证上下文、service 调用和响应封装。

#### FR-18: 核心 API

系统提供 `POST /upload`、`POST /retrieve`、`POST /query`、`POST /chat`、`POST /sources/resolve`、`POST /agent/run`。

**Consequences (testable):**
- 所有 API 请求支持 `request_id`、`user_id`、`tenant_id`，`session_id` 可选。
- 所有 API 返回统一 data/error/metadata 结构。
- route 层不得直接调用 LLM、向量数据库或复杂业务逻辑。
- `POST /sources/resolve` 必须在打开 citation 时重新校验 AuthContext、tenant、RBAC 和 ACL，只返回授权片段、document/version/chunk/page/source metadata 和安全摘要；无权限或不存在时不得泄露资源存在性。

#### FR-19: 多轮会话记忆

系统支持 chat session 和 chat message 持久化，禁止用全局变量保存用户会话。

**Consequences (testable):**
- `chat_sessions` 和 `chat_messages` 记录 `tenant_id`、`user_id`、created_at、updated_at。
- 会话上下文不得绕过权限过滤。
- 会话历史进入 prompt 前必须经过 token budget 和安全过滤。

#### FR-20: 前端集成路径

MVP 首选通过 Open WebUI 兼容 chat adapter 接入，由后端 `/chat` 承载 RAG、citation、SSE 和权限治理；最小自定义 sidecar 仅用于 Source Inspector、上传/job 状态或日志入口，不让复杂前端挤占 RAG 核心开发优先级。

**Consequences (testable):**
- 后端 API 文档足以让 Open WebUI 或轻量前端调用。
- 自定义前端的第一阶段只展示上传、查询、citation、job 状态和日志入口。[ASSUMPTION: 第一阶段以前端集成为辅助目标，后端 RAG 闭环优先。]
- Source Inspector、Knowledge Admin、Diagnostics 等自定义界面若进入 MVP，必须满足 UX 文档中的 WCAG 2.2 AA、键盘可访问、`aria-live`、焦点管理、非纯颜色状态和长 ID 换行/截断要求。

### 4.6 Auth、RBAC、审计与数据治理

**Description:** 权限和治理是产品可信度的前置条件，不得作为 prompt 规则后补。所有业务请求必须带认证上下文，检索和工具执行必须在后端策略中执行权限校验。

#### FR-21: AuthContext

系统定义统一 AuthContext，包含 `user_id`、`tenant_id`、`roles`、`department`、`permissions`。

**Consequences (testable):**
- application service 必须显式接收 AuthContext 或 RequestContext。
- 缺少 tenant 或 user 的业务请求被拒绝。
- 测试覆盖跨租户访问拒绝。

#### FR-22: RBAC 与 ACL 检索过滤

系统在 retrieval 阶段执行 tenant、RBAC 和 ACL filter，禁止先检索全量再在答案中过滤。

**Consequences (testable):**
- 同一 query 在不同 tenant 下不能返回对方文档。
- 无权限文档 chunk 不进入 rerank、context packing 或 prompt。
- permission leakage rate 必须为 0。

#### FR-23: Audit Log

系统记录关键业务行为审计，包括上传、删除、检索、问答、Agent run 和 tool call。

**Consequences (testable):**
- 审计日志包含 request_id、trace_id、tenant_id、user_id、action、resource、latency、status、error_code。
- 审计日志不记录企业机密全文和敏感 token。

#### FR-24: 数据保留和软删除策略

系统支持文档软删除、版本追踪、日志保留策略和配置化清理。

**Consequences (testable):**
- 软删除文档默认不可检索。
- 日志保留周期可配置。[ASSUMPTION: MVP 先提供配置项和基础清理策略，复杂合规归档后续增强。]

### 4.7 Tool Registry 与受控 Agent

**Description:** Agent 是第二阶段增强能力。Agent 不能直接调用任意 Python 函数，必须通过 Tool Registry 受控执行。MVP 可以完成 Tool Registry 基础和最小 ReAct runtime，但不能以牺牲 RAG 闭环为代价。

#### FR-25: Tool Registry

系统提供 Tool Registry，工具定义必须包含 name、description、input_schema、output_schema、permission、timeout、rate_limit、handler。

**Consequences (testable):**
- 未注册工具不可调用。
- 工具入参必须按 schema 校验。
- 工具执行前必须校验 permission。

#### FR-26: 必备工具

系统支持 `rag_search`、`calculator`、`file_reader`，`web_search` 可后续扩展。

**Consequences (testable):**
- `rag_search` 复用 retrieval 权限过滤。
- `calculator` 不访问外部资源。
- `file_reader` 只能读取 allowlist 范围，不能读取任意路径。

#### FR-27: Agent Runtime

系统支持 ReAct 起步，并预留 Planner-Executor 和 LangGraph 风格状态图。

**Consequences (testable):**
- Agent Runtime 必须有 max_steps、max_tool_calls、timeout 和 repeated action detection。
- 到达限制后 Agent 必须停止并返回结构化状态。
- Agent 不能让 LLM 决定用户是否有权限。

#### FR-28: Tool Call Audit

每次工具调用必须记录审计日志。

**Consequences (testable):**
- `tool_calls` 记录 agent_run_id、tool_name、参数摘要、结果摘要、latency、status、error_code、tenant_id、user_id。
- 参数摘要不得包含敏感全文或密钥。

### 4.8 Eval、可观测性与运维

**Description:** 系统必须从第一阶段开始记录可观测数据，并从 retrieval 阶段建立 eval dataset。没有 eval 和 observability 的 RAG 无法证明可信。

#### FR-29: RAG Eval Dataset

系统支持维护可执行 eval dataset，用于 retrieval、citation、no-answer、ACL 隔离和 prompt injection 回归。

**Consequences (testable):**
- Phase 2 至少包含 20 条可执行 synthetic eval query，覆盖 expected_documents、expected_chunks、answerable、ACL 和 attack_type；占位样例不能满足 smoke gate。[ASSUMPTION: 初始 eval 集由项目维护者手工构造，覆盖制度、产品手册、FAQ 和技术文档样例。]
- eval 结果输出 retrieval hit rate、citation coverage、no-answer correctness 和 ACL 隔离结果。

#### FR-30: Structured Logging

系统使用结构化日志记录 request、retrieval、generation、tool 和 error。

**Consequences (testable):**
- 日志字段至少包含 request_id、trace_id、tenant_id、user_id、latency、model、token usage、retrieval top_k、rerank score、tool calls、error_code。
- 禁止使用 `print` 代替日志。

#### FR-31: Metrics 与 Dashboard 预留

系统预留 Prometheus、OpenTelemetry 和 Grafana 接入。

**Consequences (testable):**
- MVP 至少暴露 health/readiness 和关键 latency 指标。[ASSUMPTION: Grafana dashboard 可在后续阶段补全，不阻塞 MVP。]
- worker queue backlog 可观测。

#### FR-32: Docker Compose 本地部署

系统支持 Docker Compose 启动核心服务。

**Consequences (testable):**
- Compose 至少包含 api、worker-ingestion、worker-embedding、postgres、redis、minio。
- opensearch、milvus、prometheus、grafana 可选。
- 启动流程包含 migration 和 health check。

## 5. Cross-Cutting NFRs

### 5.1 安全

- 用户输入、文档内容、Web 内容和 Tool output 都是不可信输入。
- 权限逻辑必须在后端策略执行，不得放在 prompt 中。
- Prompt injection 防护覆盖文档诱导忽略系统提示、泄露密钥、越权读取文件、Web 页面诱导危险工具等场景。
- 外部 provider 调用必须配置 timeout，不得硬编码 API key。
- 日志不得记录 API key、access token、企业机密全文或用户敏感原文。

### 5.2 性能

- `/upload` 返回 job id 的 API 路径不得等待大批量 embedding 完成。
- `/query` 和 `/chat` 支持 SSE，优先优化 first-token latency。
- retrieval 记录 dense、sparse、merge、rerank 和 context packing 分阶段耗时。
- MVP 性能目标以可观测和可调优为主；具体 p95 SLA 需在真实数据规模确认后设定。[ASSUMPTION: 现阶段尚无真实并发和文档规模输入，因此不设硬性 p95 数值。]

### 5.3 可靠性

- worker 任务支持失败状态、重试和错误原因记录。
- 文档索引状态必须可追踪，不能出现用户以为已可检索但索引实际未完成的静默状态。
- 数据删除默认软删除，避免误删后不可恢复。

### 5.4 可测试性

- 核心模块必须可单元测试。
- 测试默认禁止真实调用外部 LLM API。
- Provider、VectorStore、Reranker、Tool Registry 和 Agent Runtime 必须有 fake 或 mock 测试实现。

### 5.5 可扩展性

- 默认实现优先 PostgreSQL + pgvector，但 VectorStore 接口必须允许 FAISS 和 Milvus 接入。
- LLM 和 Embedding Provider 必须可配置切换。
- Agent 工作流先支持 ReAct，后续可扩展 Planner-Executor 和 LangGraph 风格状态图。

## 6. 集成与依赖

- **数据库:** PostgreSQL，默认承载业务 metadata、权限、日志和 pgvector。
- **缓存 / 队列:** Redis + Celery 或 RQ。[ASSUMPTION: 队列框架最终选型尚未确认，PRD 只要求异步任务能力。]
- **对象存储:** MinIO 或 S3 兼容对象存储。
- **Sparse Search:** PostgreSQL full text 起步，OpenSearch 可选增强。
- **LLM Provider:** OpenAI、Qwen、DeepSeek、本地 vLLM、Ollama，通过抽象接入。
- **Embedding Provider:** API embedding 或本地 embedding，通过抽象接入。
- **Frontend:** Open WebUI 或自定义 React / Next.js 前端。
- **Observability:** structured logging 起步，预留 OpenTelemetry、Prometheus、Grafana。

## 7. 数据治理与数据库范围

PostgreSQL 至少包含以下表或等价模型：

- `users`
- `tenants`
- `roles`
- `documents`
- `document_versions`
- `chunks`
- `embedding_jobs`
- `retrieval_logs`
- `chat_sessions`
- `chat_messages`
- `agent_runs`
- `tool_calls`

所有表必须包含 `id`、`created_at`、`updated_at`。关键业务表必须包含 `tenant_id`、`created_by`、`status`。文档、chunk、embedding、retrieval、generation 和 audit log 必须贯穿 `tenant_id`、`user_id`、`acl`、`document_id`、`version_id`、`chunk_id`。

## 8. MVP 范围

### 8.1 In Scope

- FastAPI 项目骨架和分层目录。
- `AuthContext`、`RequestContext`、结构化错误、配置加载。
- PostgreSQL + pgvector 默认向量存储。
- Redis + worker 异步 ingestion / embedding。
- MinIO 或本地对象存储接口。
- PDF、DOCX、TXT、Markdown parser。
- FixedSizeChunker，SemanticChunker 和 HierarchicalChunker 接口预留。
- EmbeddingProvider、LLMProvider、VectorStore、Reranker 抽象。
- Fake providers 用于测试。
- Dense Retrieval、BM25 Sparse Retrieval、RRF merge、dedup、rerank interface、ACL filter。
- Context packing、prompt builder、citation extraction。
- `/upload`、`/retrieve`、`/query`、`/chat`、`/sources/resolve`。
- SSE `token`、`citation`、`error`、`final`，Agent 阶段扩展 `tool_call`、`tool_result`。
- retrieval logs、structured logs、基础 eval dataset。
- Docker Compose 本地启动核心服务。

### 8.2 Out of Scope for MVP

- Milvus 生产级部署，后续大规模阶段再引入。
- Graph RAG。
- 多 Agent 协作。
- 复杂 Web crawler。
- 完整 Observability Dashboard。
- 自研复杂前端。
- 让 Agent 执行敏感写操作。
- 真实企业 SSO 深度集成；MVP 认证方案已固定为开发/测试使用模拟 AuthContext，API 集成使用轻量 JWT adapter，二者都解析为同一 `AuthContext`。

## 9. 路线图

### Phase 0: 工程骨架和质量门

- 建立 `apps/`、`packages/`、`tests/`、`docs/`、`docker/`。
- 定义 common、auth、data 基础模型。
- 配置 pytest、ruff、基础 CI。
- 提供 health endpoint 和 Docker Compose 最小启动。

### Phase 1: Ingestion 和文档治理

- 实现 parser、cleaner、dedup、FixedSizeChunker。
- 实现文档版本、chunk metadata、ACL 和 ingestion job 状态。
- 上传 API 只创建 job，不同步等待 embedding。

### Phase 2: Hybrid Retrieval

- 实现 dense、BM25 sparse、RRF merge、dedup、rerank interface、threshold filter。
- 检索阶段执行 tenant / ACL filter。
- 建立至少 20 条 eval query 和 retrieval log。

### Phase 3: RAG Answering

- 实现 context packing、prompt builder、LLMProvider、citation extraction。
- 支持 `/query`、`/chat` 和 SSE streaming。
- 覆盖无答案、引用、多轮上下文和 prompt injection 测试。

### Phase 4: 受控 Agent

- 实现 Tool Registry。
- 实现 `rag_search`、`calculator`、`file_reader`。
- 实现 ReAct Runtime、max_steps、max_tool_calls、timeout、repeated action detection 和 tool_call audit。

### Phase 5: 产品化与展示

- 接入 Open WebUI 或轻管理台。
- 提供 demo 数据集、eval report、权限隔离演示和失败案例复盘。
- 完善 README、架构图、API 示例和面试讲解材料。

## 10. Success Metrics

**Primary**

- **SM-1: Permission leakage rate = 0** — 未授权文档不得进入 retrieval、rerank、context packing 或最终回答。验证 FR-8、FR-9、FR-13、FR-21、FR-22。
- **SM-2: Citation coverage >= 90% for answerable eval questions** — 可回答 eval 问题中的关键结论应绑定 citation。验证 FR-14、FR-16、FR-29。
- **SM-3: Retrieval hit rate >= 80% on initial eval set** — 至少一个正确 chunk 出现在最终上下文候选中。验证 FR-8、FR-9、FR-10、FR-11、FR-29。[ASSUMPTION: 初始阈值用于 MVP 校准，后续需按数据集规模调整。]
- **SM-4: No-answer correctness >= 85% on unanswerable eval questions** — 上下文不足时系统应拒答。验证 FR-14、FR-15、FR-16、FR-29。

**Secondary**

- **SM-5: Ingestion traceability = 100%** — 每个 retrieval-ready chunk 都能追溯到 document、version、source、page、acl、checksum 和 embedding metadata。验证 FR-1 到 FR-7。
- **SM-6: Tool audit coverage = 100%** — 每次 tool call 都有审计记录。验证 FR-25 到 FR-28。
- **SM-7: API structured error coverage = 100% for expected domain errors** — 权限、文档不存在、job 失败、provider 超时等预期错误都返回结构化错误。验证 FR-18、FR-23、FR-30。

**Counter-metrics (do not optimize blindly)**

- **SM-C1: 不以回答率牺牲拒答正确性** — 不能为了提高 answer success rate 而编造来源或忽略无答案策略。
- **SM-C2: 不以 Agent 自动化次数作为价值指标** — Agent 工具调用越多不代表越好，重复调用和越权风险必须受限。
- **SM-C3: 不以模型数量作为产品成熟度指标** — Provider 可替换比接入更多模型更重要。

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 退化为 RAG demo | 无法证明生产能力 | 强制拆分 ingestion、retrieval、rag、auth、observability、eval |
| 权限后补 | 存在数据泄露风险 | tenant、ACL、RBAC 从数据模型和检索阶段开始实现 |
| 只做向量检索 | 条款、编号、专有名词召回差 | BM25 sparse retrieval 和 RRF merge 是 MVP 必需 |
| Agent 过早上线 | 工具越权、无限循环、成本失控 | Tool Registry、max_steps、timeout、audit 先于 Agent |
| 前端消耗过多 | 后端核心闭环延迟 | 先接 Open WebUI 或轻量管理台 |
| 缺少 eval | 无法判断质量 | Phase 2 开始维护 eval dataset 和失败样例 |
| 单一模型绑定 | 换模型和本地化部署困难 | LLMProvider 和 EmbeddingProvider 抽象先行 |
| 日志泄露敏感信息 | 合规风险 | 只记录摘要和 metadata，不记录密钥和企业机密全文 |

## 12. 验收标准

- 模块边界清晰，符合 API / Application Service / Domain / Infrastructure / Storage 分层。
- 核心接口有类型定义和 fake 实现。
- 核心模块有单元测试，外部 LLM 默认 mock。
- 上传、检索、问答、citation、权限、日志、eval 至少有端到端 smoke test。
- API 返回结构化错误，包含 request_id。
- 权限过滤在 retrieval 阶段生效。
- Docker Compose 可以启动核心服务。
- 文档包含必要配置、运行命令、API 示例、eval 示例和已知限制。

## 13. Decisions and Open Questions

### 13.1 Resolved Decisions

1. Citation MVP 先通过 `POST /sources/resolve` 返回授权片段、source metadata、document/version/chunk/page 信息；原文页码跳转依赖后续文档预览器，不阻塞 MVP。
2. Open WebUI 首个集成路径为兼容 chat adapter backed by `/chat`；自定义 sidecar 仅服务 Source Inspector、上传/job 状态、日志和 eval 入口。
3. MVP 认证方案固定为“开发/测试模拟 AuthContext + 轻量 JWT adapter”。两种入口必须产出相同的 `AuthContext` DTO，并统一进入后端 RBAC、ACL、tenant filter 和 audit 流程；企业 SSO adapter 后置，但接口从第一天保持 SSO 兼容。

### 13.2 Remaining Open Questions

1. Sparse retrieval 首选 PostgreSQL full text 还是 OpenSearch？
2. 队列框架最终选 Celery 还是 RQ？
3. 初始 eval dataset 的业务样例是否采用 HR 制度、产品手册、售前 FAQ、技术故障手册四类？
4. `file_reader` 工具的 allowlist 范围由配置文件定义，还是由管理员后台维护？
5. Phase 1 是否需要同时实现 DOCX 和 PDF parser，还是按 Markdown/TXT -> PDF -> DOCX 顺序递进？

## 14. Assumptions Index

- §2.3 — [ASSUMPTION: v1 先验证中小规模企业知识库场景，Milvus 和复杂分布式能力后置。]
- §4.2 FR-7 — [ASSUMPTION: FAISS 在 MVP 中可先提供接口或开发环境实现，生产默认仍为 pgvector。]
- §4.5 FR-20 — [ASSUMPTION: 第一阶段以前端集成为辅助目标，后端 RAG 闭环优先。]
- §4.6 FR-24 — [ASSUMPTION: MVP 先提供配置项和基础清理策略，复杂合规归档后续增强。]
- §4.8 FR-29 — [ASSUMPTION: 初始 eval 集由项目维护者手工构造，覆盖制度、产品手册、FAQ 和技术文档样例。]
- §4.8 FR-31 — [ASSUMPTION: Grafana dashboard 可在后续阶段补全，不阻塞 MVP。]
- §5.2 — [ASSUMPTION: 现阶段尚无真实并发和文档规模输入，因此不设硬性 p95 数值。]
- §6 — [ASSUMPTION: 队列框架最终选型尚未确认，PRD 只要求异步任务能力。]
- §10 — [ASSUMPTION: 初始阈值用于 MVP 校准，后续需按数据集规模调整。]
