# Technical Overview

This document holds the implementation details that are intentionally kept out
of the root README. The root README should read like an open-source project
landing page; this file is the technical map for contributors and reviewers.

## Architecture

AegisRAG is organized around explicit layers:

```text
API Layer
  -> Application Service Layer
    -> Domain Layer
      -> Infrastructure Ports
        -> Storage / External Adapters
```

FastAPI routes assemble request context and call application services. Business
logic belongs in `packages/*` modules. LLMs, embeddings, vector stores, object
storage, queues, audit persistence, and chat memory are accessed through ports
or repositories rather than directly from routes.

```text
apps/
  api/                 FastAPI app, routes, middleware, dependency assembly
  worker/              RQ workers for ingestion and embedding jobs
  web/                 Next.js workbench
packages/
  auth/                AuthContext, RBAC, ACL and permission policy
  common/              config, errors, envelope, context, audit, logging
  data/                document lifecycle, storage models, repositories, queues
  ingestion/           parser registry, cleaners, dedup, chunkers
  embeddings/          embedding DTOs, ports, fake and compatible providers
  vectorstores/        VectorStore contract and adapters
  retrieval/           dense, sparse, RRF, rerank, query rewrite, query router, cache
  rag/                 prompt, context packing, generation, citations, chat
  agent/               Tool Registry, runtime, governed tools, persistence
  memory/              chat session and message memory
  eval/                dataset loaders, runners, reports, smoke gates
```

## Ingestion

Documents are normalized through a production-style ingestion chain:

```text
RawDocument -> ParsedDocument -> Section -> Chunk -> Embedding Job -> Vector Record
```

Implemented parser coverage includes Markdown, TXT, PDF, and DOCX. Chunk
metadata carries document, version, tenant, source, title path, page range,
token count, checksum, and ACL fields. Ingestion work is designed for async
workers so upload requests do not wait for large embedding batches.

## Embeddings and LLM Providers

Embedding and LLM calls go through provider-neutral ports. Local and CI paths
use fake providers by default; real generation can use OpenAI-compatible HTTP
adapters for OpenAI, Qwen, DeepSeek, Ollama, vLLM, or compatible gateways.

Provider rules:

- no vendor SDK calls from route handlers
- external calls require timeout configuration
- provider model names and embedding dimensions are recorded
- tests avoid real external API calls unless explicitly requested

## Retrieval Pipeline

The production retrieval flow is decomposed into testable stages:

```text
query
  -> [query rewrite]          ← HyDE-based, enabled via QUERY_REWRITE_ENABLED
  -> [query routing]          ← adaptive: factual→fast, complex→full, comparison→high-recall
  -> dense retrieval
  -> BM25/PostgreSQL full-text sparse retrieval
  -> RRF merge
  -> deduplication
  -> [rerank]                 ← OpenAI-compatible, enabled via RERANK_PROVIDER
  -> score threshold
  -> [cache]                  ← Redis LRU, enabled via RETRIEVAL_CACHE_ENABLED
  -> context packing
```

Stages in brackets are optional and configurable via environment variables. See
the [evaluation guide](evaluation.md) for benchmarking different configurations.

## Ingestion

Documents are normalized through a production-style ingestion chain:

```text
RawDocument -> ParsedDocument -> Section -> Chunk -> Embedding Job -> Vector Record
```

Two chunking strategies are available:
- **Fixed-size** (default): `CHUNK_SIZE=800`, `CHUNK_OVERLAP=120`
- **Semantic** (opt-in): `SEMANTIC_CHUNKING_ENABLED=true`, splits at topic
  boundaries using embedding similarity (`SEMANTIC_THRESHOLD=0.65`)

Implemented parser coverage includes Markdown, TXT, PDF, and DOCX. Chunk
metadata carries document, version, tenant, source, title path, page range,
token count, checksum, and ACL fields. Ingestion work is designed for async
workers so upload requests do not wait for large embedding batches.

## RAG Generation

The RAG layer contains:

- context packing with token budgets and deduplication
- prompt building with explicit untrusted-context boundaries
- provider-neutral generation and streaming
- citation extraction from packed context
- source resolution with authorization rechecks
- no-answer behavior when the context does not support an answer

