# 技术偏好与全程 Vibe Coding 实现规则

项目名称：本地化多源知识增强 RAG + Agent 问答系统

本文档用于描述项目的技术偏好、架构约束、实现优先级和 Vibe Coding 过程中的工程标准。它既服务于开发，也服务于简历、面试和项目复盘。

## 1. 项目定位

本项目是一个面向企业内部知识库问答和多工具增强推理的生产级 AI 系统。目标不是完成一个能跑通的 demo，而是构建一个可以长期演进、可部署、可测试、可观测、可扩展的 AI 应用工程项目。

核心关键词：

- RAG（检索增强生成）
- LangChain 生态兼容
- LangGraph 风格 Agent 工作流
- Vector Database
- Embedding
- Agent / Tool Calling
- Hybrid Retrieval
- LLM Orchestration
- RBAC
- Streaming
- Observability

## 2. 技术栈建议

### 2.1 后端

优先选择 Python 技术栈：

- Python 3.11+
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- PostgreSQL
- Redis
- Celery 或 RQ
- httpx
- pytest
- ruff
- mypy 可选

原因：

- Python 是 RAG、Agent、Embedding 和 LLM Orchestration 生态最成熟的语言。
- FastAPI 适合构建异步 API、SSE streaming 和模型服务编排。
- Pydantic 适合定义 Tool Calling schema、API schema 和领域 DTO。

### 2.2 向量数据库

项目实现 VectorStore 协议，支持运行时切换：

1. **PostgreSQL + pgvector**（默认）— 适合企业级系统落地，数据、权限、metadata 和向量统一管理
2. **Milvus**（`VECTOR_STORE_TYPE=milvus`）— 千万级以上向量、分布式扩展和高并发检索
3. **Fake**（测试用）— 内存实现，适合单元测试和 CI

通过不变的 VectorStore 接口，rest of system 完全无感底层切换。

### 2.3 LLM 和 Embedding

系统必须保持模型无关：

- OpenAI
- Qwen
- DeepSeek
- 本地 vLLM
- Ollama

不允许业务模块直接依赖具体模型厂商。必须通过 `LLMProvider`、`EmbeddingProvider` 抽象调用。

### 2.4 前端

两种路线：

- 快速落地：自建 Next.js 工作台
- 自定义产品化：React / Next.js

前端只负责展示、输入、会话管理和状态反馈，RAG、Agent、权限、工具调用、检索逻辑全部放在后端。

## 3. 架构偏好

推荐分层：

```text
Frontend
  -> API Gateway
  -> Application Services
  -> Domain Services
  -> Infrastructure Adapters
  -> Storage
```

核心模块：

```text
Data Layer
Ingestion Pipeline
Embedding Layer
Vector DB Layer
Retrieval Engine
RAG Generation Layer
Agent Layer
Memory Layer
Auth Layer
API Layer
Observability Layer
```

关键原则：

- route 只做请求解析、认证上下文注入和响应封装
- service 负责编排业务流程
- domain 负责核心规则
- infrastructure 负责数据库、向量库、LLM、对象存储、消息队列
- prompt 不是业务逻辑的唯一承载地

## 4. Vibe Coding 总规则

本项目允许全程 Vibe Coding，但不能随意生成代码。每次 AI 编码必须遵守以下过程：

1. 先理解当前目录结构和已有抽象
2. 明确功能属于哪个模块
3. 先设计接口和数据结构
4. 再实现核心逻辑
5. 补测试
6. 补文档
7. 运行验证命令
8. 总结改动和剩余风险

AI 不允许：

- 不读现有代码就新增重复模块
- 用单文件完成复杂功能
- 在 API route 中堆业务逻辑
- 直接调用具体 LLM SDK
- 绕过权限系统
- 省略测试
- 用 mock 掩盖真实接口设计问题

## 5. 数据层设计偏好

数据层必须管理：

- 原始文件
- 文档版本
- 文档 metadata
- chunk
- embedding job
- 权限信息
- ingestion 状态

