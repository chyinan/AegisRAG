---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments:
  - AGENTS.md
  - docs/TECHNICAL_PREFERENCES.md
workflowType: research
lastStep: 6
research_type: technical
research_topic: 企业级本地 RAG + Agent 系统技术架构
research_goals: 基于当前技术生态细化架构取舍、模块边界、实现路线和质量指标
user_name: 浅川枫
date: 2026-05-26
web_research_enabled: true
source_verification: true
---

# Technical Research: 企业级本地 RAG + Agent 系统技术架构

## Research Overview

本技术研究用于约束项目实现路线：先构建可测试、可观测、可替换的 RAG 核心，再建设受控 Agent runtime。研究结论与 `AGENTS.md` 的生产级规则一致，并对技术栈、模块接口、验证指标做进一步细化。

## Technical Scope Confirmation

研究范围：

- 技术栈和基础设施取舍。
- API、异步任务、存储、检索、RAG、Agent 的集成模式。
- 架构模式和边界。
- 实现路线、测试策略、质量指标。

## Technology Stack Analysis

### 后端

推荐继续采用：

- Python 3.11+。
- FastAPI。
- Pydantic v2。
- SQLAlchemy 2.x。
- Alembic。
- PostgreSQL。
- Redis。
- Celery 或 RQ。
- httpx。
- pytest、ruff、mypy optional。

理由：该组合能覆盖 async API、数据模型、异步任务、测试、迁移和部署，且与 RAG / Agent 生态兼容。

### 向量和搜索

默认路线：

1. PostgreSQL + pgvector：第一阶段默认，方便 metadata、ACL、版本和向量统一治理。
2. PostgreSQL full text search 或 OpenSearch：承担 BM25 / sparse retrieval。
3. FAISS：本地轻量和离线测试。
4. Milvus：大规模和高并发阶段再引入。

技术取舍：

- pgvector 适合作为产品早期默认，不必过早引入分布式向量库。
- BM25 必须第一阶段进入，因为企业文档包含大量编号、条款和专有名词。
- OpenSearch 可作为 sparse 和 hybrid 的增强基础设施，但会增加部署复杂度。

### LLM 和 Embedding

必须抽象：

- `LLMProvider`
- `EmbeddingProvider`
- `Reranker`
- `Tokenizer`

测试默认使用 fake provider，不允许单元测试调用真实外部模型。

## Integration Patterns Analysis

### API 边界

API route 只做：

- request_id 注入。
- AuthContext 解析。
- schema 校验。
- 调用 application service。
- 结构化响应和错误映射。

业务编排放到 application service，检索规则放到 domain，外部系统放到 infrastructure adapter。

### 异步任务

必须异步化的任务：

- 大文件 parse。
- chunk 和 checksum 计算。
- embedding batch。
- vector / sparse index upsert。
- eval batch。

上传接口只返回 job id，不等待大批量 embedding 完成。

### 数据流

推荐链路：

```text
upload
 -> raw document storage
 -> document metadata
 -> ingestion job
 -> parse
 -> clean
 -> dedup
 -> chunk
 -> embedding job
 -> vector upsert
 -> sparse index upsert
 -> retrieval-ready status
```

检索链路：

```text
query
 -> optional rewrite
 -> ACL and metadata filter build
 -> dense retrieval
 -> sparse retrieval
 -> RRF merge
 -> dedup
 -> rerank
 -> threshold
 -> context packing
 -> prompt build
 -> LLM generate or stream
 -> citation extraction
```

Agent 链路：

```text
agent request
 -> policy check
 -> runtime state
 -> model step
 -> tool selection
 -> registry validation
 -> permission check
 -> timeout execution
 -> audit log
 -> final answer validation
```

## Architectural Patterns and Design

### 推荐架构

采用“分层 + 端口适配器”风格：

- API Layer：FastAPI route、schema、dependency。
- Application Service Layer：用例编排。
- Domain Layer：chunk、retrieval、RAG、Agent 的核心规则。
- Infrastructure Layer：LLM、embedding、vector store、object storage、queue、search。
- Storage Layer：SQLAlchemy model、repository、migration。

### 关键接口

必须优先定义：