The intended answer shape includes an answer plus structured citations:

```json
{
  "answer": "...",
  "citations": [
    {
      "document_id": "...",
      "chunk_id": "...",
      "source": "...",
      "page": 3
    }
  ]
}
```

## Agent Runtime

The Agent layer is deliberately governed. Agents do not call arbitrary Python
functions. The Tool Registry owns the executable boundary:

```text
name
description
input_schema
output_schema
permission
timeout
rate_limit
handler
```

The runtime supports max steps, max tool calls, timeout policy, repeated action
detection, audit logging, and final answer validation. Implemented tools include
`rag_search`, `calculator`, and restricted `file_reader`.

## Authentication and Authorization

Protected business requests use backend `AuthContext` data:

```text
user_id
tenant_id
roles
department
permissions
acl
```

Retrieval applies tenant and ACL filters during query construction. Source
resolution rechecks tenant, RBAC, ACL, document, version, chunk, soft-delete,
and active-state rules. Client UIs and compatibility adapters are entry points,
not authorization boundaries.

## API Surface

Core API areas include:

- `POST /upload`
- `POST /retrieve`
- `POST /query`
- `POST /chat`
- `POST /agent/run`
- source resolution and safe source metadata endpoints
- diagnostics, review queue, audit explorer, and eval evidence endpoints
- OpenAI-compatible chat integration paths for external clients

Streaming paths use SSE-style events such as `token`, `citation`, `tool_call`,
`tool_result`, `error`, and `final` where applicable.

## Storage Model

The PostgreSQL storage layer covers:

```text
users
tenants
roles
documents
document_versions
chunks
embedding_jobs
retrieval_logs
chat_sessions
chat_messages
agent_runs
tool_calls
review_items
audit_logs
```

Critical business rows carry `tenant_id`, creator context, status, and
timestamps. Document deletion is soft by default. Vector storage defaults toward
PostgreSQL + pgvector for the enterprise path, with lighter adapters available
for local tests.

## Observability

Request and workflow logs are structured around:

```text
request_id
trace_id
user_id
tenant_id
latency
model
token_usage
retrieval_top_k
rerank_score
tool_calls
status
error_code
```

Logs must avoid API keys, bearer tokens, raw object-store keys, sensitive local
paths, and full confidential document text.

## Local Development

Install dependencies and run the backend:

```powershell
uv sync --dev
uv run alembic upgrade head
uv run fastapi dev apps/api/main.py
```

Run the frontend:

```powershell
cd apps/web
npm install
npm run dev
```

Run the core local stack:

```powershell
docker compose --env-file .env -f docker/compose.yaml up -d --build postgres redis minio migration api web worker-ingestion worker-embedding
```

See [Local Development](operations/local-development.md) for provider
configuration, worker queues, Docker profiles, compatibility-client setup, and
smoke checks.

## Evaluation and Tests

**Unit and integration tests:**

```powershell
uv run ruff check .
uv run pytest tests/unit
uv run pytest tests/integration
```

**RAG quality evaluation (RAGAS):**

```powershell
# Install eval dependencies
uv sync --group eval

# Run against a running API
python evaluation/evaluate.py \
  --api-url http://localhost:8000 \
  --dataset evaluation/dataset/sample.json \
  --output evaluation/reports/

# Benchmark multiple configs
python evaluation/benchmark.py \
  --api-url http://localhost:8000 \
  --dataset evaluation/dataset/sample.json \
  --configs default,high_recall
```

Evaluated metrics: **Context Precision**, **Context Recall**, **Faithfulness**,
**Answer Relevancy**. Reports output as JSON (API-compatible) and Markdown.

See [Evaluation Guide](evaluation.md) for dataset format, metric definitions,
and CI integration.

## Current Limits and Roadmap

Near-term limits:

- production SSO is not complete
- formal eval editing UI is not complete
- assignment workflows are not complete
- multi-step planning remains bounded and conservative
- Milvus, Graph RAG, multi-agent orchestration, and complex web crawling are
  deferred
- production backup, restore, metrics dashboards, and deployment hardening need
  further work

The current priority remains a trusted enterprise RAG loop: ingestion,
retrieval, citation, RBAC, eval, observability, and review governance before
broader autonomous Agent features.