推荐存储：

- 原始文件：MinIO、本地对象存储或 S3
- 元数据：PostgreSQL
- 向量：pgvector、FAISS 或 Milvus
- 稀疏索引：OpenSearch、Elasticsearch 或 PostgreSQL full text
- 缓存和 session：Redis

文档版本规则：

- 每个文档有 `document_id`
- 每次变更生成 `version_id`
- chunk 和 embedding 绑定 `version_id`
- 删除默认软删除
- 支持按版本回滚和重建索引

权限规则：

- 所有数据必须有 `tenant_id`
- 企业场景必须支持 `department`、`role`、`acl`
- 检索阶段必须进行权限过滤
- 不允许让 LLM 判断权限

## 6. Ingestion Pipeline 偏好

标准链路：

```text
upload / folder scan / web crawl
 -> raw document storage
 -> metadata insert
 -> parse
 -> clean
 -> deduplicate
 -> chunk
 -> enrich metadata
 -> embedding
 -> vector upsert
 -> sparse index upsert
```

解析器：

- PDF：pymupdf、pdfplumber
- DOCX：python-docx
- Markdown：保留标题层级
- TXT：编码检测和段落切分
- OCR：可选，适合扫描件
- Web：正文抽取、去广告、保留 URL

清洗：

- 页眉页脚
- 目录噪声
- 重复水印
- 空行
- 乱码
- 多余空白

去重：

- 文档级 checksum
- chunk 级 SimHash 或 MinHash
- 高成本场景可使用 embedding 相似度去重

## 7. Chunk 策略偏好

必须支持三类 chunk：

### 7.1 固定长度 chunk

适合：

- 普通 PDF
- TXT
- 没有明显结构的文档

默认：

- 500 到 800 tokens
- 10% 到 20% overlap

优点：

- 稳定
- 实现简单
- 对大多数文档可用

缺点：

- 可能切断语义边界
- 对标题层级利用不足

### 7.2 语义 chunk

适合：

- FAQ
- 规章制度
- 产品文档
- Markdown
- Wiki

优点：

- chunk 更贴近自然语义
- 检索精度通常更高

缺点：

- 需要更复杂的解析和边界识别

### 7.3 层级 chunk

适合：

- 长文档
- 合同
- 技术手册
- 政策文件

设计：

- 子 chunk 用于精确召回
- 父 chunk 用于补充上下文

优点：

- 兼顾召回精度和上下文完整性
- 更适合生产级 RAG

## 8. Embedding 偏好

Embedding 层必须支持：

- 本地模型
- API 模型
- batch embedding
- retry
- rate limit
- timeout
- 增量更新
- 模型版本记录

需要记录：

- embedding_model
- embedding_dim
- embedding_provider
- embedding_version
- chunk_id
- document_id
- version_id

模型切换规则：

- embedding 模型变化时必须重建索引
- 不同维度不能写入同一向量索引
- 可以为不同模型维护不同 collection 或 index

## 9. Retrieval Engine 偏好

生产默认检索流程：

```text
User Query
 -> Query Rewrite optional
 -> Dense Retrieval
 -> BM25 Sparse Retrieval
 -> Hybrid Merge
 -> Dedup
 -> Cross Encoder Rerank
 -> ACL Filter
 -> Score Threshold
 -> Context Packing
```

Dense Retrieval：

- 适合语义匹配
- 能处理同义表达
- 对自然语言问题友好

BM25 Sparse Retrieval：

- 适合关键词、编号、错误码、人名、产品型号
- 对企业文档非常重要

Hybrid Retrieval：

- 通过 RRF 或加权融合结合语义召回和关键词召回
- 生产环境通常比纯向量检索更稳定

Rerank：

- 默认使用 Cross Encoder
- LLM rerank 可作为高成本增强选项
- rerank 前召回多一些，rerank 后只保留高质量上下文

## 10. RAG Generation 偏好

Prompt 必须包含：