- `DocumentParser`
- `Chunker`
- `EmbeddingProvider`
- `VectorStore`
- `SparseRetriever`
- `HybridMerger`
- `Reranker`
- `ContextPacker`
- `PromptBuilder`
- `LLMProvider`
- `ToolRegistry`
- `AgentRuntime`

接口先行可以确保测试和替换，不被具体 SDK 绑定。

### 安全架构

安全边界：

- 用户输入、文档内容、Web 内容、Tool output 都是不可信输入。
- 权限由后端策略执行，不由 prompt 执行。
- Tool output 只能作为 observation。
- 敏感工具需要 allowlist、参数校验、timeout 和审计。

### 可观测架构

请求日志字段：

- request_id、trace_id、user_id、tenant_id。
- latency、model、token usage。
- dense top_k、sparse top_k、rerank score。
- tool calls、error code。

RAG 特有指标：

- retrieval hit rate。
- context precision。
- faithfulness / groundedness。
- citation coverage。
- no-answer rate。
- permission leakage rate。

## Implementation Research

### 推荐实现顺序

1. `packages/common`：config、logging、errors、request context。
2. `packages/auth`：AuthContext、RBAC policy、ACL model。
3. `packages/data`：document、version、chunk、job model。
4. `packages/ingestion`：parser、cleaner、dedup、chunker。
5. `packages/embeddings`：provider protocol、fake provider、batch job。
6. `packages/vectorstores`：VectorStore protocol、pgvector adapter、FAISS optional。
7. `packages/retrieval`：dense、sparse、RRF、rerank interface。
8. `packages/rag`：context packing、prompt builder、citation extraction。
9. `packages/llm`：provider protocol、streaming。
10. `packages/agent`：Tool Registry、runtime、audit。
11. `apps/api`：routes 调 application services。
12. `apps/worker`：ingestion、embedding、eval worker。

### 测试策略

每个阶段必须有测试：

- parser：不同格式和异常文档。
- chunker：token count、overlap、metadata。
- retrieval：dense fake、BM25 fake、RRF、dedup、ACL filter。
- rag：context packing、prompt injection 防护、citation。
- agent：max_steps、permission、timeout、repeated action。
- API：schema、structured error、request_id。

### 技术债控制

禁止为了速度做以下取舍：

- 在 route 中直接拼 prompt。
- 在业务代码中直接调用具体 LLM SDK。
- 上传时同步等待 embedding。
- 检索后再做权限过滤。
- Agent 直接调用任意 Python 函数。
- 只写 happy path，不写 fake provider 测试。

## Technical Recommendations

### 必做

- 把 pgvector 作为默认实现，同时保留 `VectorStore` 协议。
- BM25 sparse retrieval 第一阶段就实现。
- Reranker 可以先 fake，但接口必须存在。
- Tool Registry 必须先于 Agent runtime。
- Eval dataset 必须在 Phase 2 开始建立。
- structured logging 从第一个 API 开始做。

### 暂缓

- Milvus。
- Graph RAG。
- 多 Agent。
- 复杂 Web crawler。
- 全量自定义前端。

这些功能对就业展示有加分，但不是可信 MVP 的前置条件。

## Source Documentation

- pgvector: https://github.com/pgvector/pgvector
- PostgreSQL Full Text Search: https://www.postgresql.org/docs/current/textsearch.html
- Milvus Documentation: https://milvus.io/docs
- OpenSearch Hybrid Search Documentation: https://docs.opensearch.org/docs/latest/search-plugins/search-pipelines/normalization-processor/
- LangGraph Documentation: https://docs.langchain.com/oss/python/langgraph/overview
- LlamaIndex Documentation: https://docs.llamaindex.ai/
- Dify Documentation: https://docs.dify.ai/
- Open WebUI Knowledge Documentation: https://docs.openwebui.com/features/workspace/knowledge/
- OWASP Top 10 for LLM Applications 2025: https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/
- Ragas Documentation: https://docs.ragas.io/
- OpenTelemetry GenAI Semantic Conventions: https://opentelemetry.io/docs/specs/semconv/gen-ai/

## Technical Research Conclusion

最佳技术路线是“先窄后深”：先做一个权限、引用、评估、可观测完整的企业 RAG 核心，再扩展 Tool Registry 和 Agent runtime。这样既能形成产品可信度，也能在就业场景中证明系统设计和工程落地能力。
