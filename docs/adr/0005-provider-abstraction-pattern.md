---
status: Accepted
date: 2026-06-30
deciders: Architecture Team
---

# ADR 0005: Provider Abstraction Pattern for LLM, Embedding, and Vector Store

## Status

Accepted

## Context

AegisRAG 需要对接多种外部 AI 服务：大语言模型（LLM）、文本嵌入（Embedding）、向量数据库（Vector Store）。这些服务来自不同厂商（OpenAI、DeepSeek、Milvus、pgvector 等），API 协议和 SDK 各不相同。如果业务代码直接依赖具体实现，切换提供商或引入新提供商将需要大规模改写。

核心约束：
- 业务代码（检索管线、RAG 生成、知识导入）不应感知底层提供商的具体实现
- 测试环境不能依赖外部 API（成本、网络、速率限制）
- CI 管道必须在无 GPU、无外部服务的裸环境运行
- 未来可能从 pgvector 迁移到 Milvus，或从 DeepSeek 切换到其他 LLM

## Decision

采用 **Provider 接口抽象模式**：在 `packages/` 各子包中定义 Protocol 接口，具体实现在 `adapters/` 子目录下，业务代码仅依赖接口。

### 接口层（Ports）

| 子包 | 接口 | 定义位置 | 核心方法 |
|------|------|----------|----------|
| `packages/llm` | `LLMProvider` | `ports.py:9-12` | `generate(GenerateRequest) → GenerateResponse`, `stream() → AsyncIterator[GenerateChunk]` |
| `packages/embeddings` | `EmbeddingProvider` | `ports.py:8-9` | `embed_texts(EmbeddingRequest) → EmbeddingResponse` |
| `packages/vectorstores` | `VectorStore` | `ports.py:14-28` | `upsert()`, `search()`, `delete_by_document()` |
| `packages/retrieval` | `CandidateRetriever` | `ports.py:19-26` | `retrieve(RetrievalRequest, RetrievalFilterSet) → list[RetrievalCandidate]` |
| `packages/retrieval` | `Reranker` | `ports.py:29-37` | `rerank(request, filters, candidates) → RerankResult` |

### 适配器层（Adapters）

每个接口有多个适配器实现：

```
packages/llm/adapters/
  openai_compatible.py   ← OpenAI / DeepSeek / 兼容端点
  fake.py                ← 可配置失败模式（timeout/rate_limited/failed）

packages/embeddings/adapters/
  openai_compatible.py   ← OpenAI / DeepSeek embedding 端点
  fake.py                ← 固定向量返回

packages/vectorstores/adapters/
  pgvector.py            ← PostgreSQL + pgvector 扩展
  milvus.py              ← Milvus 向量数据库
  fake.py                ← 内存字典存储
```

### 依赖方向

```
业务代码 (service.py / application.py)
  ↓ 依赖
接口 (ports.py: Protocol)
  ↑ 实现
适配器 (adapters/openai_compatible.py, adapters/pgvector.py, ...)
```

业务代码通过构造函数注入接口：

```python
# 业务代码不 import 任何 adapter
class DenseRetriever:
    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,  # 接口
        vector_store: VectorStore,              # 接口
        config: DenseRetrieverConfig,
    ) -> None: ...
```

`LLMReranker` 通过 `LLMProvider` 接口使用已有 LLM 做精排——无需新 API Key、无需新基础设施。

### Fake Provider 用于测试

`FakeLLMProvider` 支持注入 `failure_mode`（timeout / rate_limited / failed / stream_failed），测试可以覆盖异常路径。`FakeEmbeddingProvider` 和 `FakeVectorStore` 同样提供可控行为。CI 管道无需网络即可运行完整测试套件。

## Consequences

**正面影响：**
- **零改动切换**：更换 LLM / Embedding 提供商或向量数据库，只需修改 DI 注入，业务代码不动
- **可测试性**：测试用 fake provider，覆盖正常路径和异常路径（超时、限流、失败）
- **CI 友好**：CI 不依赖外部 AI 服务，测试确定性强、速度快
- **类型安全**：Python Protocol 提供结构化类型检查，编译期发现接口不匹配

**负面影响：**
- 新增提供商需实现完整接口（~150-500 行适配代码）
- Protocol 是隐式接口（鸭子类型），不如 ABC 有显式继承关系，但提供了更好的解耦
- 接口演化需同步所有适配器（可通过版本化 DTO 缓解）

## Alternatives Considered

| 方案 | 评估 |
|------|------|
| 直接使用各 SDK（openai, milvus-client） | 更换提供商需重写所有调用点，测试依赖外部服务 |
| LangChain / LlamaIndex 内置抽象 | 抽象过重，引入大量非必要依赖，版本冲突频发 |
| ABC 抽象基类 | 需要显式继承，增加耦合；Python Protocol 让适配器不感知接口定义 |
| 统一 gateway 服务 | 引入网络跳转和序列化开销，过度设计（YAGNI） |