- 角色说明
- 上下文边界
- 回答约束
- citation 要求
- 无答案策略
- prompt injection 防护规则

生成策略：

- 仅基于上下文回答
- 不足以回答时明确说明无法确认
- 不编造来源
- 关键结论绑定 citation
- 支持 streaming
- 记录 token usage

Context Packing：

- 按 rerank 分数排序
- 过滤低相关 chunk
- 去重
- 合并相邻 chunk
- 控制 token budget
- 保留 source metadata

## 11. Agent 偏好

Agent 是任务执行系统，不是单纯问答系统。RAG 可以作为 Agent 的一个工具。

必须支持：

- Tool Registry
- Tool schema
- Tool permission
- Tool timeout
- Tool audit log
- max_steps
- max_tool_calls
- repeated action detection

工具：

- `rag_search`
- `calculator`
- `file_reader`
- `web_search`

执行模式：

- ReAct：适合简单工具调用
- Planner-Executor：适合复杂多步骤任务
- LangGraph 风格工作流：适合生产级状态机、可恢复执行和可观测 Agent

安全边界：

- Agent 只能调用注册工具
- 工具参数必须校验
- 工具执行必须带用户上下文
- 敏感工具需要权限检查
- Web 和文档内容不能覆盖系统规则

## 12. API 偏好

必须实现：

```text
POST /upload
POST /retrieve
POST /query
POST /chat
POST /agent/run
```

推荐响应结构：

```json
{
  "request_id": "...",
  "data": {},
  "error": null,
  "metadata": {
    "latency_ms": 123
  }
}
```

错误结构：

```json
{
  "request_id": "...",
  "data": null,
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "Document not found"
  }
}
```

流式接口：

- 使用 SSE
- 事件包括 token、citation、tool_call、tool_result、final、error

## 13. 安全偏好

必须防护：

- prompt injection
- 越权检索
- 越权工具调用
- 任意文件读取
- API Key 泄露
- SSRF
- Web 搜索结果注入
- 日志泄露敏感信息

原则：

- 用户输入是不可信的
- 文档内容是不可信的
- Web 内容是不可信的
- Tool output 不等于系统指令
- LLM 不能绕过后端权限策略

## 14. 测试偏好

测试优先级：

1. chunker
2. retrieval merge
3. ACL filter
4. prompt builder
5. citation extraction
6. tool registry
7. agent max_steps
8. API contract

测试原则：

- 核心逻辑必须单元测试
- 外部 LLM 默认 mock
- 向量库适配器可使用 integration test
- RAG 质量用 eval dataset 单独评估

## 15. 可观测性偏好

必须记录：

- request_id
- trace_id
- tenant_id
- user_id
- model
- token usage
- retrieval latency
- rerank latency
- generation latency
- top_k
- tool calls
- error code

指标：

- p50 / p95 latency
- query success rate
- no answer rate
- citation coverage
- retrieval hit rate
- tool error rate
- embedding queue backlog

推荐：

- OpenTelemetry
- Prometheus
- Grafana
- JSON structured logs

## 16. 部署偏好

开发环境使用 Docker Compose。

服务：

```text
api
worker-ingestion
worker-embedding
postgres
redis
minio
opensearch
milvus optional
prometheus optional
grafana optional
```

生产环境可演进到 Kubernetes。

必须支持：

- health check
- readiness check
- migration
- graceful shutdown
- worker retry
- secret management
- backup
- restore

## 17. CI/CD 偏好

推荐流水线：

```text
install dependencies
 -> lint
 -> type check optional
 -> unit tests
 -> integration tests
 -> build docker image
 -> migration check
 -> rag eval smoke test
 -> deploy staging
```

工具：

- ruff
- pytest
- mypy optional
- docker build
- alembic check

## 18. 简历导向实现顺序

为了让项目更适合 AI 应用工程师、RAG 工程师、Agent 工程师岗位展示，优先实现以下能力：

1. 多源文档 ingestion pipeline
2. pgvector + BM25 的 Hybrid Retrieval
3. Cross Encoder rerank
4. citation 可追踪答案生成
5. LLM Provider 抽象
6. Tool Registry
7. ReAct Agent
8. LangGraph 风格状态工作流
9. RBAC 权限隔离
10. RAG 评估和可观测性

## 19. 禁止事项

严禁：

- 单文件实现整个系统
- 将 RAG 写在 route 中
- 将 Agent 写成无限循环
- 没有 citation 的知识库问答
- 没有 ACL 的检索
- 没有 metadata 的 chunk
- 没有版本号的 embedding
- 没有 timeout 的外部调用
- 没有测试的核心逻辑
- 在 prompt 中保存业务状态
- 把 API Key 写进代码

## 20. 推荐给 AI 的工作指令

通用指令：

```text
请按照 AGENTS.md 和 docs/TECHNICAL_PREFERENCES.md 的规则实现。先读取现有目录结构和相关模块，再说明模块边界，然后实现代码、测试和必要文档。不要写 demo，不要绕过 Provider 抽象，不要把业务逻辑写进 route。
```

RAG 指令：

```text
请实现生产级 RAG pipeline，包括 query rewrite 接口、dense retrieval、BM25 retrieval、hybrid merge、rerank、context packing、prompt building 和 citation。所有模块必须可测试，外部 LLM 使用 mock provider。
```

Agent 指令：

```text
请实现 Agent Tool Registry 和 ReAct Agent Runtime。工具必须有 schema、权限、timeout、rate limit、audit log。Agent 必须有 max_steps、max_tool_calls 和 repeated action detection。
```

API 指令：

```text
请实现 FastAPI 接口层，只做请求解析、认证上下文注入和响应封装。具体业务逻辑必须放到 application service 中。
```

## 21. 判断代码是否合格

合格代码应满足：

- 架构层级清晰
- 接口可替换
- 有类型
- 有测试
- 有异常处理
- 有权限校验
- 有日志
- 有配置化
- 有 citation
- 有审计记录
- 可部署

如果一个实现只能在本地跑通一次，但不能被测试、不能被替换、不能被观测、不能被权限系统约束，则不合格。

## 22. 调研驱动的优化补充

本项目的调研结论见：

- `docs/EMPLOYMENT_PRODUCT_MARKET_OPTIMIZATION.md`
- `_bmad-output/planning-artifacts/research/market-enterprise-rag-agent-employment-product-research-2026-05-26.md`
- `_bmad-output/planning-artifacts/research/domain-enterprise-rag-agent-industry-research-2026-05-26.md`
- `_bmad-output/planning-artifacts/research/technical-enterprise-rag-agent-architecture-research-2026-05-26.md`

调研后的技术路线补充：

1. 项目优先定位为“企业私有知识 RAG + 受控 Agent 问答系统”，不是通用聊天工具。
2. 第一阶段必须先完成可信 RAG 闭环：多源 ingestion、chunk metadata、hybrid retrieval、rerank 接口、context packing、citation、SSE、RBAC filter、retrieval log。
3. BM25 sparse retrieval 不后置。企业文档中的编号、条款、错误码、人名、产品型号需要 sparse retrieval 支撑。
4. Agent 只能在 Tool Registry、权限、timeout、max_steps、audit log 完成后进入主线。
5. Eval dataset 从 retrieval 阶段开始建立，至少覆盖 retrieval hit rate、citation coverage、no-answer correctness、ACL 隔离。
6. 自定义 React / Next.js 前端不应阻塞后端核心闭环。
7. Milvus、Graph RAG、多 Agent、复杂 Web crawler 属于第三阶段增强，不作为 MVP 前置条件。

就业展示优先级：

1. 权限隔离和 citation，比单纯模型接入更重要。
2. hybrid retrieval 和 eval，比复杂 Agent 更重要。
3. 结构化日志、审计和 Docker Compose，比花哨 UI 更重要。
4. Provider 抽象和 fake provider 测试，比直接接真实模型更重要。
