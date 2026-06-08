# Local Development

The current project state verifies the Python project skeleton, unified response
envelope, API health/readiness endpoints, Docker Compose dependency stack, and
empty worker queue startup.

Use:

```powershell
uv sync
uv run pytest
uv run ruff check .
uv run mypy apps packages tests
```

Run the API locally with FastAPI tooling when needed:

```powershell
uv run fastapi dev apps/api/main.py
```

Useful local checks:

```text
GET /health
GET /ready
```

Both endpoints return the shared response envelope:

```json
{
  "request_id": "...",
  "data": {},
  "error": null,
  "metadata": {
    "latency_ms": null
  }
}
```

`/ready` 会对已配置的 PostgreSQL、Redis 和 MinIO 执行 live probe。未配置的依赖
保持 `not_configured`，不会阻塞本地 Python 测试或应用 import。已配置依赖失败时
返回 HTTP 503 和 `ready=false`，只包含 dependency name、status、latency 和 error_code。
响应和日志不得包含 `DATABASE_URL`、`REDIS_URL`、MinIO 凭据、bearer token、
API key、文档内容或本机绝对路径。

每次 `/ready` 调用都会写入 `api.readiness.checked`，字段包含 request ID、
`ready` 以及 dependency name/status/configured/latency/error_code。

## Docker Compose 本地依赖栈

准备本地环境变量：

```powershell
Copy-Item .env.example .env
```

然后替换 `.env` 中的占位值。`.env` 不可提交。容器配置中使用服务名 DNS：

```text
postgres
redis
minio
```

校验 Compose 配置：

```powershell
docker compose -f docker/compose.yaml config
```

启动本地栈：

```powershell
docker compose -f docker/compose.yaml up -d --build postgres redis minio migration api worker-ingestion worker-embedding
```

`migration` 服务执行：

```powershell
uv run --no-sync alembic upgrade head
```

API 容器执行：

```powershell
uv run --no-sync uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

从宿主机验证 API：

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8000/ready
```

### Optional Open WebUI Compose Profile

Open WebUI is available as an optional Docker Compose profile for local demos.
The default backend stack does not start it and tests do not require an Open
WebUI container.

Prepare local secrets:

```powershell
Copy-Item .env.example .env
$serviceToken = "replace-with-local-openwebui-provider-key"
$tokenHash = (.venv\Scripts\python.exe -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" $serviceToken)
```

Put the plaintext value only in the Open WebUI provider variable:

```text
OPENWEBUI_PROVIDER_API_KEY=replace-with-local-openwebui-provider-key
```

Put only the hash in the backend mapping:

```text
OPENWEBUI_SERVICE_TOKEN_HASHES_JSON=[{"token_sha256":"<sha256_of_openwebui_provider_api_key>","user_id":"openwebui-service","tenant_id":"tenant-local","roles":["openwebui"],"department":"platform","permissions":["document:read","retrieval:query"]}]
```

Keep the mapped permissions at `document:read` and `retrieval:query` for the
local demo. Do not grant `document:manage`, `agent:*`, wildcard permissions, or
cross-tenant access to the Open WebUI service token.

Validate and start the profile:

```powershell
docker compose -f docker/compose.yaml --profile open-webui config
docker compose -f docker/compose.yaml --profile open-webui up -d --build postgres redis minio migration api worker-ingestion worker-embedding open-webui
```

Open WebUI in the browser:

```text
http://127.0.0.1:3000
```

Container-to-container OpenAI-compatible base URL:

```text
http://api:8000/v1
```

Host curl base URL:

```text
http://127.0.0.1:8000/v1
```

Verify model discovery from the host:

```powershell
curl.exe http://127.0.0.1:8000/v1/models `
  -H "Authorization: Bearer <openwebui-provider-api-key>" `
  -H "X-Request-ID: req-openwebui-profile-1" `
  -H "X-Trace-ID: trace-openwebui-profile-1"
```

`open-webui` waits for the API healthcheck through Compose `depends_on`.
Readiness and auth failures return safe summaries only: dependency
name/status/latency/error_code or stable auth errors. They must not expose
database URLs, Redis URLs, MinIO credentials, JWT secrets, service tokens,
provider API keys, SQL, prompts, chunk content, provider raw responses, object
keys, local paths, or container paths.

Open WebUI persists provider settings in `open-webui-data`. If the volume was
initialized with an older base URL or key, update the provider configuration in
the UI or intentionally reset that volume. The local default image can use
`ghcr.io/open-webui/open-webui:main`; production deployments should pin an
explicit image version.

停止容器但保留数据：

```powershell
docker compose -f docker/compose.yaml down
```

只有在明确需要重置 PostgreSQL、Redis 和 MinIO 数据时，才删除本地 volume：

```powershell
docker compose -f docker/compose.yaml down -v
```

## Worker 队列

`worker-ingestion` 和 `worker-embedding` 使用同一个 worker 镜像，但队列名不同：

```text
worker-ingestion: WORKER_QUEUE_NAME=ingestion
worker-embedding: WORKER_QUEUE_NAME=embedding
```

Worker 使用 RQ 和 JSON serialization。队列 payload 必须是只包含 ID 和 JSON 可序列化
摘要参数的小 DTO。不要入队 file handle、SQLAlchemy model、`AuthContext`、文档全文、
prompt、token、API key、MinIO 凭据或本机绝对路径。
payload 会保留 `request_id` 和 `trace_id`，用于把异步 parser audit/log 关联回原始上传请求。

`worker-ingestion` 的 document ingestion job 先校验 payload 只包含
`document_id` 和 `version_id`，然后委托 ingestion parser application service。parser
service 会 tenant-scoped 读取 `ingestion_jobs` 和 `document_versions`，从
`ObjectStorage.get_document()` 读取 raw object，按 `source_type` 选择 parser，并把
job 状态推进到：

```text
parsing
parsed
failed_terminal
failed_retryable
```

本阶段支持 Markdown/TXT/PDF/DOCX parser。PDF parser 按非空文本页生成 section，
`page_start` 和 `page_end` 使用 1-based 页码；DOCX parser 通过 `Title`、`Heading 1`
到 `Heading 9` 维护 `title_path`，且不伪造页码，`page_start/page_end` 保持为空。
OCR、表格结构化、chunker、embedding、retrieval 和 RAG 不在当前 parser 阶段执行。
cleaner 和 dedup 已作为 `packages.ingestion` 中的纯组件实现，但当前 parse job 仍只记录
`parsed` 安全摘要；后续 chunker 阶段应按 `parse -> clean -> dedup -> chunk` 串联。

本地验证 cleaner/dedup：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ingestion/test_cleaner.py tests/unit/ingestion/test_dedup.py
.venv\Scripts\python.exe -m pytest tests/unit/ingestion
```

Cleaner/dedup metadata 只记录安全计数和稳定 checksum，例如 cleaned section 数、空 section
移除数、重复 section 数和 dedup 后 section 数。不要在日志、audit、数据库 metadata 或测试
fixture 中写入被删除页眉页脚、重复正文、企业真实文档内容、prompt、token、API key 或本机绝对路径。

本地验证 FixedSizeChunker：

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ingestion/test_fixed_size_chunker.py
.venv\Scripts\python.exe -m pytest tests/unit/ingestion
```

FixedSizeChunker 当前是 `packages.ingestion` 中的纯组件，用于
`parse -> clean -> dedup -> chunk` 的 `Section -> Chunk` 阶段。默认 token 目标范围
为 500 到 800，overlap 只接受 10% 到 20%，默认 15%。Chunk metadata 保留
`tenant_id`、`document_id`、`version_id`、`source_type`、`source_uri`、`acl`、
`title_path`、`section_ids`、`page_start/page_end`、`token_count` 和稳定 checksum。
异常 details 只允许 document/version/section/reason 这类安全摘要；不得包含 chunk
正文、被删除正文、prompt、token、API key、MinIO 凭据或本机绝对路径。

Chunk persistence now uses the storage DTO `ChunkRecord` and the `chunks` table
after chunking. Repository methods remain tenant-scoped and return DTOs, not
SQLAlchemy models. `document_versions.metadata.chunk_artifact_summary` may
record only safe aggregates such as `chunk_count`, token min/max, and checksum
summaries. Do not store chunk content, prompt-like text, removed text, tokens,
secrets, or local absolute paths in logs, audit metadata, job metadata, or
version metadata.

本地验证 chunk migration 和 repository：

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/storage/test_alembic_migrations.py
.venv\Scripts\python.exe -m pytest tests/integration/storage/test_document_repositories.py
```

## Embedding Fake Provider and Job Stage

Embedding is a separate provider-backed stage after chunk persistence. Local
development defaults to a deterministic fake provider:

```text
EMBEDDING_PROVIDER=fake
EMBEDDING_MODEL=fake-embedding
EMBEDDING_DIM=8
EMBEDDING_TIMEOUT_SECONDS=10
EMBEDDING_RETRY_BUDGET=2
EMBEDDING_QUEUE_NAME=embedding
VECTOR_STORE_TYPE=fake
VECTOR_INDEX_DIM=8
VECTOR_DISTANCE_METRIC=cosine
```

The fake provider performs no network, external API, or local model process
call. It returns deterministic vectors for tests and supports configured
failure modes in unit tests. Real provider adapters must be introduced behind
`packages.embeddings.ports.EmbeddingProvider`; business services must continue
calling only the port.

The embedding worker validates an ID-only payload with `job_type =
embedding.embed_document`, reconstructs `AuthenticatedRequestContext`, then
delegates to `EmbeddingJobService`. The service reads active chunks from the
same tenant/document/version scope, calls the provider once, maps the returned
vectors to `VectorRecord`, and writes them through the configured
`VectorStore`. It records only safe summaries: provider/model/version/dim,
chunk count, vector count, token min/max, usage counts, attempt_count, retry
timestamps, latency, status, and stable error codes. It does not put complete
vectors, chunk content, provider raw responses, prompts, tokens, API keys, or
local absolute paths into queue payloads, logs, audit, job metadata, or version
metadata.

Status progression:

```text
chunked -> embedding -> embedded
embedded + indexed vector summary -> retrieval_ready
embedding -> failed_retryable
embedding -> failed_terminal
```

`embedded` means provider vectors were generated and vector upsert completed
for this stage. `retrieval_ready` is set only after repository validation sees
active chunks, an embedded job, `index_status=indexed`, and a vector count that
matches active chunk count.

Document lifecycle APIs for local admin smoke tests:

```text
GET /documents/{document_id}/versions/{version_id}/status
DELETE /documents/{document_id}
DELETE /documents/{document_id}/versions/{version_id}
```

They require `document:manage`, return the shared envelope, and expose only safe
status summaries. Delete is soft-delete only: documents, versions, chunks, and
vector records move to `deleted`; raw object storage files are retained.

Local validation:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/embeddings tests/unit/data/test_embedding_queue_payload.py
.venv\Scripts\python.exe -m pytest tests/integration/storage tests/integration/worker
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy apps packages tests
```

## Retrieval Local Checks

Dense and sparse candidate retrieval share the same `RetrievalService` entry
point and `CandidateRetriever` port. Business code passes a full
`RetrievalRequest` and an AuthContext-derived `RetrievalFilterSet`; do not call
vector stores, PostgreSQL full text search, OpenSearch, LLMs, embeddings, or
prompt builders from API routes.

Sparse retrieval currently uses `packages.retrieval.PostgresSparseRetriever`.
The production MVP path is PostgreSQL full text search over active chunks with
a PostgreSQL-only GIN expression index. Local unit tests use deterministic fake
or SQLite fallback paths and do not require real PostgreSQL, OpenSearch,
network access, LLM APIs, or embedding APIs.

Sparse query-stage filters must include tenant, ACL, request metadata,
`status='active'`, `deleted_at IS NULL`, `include_deleted=False`, `top_k`, and
score threshold. Request metadata may narrow scope only. Private ACL entries
without matching allow lists are denied by default, and `denied_users` wins over
allow rules.

Sparse candidates expose citation metadata only: document/version/chunk IDs,
safe source display metadata, page range, title path, tenant, ACL, safe
metadata, score, and `retrieval_method="sparse"`. They must not include chunk
content, SQL text, tsquery/tsvector data, full query text, secrets, tokens, or
local absolute paths.

Hybrid retrieval is available through `packages.retrieval.HybridRetriever`.
It receives injected dense and sparse `CandidateRetriever` implementations and
uses `RRFMerger` for reciprocal-rank fusion. Branch requests clear
`score_threshold`; final filtering happens after normalized fusion scoring, so a
strong dense-only or sparse-only candidate is not removed before merge. The
merged candidate uses `retrieval_method="hybrid"` and keeps safe provenance in
`metadata["retrieval_provenance"]`.

RRF defaults:

```text
raw_rrf_score = sum(weight(method) / (rank_constant + rank))
rank starts at 1
rank_constant = 60
dense_weight = 1.0
sparse_weight = 1.0
```

`RetrievalCandidate.score` is normalized to 0..1 for compatibility with
`RetrievalRequest.score_threshold`. Hybrid provenance and in-memory merge trace
may include method names, ranks, scores, contributions, safe counts, thresholds,
and config values only. Do not include full query text, chunk content, SQL,
tsquery/tsvector data, vectors, embeddings, provider raw responses, secrets,
tokens, or local absolute paths.

Rerank is available through `packages.retrieval.RerankingRetriever`, which wraps
an injected upstream `CandidateRetriever` plus an injected `Reranker` port.
Local tests use `FakeReranker`; it is deterministic and does not call real
rerank models, LLM APIs, embedding APIs, OpenSearch, network services, or
production PostgreSQL.

`RerankConfig.failure_policy` controls degradation. `fallback` keeps upstream
ordering and scores, annotates `metadata["rerank_provenance"]` with
`score_source="fallback_upstream"`, and records `RETRIEVAL_RERANK_DEGRADED`.
`fail_closed` raises a stable `RetrievalError`. Rerank trace and error details
may include request/trace IDs, tenant/user IDs, top_k, provider/model, latency,
safe counts, ranks, pre-rerank score, rerank score, and stable error codes only.
They must not include full query text, chunk content, SQL, tsquery/tsvector
data, vectors, embeddings, provider raw responses, secrets, tokens, or local
absolute paths.

`POST /retrieve` is available for authorized retrieval smoke checks. In local
development, enable dev auth headers and keep fake embedding/provider defaults
unless a real provider adapter has been implemented:

```powershell
$env:APP_ENV = "local"
$env:ENABLE_DEV_AUTH_HEADERS = "true"
$env:EMBEDDING_PROVIDER = "fake"
$env:VECTOR_STORE_TYPE = "pgvector"
```

Example request:

```powershell
curl.exe -X POST http://127.0.0.1:8000/retrieve `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-retrieve-local-1" `
  -H "X-Trace-ID: trace-retrieve-local-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read" `
  -d "{\"query\":\"leave policy\",\"top_k\":5,\"metadata_filter\":{},\"score_threshold\":0.1}"
```

The response uses the shared envelope and returns citation-safe candidates only:
document/version/chunk IDs, `source_display_name`, `source_type`, page range,
title path, score, retrieval method, tenant, ACL, and safe provenance metadata.
It must not return chunk content or raw `source_uri`.

`retrieval_logs` stores replay-safe summaries for success and failure paths.
For PostgreSQL local checks:

```sql
SELECT request_id, trace_id, tenant_id, user_id, status, latency_ms,
       top_k, result_count, rerank_score, error_code, query_summary, metadata
FROM retrieval_logs
WHERE tenant_id = 'tenant-local-1'
ORDER BY created_at DESC
LIMIT 20;
```

Log and audit metadata may include dense/sparse counts, RRF fusion summary,
rerank status/score/latency, and candidate IDs only. They must not contain full
query text, chunk content, SQL, tsquery/tsvector data, vectors, embeddings,
provider raw responses, secrets, tokens, or local absolute paths.

Local sparse verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_sparse.py
.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_rrf.py
.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_rerank.py
.venv\Scripts\python.exe -m pytest tests/integration/api/test_retrieve_routes.py
.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_retrieve_application.py
.venv\Scripts\python.exe -m pytest tests/integration/storage/test_retrieval_log_repositories.py
.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth
.venv\Scripts\python.exe -m pytest tests/integration/storage/test_alembic_migrations.py
```

Retrieval eval smoke is also available for local regression. It uses synthetic
fixtures and a fixture-backed local retriever, then passes candidates through
`RetrievalService` so tenant, metadata, ACL, score-threshold, and top_k guards
stay covered by the same production contract. The default path does not call
real external LLM, embedding, rerank, OpenSearch, network services, Docker,
Redis, MinIO, pgvector, or production PostgreSQL.

Run the smoke runner:

```powershell
.venv\Scripts\python.exe -m tests.eval.retrieval.run_smoke --dataset tests/eval/datasets/retrieval_smoke.json --report-dir tests/eval/reports
```

Run the eval tests explicitly because `pyproject.toml` keeps default pytest
collection scoped to `tests/unit` and `tests/integration`:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/eval tests/eval
```

Reports are written to `tests/eval/reports/` by default. Summary metrics include
`case_count`, `passed_count`, `failed_count`, `retrieval_hit_rate`,
`acl_isolation_passed`, `no_answer_passed`, `prompt_injection_passed`,
`average_latency_ms`, and top_k summary. Per-case rows include request/trace
IDs, tenant/user IDs, top_k, latency, pass/fail state, failure stage, and matched
document/chunk IDs only. Reports must not contain full query text, chunk
content, SQL, tsquery/tsvector data, vectors, embeddings, provider raw
responses, secrets, tokens, or local absolute paths.

RAG eval dataset smoke is separate from retrieval eval. The dataset lives at
`tests/eval/datasets/rag_smoke.json` and contains synthetic RAG cases plus a
synthetic corpus for later citation, no-answer, ACL isolation, prompt-injection,
and answer-quality regression. Story 5.1 only validates the typed dataset,
corpus shape, coverage counts, safe error details, and safe report serialization.
It does not execute a full RAG quality runner, does not call `/query` or
`/chat`, does not use LLM-as-judge scoring, and does not add a CI gate.

Run the RAG dataset smoke:

```powershell
.venv\Scripts\python.exe -m tests.eval.rag.run_dataset_smoke --dataset tests/eval/datasets/rag_smoke.json --report-dir tests/eval/reports
```

RAG dataset reports are also written to `tests/eval/reports/` by default.
Summary metrics include `case_count`, `answerable_count`, `no_answer_count`,
`acl_case_count`, `prompt_injection_case_count`, `citation_expected_count`, and
`dataset_version`. Per-case rows include case/category IDs, tenant/user IDs,
top_k, expected document/chunk/citation IDs, and safe flags only. Reports,
errors, stdout, and logs must not include query text, answer expectation text,
chunk content, prompts, SQL, vectors, embeddings, provider raw responses,
secrets, tokens, object keys, or local absolute paths.

RAG quality runner is the Story 5.2 full local RAG eval path. It uses the same
`rag_smoke.json` dataset, but executes each case through production components:
`RetrievalService`, `RetrievalCandidateHydrator`, `ContextPacker`,
`PromptBuilder`, `RagGenerationService` with a local fake provider, and
`CitationExtractor`. It does not call `/query` or `/chat`, and it does not use
real OpenAI, Qwen, DeepSeek, vLLM, Ollama, embedding APIs, rerank APIs,
OpenSearch, PostgreSQL, pgvector, Redis, MinIO, Docker, HTTP APIs, network
services, or production databases.

Run the RAG quality runner:

```powershell
.venv\Scripts\python.exe -m tests.eval.rag.run_smoke --dataset tests/eval/datasets/rag_smoke.json --report-dir tests/eval/reports
```

Run the CI/local RAG eval smoke gate:

```powershell
.venv\Scripts\python.exe -m tests.eval.rag.run_ci_smoke --dataset tests/eval/datasets/rag_smoke.json --config tests/eval/config/rag_smoke_gate.json --report-dir tests/eval/reports
```

The gate reuses Story 5.2 `run_rag_eval()` and does not duplicate retrieval,
hydration, context packing, prompt build, generation, or citation extraction
logic. Thresholds live in `tests/eval/config/rag_smoke_gate.json`; adjust that
file for MVP calibration instead of changing production code. The default
thresholds are retrieval hit rate >= 0.80, citation coverage >= 0.90,
no-answer correctness >= 0.85, ACL isolation and prompt-injection checks must
pass, and `failed_count <= 0`.

Exit code meanings:

```text
0 pass
1 threshold or case failure
2 dataset or gate config validation error
3 unexpected safe runner error
```

Reports are written to `tests/eval/reports/` by default and use
`rag-ci-smoke-{timestamp}-{uuid}.json` filenames so repeated runs do not
overwrite each other. Reports contain generated time, commit, branch, dataset
summary, threshold config summary, runner summary, failed case IDs, and failure
stages. Stdout includes a compact safe JSON summary and only the report
filename, not the local absolute path.

Failure output is intentionally narrow: it can show safe metric names,
expected/actual metric values, failed case IDs, failure stages, and the report
filename. It must not include full query text, generated answers, chunk content,
prompts, SQL, vectors, embeddings, provider raw responses, secrets, tokens,
cookies, object keys, local absolute paths, or real enterprise data.

Quality reports include `case_count`, `passed_count`, `failed_count`,
`retrieval_hit_rate`, `citation_coverage`, `no_answer_correctness`,
`acl_isolation_passed`, `prompt_injection_passed`, and `average_latency_ms`.
Per-case rows include request/trace IDs, tenant/user IDs, top_k, latency,
failure stage, matched document/chunk/citation IDs, retrieval/context/citation
counts, unsupported and forged-reference counts, prompt-risk count, and safe
generation provider/model/token usage summaries only.

Known limitations: this gate is synthetic and local only. Real provider/API
eval, LLM-as-judge faithfulness scoring, faithfulness dashboards, long-term
trend storage, and Docker Compose dependent eval are outside this story.

## RAG Context Packing Local Checks

Context packing is available in `packages/rag` as pure domain code. It receives
explicit `ContextCandidate` DTOs from a future RAG application service after
retrieval authorization and chunk-content resolution. Do not pass SQLAlchemy
models, API schemas, raw dict rows, vector-store results, or storage models
directly into the packer.

Local unit validation:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/rag/test_context_packer.py
.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth
```

The packer re-checks tenant and ACL permissions, sorts by final score,
deduplicates chunk identities, applies token budget, drops oversized or
over-budget candidates according to config, merges adjacent chunks only within
the same tenant/document/version/title path/ACL boundary, and includes
parent/child/neighbor context only from the explicit `related_chunks_by_id`
input map. It must not query PostgreSQL, vector stores, object storage, Redis,
OpenSearch, MinIO, network services, LLMs, embeddings, or tokenizer/model
packages.

Packed output can contain authorized chunk content because it is prompt-ready
context. Packing trace, drop records, errors, logs, metadata summaries, and test
reports must remain safe: IDs, counts, scores, token counts, budget, drop
reasons, merge summaries, and related-context counts only. They must not include
chunk content, full query text, prompts, SQL, vectors, embeddings, provider raw
responses, secrets, tokens, or local absolute paths.

## RAG Prompt Builder Local Checks

Prompt building is available in `packages/rag` as pure domain code after
context packing. It receives `PromptBuildRequest` with typed `PackedContext` and
returns structured `PromptMessage` parts for the later generation stage:
system, security policy, citation policy, no-answer policy, user question, and
untrusted context. Do not pass SQLAlchemy models, API schemas, raw dict rows,
retrieval raw results, storage rows, vector-store objects, or provider responses
directly into the builder.

Local unit validation:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/rag/test_prompt_builder.py
.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth
```

The builder wraps each context item in explicit `<context_item ...>` boundaries
with `untrusted_content="true"`, keeps user question and context separate from
system policy, requires answers to use only the provided context, and instructs
the later model to use only real `PackedCitationSource` identifiers. Empty
context is handled as a no-answer prompt, while oversized query/context inputs
fail closed with stable domain errors.

Prompt trace, errors, logs, metadata summaries, and test reports must remain
safe: request/trace/tenant/user IDs, context counts, source chunk counts, input
lengths, prompt part counts, risk type names, and error codes only. They must
not include full query text, chunk content, full prompt, SQL, vectors,
embeddings, provider raw responses, secrets, tokens, or local absolute paths.

Current prompt-builder non-goals: citation extraction, `/query`, `/chat`, SSE
streaming, chat memory, Open WebUI inbound compatibility, real provider
adapters, citation eval, RAG answer eval, and CI smoke gates are not completed
by prompt building.

## RAG LLM Provider Local Checks

`packages/llm` is the provider-neutral generation boundary for local RAG
development. It defines typed `LLMMessage`, `GenerateRequest`,
`GenerateResponse`, `GenerateChunk`, safe token usage, safe generation metadata,
the `LLMProvider` protocol, stable provider errors, and a deterministic
`FakeLLMProvider`.

Local/test configuration is fake-first:

```powershell
$env:LLM_PROVIDER = "fake"
$env:LLM_MODEL = "fake-llm"
$env:LLM_TIMEOUT_SECONDS = "10"
$env:LLM_RETRY_BUDGET = "2"
$env:LLM_FAKE_RESPONSE_TEXT = "Fake LLM response."
```

Run local unit validation with:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/llm tests/unit/rag/test_generation.py
```

`RagGenerationService` maps `PromptBuildResult.messages` to typed LLM messages,
verifies request/trace/tenant/user identity against
`AuthenticatedRequestContext`, and calls an injected provider. Generation
metadata must remain safe: request ID, trace ID, tenant ID, user ID,
provider/model/version, usage counts, latency, finish reason, chunk/token
counts, and error code only. It must not include prompt text, context content,
full user query, provider raw responses, API keys, access tokens, bearer tokens,
SQL, vectors, embeddings, local absolute paths, or file contents.

Still out of scope here: real OpenAI/Qwen/DeepSeek/vLLM/Ollama adapters,
`/chat`, SSE streaming, chat memory, OpenAI-compatible inbound routes, and RAG
answer eval.

## RAG Query Local Checks

`POST /query` is the non-streaming RAG answer endpoint. `POST /query/stream` is
the SSE streaming variant. Both use the same development auth headers as
`/retrieve`, the same permission gate, and the same application flow:
retrieval, chunk-content hydration, context packing, prompt build, fake LLM
generation, and citation extraction.

Local/test generation is fake-first:

```powershell
$env:LLM_PROVIDER = "fake"
$env:LLM_MODEL = "fake-llm"
$env:LLM_FAKE_RESPONSE_TEXT = "无法从给定上下文确认。"
```

Example request:

```powershell
curl.exe -X POST http://127.0.0.1:8000/query `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-query-local-1" `
  -H "X-Trace-ID: trace-query-local-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read" `
  -d "{\"query\":\"leave policy\",\"top_k\":5,\"metadata_filter\":{},\"score_threshold\":0.1}"
```

Streaming example:

```powershell
curl.exe -N -X POST http://127.0.0.1:8000/query/stream `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-query-stream-local-1" `
  -H "X-Trace-ID: trace-query-stream-local-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read" `
  -d "{\"query\":\"leave policy\",\"top_k\":5,\"metadata_filter\":{},\"score_threshold\":0.1}"
```

The stream uses standard SSE frames:

```text
event: citation
data: {"request_id":"...","trace_id":"...","event":"citation","citation":{...}}

event: token
data: {"request_id":"...","trace_id":"...","event":"token","index":0,"delta":"..."}

event: final
data: {"request_id":"...","trace_id":"...","event":"final","status":"success",...}
```

The `citation` and `final` SSE payloads use the same safe citation shape as
`/query` and `/chat`: `source_display_name`, `source_type`, document/version/
chunk IDs, page metadata, title path, retrieval method, and score. They do not
include raw `source_uri`.

`QueryResponse` contains request/trace/tenant/user IDs, answer text, structured
citations, `no_answer`, `unsupported_claims`, and safe metadata summaries for
retrieval, context, prompt risk, generation token usage, citation counts, and
latency. Citations use `source_display_name`, `source_type`, document/version/
chunk IDs, page range, title path, retrieval method, and score. They must not
contain prompt text, chunk content, full query text, raw `source_uri`, provider
raw responses, API keys, access tokens, SQL, vectors, embeddings, or local
absolute paths.

`/query/stream` emits `citation`, `token`, `error`, and `final` events. Tool
events `tool_call` and `tool_result` are reserved for later Agent stories and
are not emitted by RAG query streaming. The local fake LLM provider streams
deterministic deltas from `LLMProvider.stream()` followed by one final provider
response; tests do not call real OpenAI/Qwen/DeepSeek/Ollama/vLLM providers,
network, Docker, Redis, MinIO, or production databases.

Local validation:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/rag/test_citation_extractor.py tests/unit/rag/test_query_service.py tests/unit/rag/test_streaming.py
.venv\Scripts\python.exe -m pytest tests/integration/api/test_query_routes.py
.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/llm tests/unit/retrieval tests/unit/auth
```

## RAG Chat Local Checks

`POST /chat` and `POST /chat/stream` add tenant/user-scoped session memory on
top of the existing `/query` RAG path. They use the same dev auth headers and
`document:read` or `retrieval:query` permission gate. Local generation still
uses `FakeLLMProvider` unless a real provider adapter has been implemented.

Start a session:

```powershell
curl.exe -X POST http://127.0.0.1:8000/chat `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-chat-local-1" `
  -H "X-Trace-ID: trace-chat-local-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read,retrieval:query" `
  -d "{\"query\":\"leave policy\",\"top_k\":5,\"metadata_filter\":{},\"score_threshold\":0.1}"
```

Continue the session with the returned `session_id`:

```powershell
curl.exe -X POST http://127.0.0.1:8000/chat `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-chat-local-2" `
  -H "X-Trace-ID: trace-chat-local-2" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read,retrieval:query" `
  -d "{\"query\":\"What about carryover?\",\"session_id\":\"<session-id>\",\"top_k\":5}"
```

Streaming chat:

```powershell
curl.exe -N -X POST http://127.0.0.1:8000/chat/stream `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-chat-stream-local-1" `
  -H "X-Trace-ID: trace-chat-stream-local-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read,retrieval:query" `
  -d "{\"query\":\"leave policy\",\"session_id\":\"<session-id>\",\"top_k\":5}"
```

The `final` SSE payload includes `session_id`, `tenant_id`, `user_id`, answer,
citations, `no_answer`, unsupported claims, and safe metadata. Partial token
events are never persisted as separate chat messages; only the terminal final
assistant result is stored. Chat citations use safe source display metadata and
do not expose raw `source_uri`.

Local validation:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/memory tests/unit/rag
.venv\Scripts\python.exe -m pytest tests/integration/api/test_chat_routes.py tests/integration/api/test_query_routes.py
.venv\Scripts\python.exe -m pytest tests/integration/storage/test_chat_memory_repositories.py
```

## Open WebUI and Source Inspector Local Checks

Open WebUI can use this API as an OpenAI-compatible server. In Open WebUI,
configure an OpenAI-compatible connection with this base URL:

```text
http://127.0.0.1:8000/v1
```

When Open WebUI runs inside the optional Compose profile, configure the provider
base URL as the container network URL instead:

```text
http://api:8000/v1
```

For production-like Open WebUI smoke tests, configure the provider API key in
Open WebUI as a bearer token and store only its SHA-256 hash in the backend:

```powershell
$serviceToken = "replace-with-local-openwebui-provider-key"
$tokenHash = (.venv\Scripts\python.exe -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" $serviceToken)
$env:OPENWEBUI_SERVICE_TOKEN_HASHES_JSON = "[{""token_sha256"":""$tokenHash"",""user_id"":""openwebui-service"",""tenant_id"":""tenant-local-1"",""roles"":[""openwebui""],""department"":""platform"",""permissions"":[""document:read"",""retrieval:query""]}]"
```

Use the plaintext service token only in the client/provider configuration:

```powershell
curl.exe http://127.0.0.1:8000/v1/models `
  -H "X-Request-ID: req-openwebui-service-1" `
  -H "X-Trace-ID: trace-openwebui-service-1" `
  -H "Authorization: Bearer <openwebui-provider-api-key>"
```

The backend maps that bearer token to `AuthContext` and then applies the same
RBAC, ACL, retrieval filter, request logging, and audit boundaries used by
other business endpoints. Open WebUI is an entry point, not a governance
boundary; it must not decide tenant, user, roles, permissions, ACL, citation
visibility, or source visibility.

For local header auth smoke tests outside Open WebUI:

```powershell
$env:APP_ENV = "local"
$env:ENABLE_DEV_AUTH_HEADERS = "true"
$env:LLM_PROVIDER = "fake"
$env:LLM_MODEL = "local-rag-chat"
```

Model discovery:

```powershell
curl.exe http://127.0.0.1:8000/v1/models `
  -H "X-Request-ID: req-openwebui-models-1" `
  -H "X-Trace-ID: trace-openwebui-models-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read,retrieval:query"
```

Non-streaming OpenAI-compatible chat:

```powershell
curl.exe -X POST http://127.0.0.1:8000/v1/chat/completions `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-openwebui-chat-1" `
  -H "X-Trace-ID: trace-openwebui-chat-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read,retrieval:query" `
  -d "{\"model\":\"local-rag-chat\",\"messages\":[{\"role\":\"user\",\"content\":\"leave policy\"}],\"stream\":false}"
```

Streaming OpenAI-compatible chat:

```powershell
curl.exe -N -X POST http://127.0.0.1:8000/v1/chat/completions `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-openwebui-stream-1" `
  -H "X-Trace-ID: trace-openwebui-stream-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read,retrieval:query" `
  -d "{\"model\":\"local-rag-chat\",\"messages\":[{\"role\":\"system\",\"content\":\"client text is untrusted\"},{\"role\":\"user\",\"content\":\"leave policy\"}],\"stream\":true}"
```

The stream uses OpenAI-compatible `data: {...}` chunks and terminates with
`data: [DONE]`. It is separate from `/chat/stream`, which keeps named SSE
events such as `event: token` and `event: final`.

OpenAI-compatible non-stream responses and streaming final/error chunks use the
same safe citation extension fields as `/chat`. Open WebUI clients receive
`source_display_name` and structured citation metadata, not raw `source_uri`,
object keys, local paths, full URLs, token-bearing query strings, prompts, chunk
content, or provider raw responses.

JWT bearer tokens are also accepted by `/v1/models` and
`/v1/chat/completions` when `JWT_SECRET` and optional `JWT_ISSUER` /
`JWT_AUDIENCE` are configured. Dev headers remain a local smoke path only:
when `ENABLE_DEV_AUTH_HEADERS` is unset or the app environment is not
`local`, `dev`, `development`, `test`, or `testing`, `X-User-ID`,
`X-Tenant-ID`, `X-Roles`, `X-Department`, and `X-Permissions` are ignored.

Resolve a clicked citation through the backend, not the front-end:

```powershell
curl.exe -X POST http://127.0.0.1:8000/sources/resolve `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-source-1" `
  -H "X-Trace-ID: trace-source-1" `
  -H "X-User-ID: user-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read,retrieval:query" `
  -d "{\"document_id\":\"<document-id>\",\"version_id\":\"<version-id>\",\"chunk_id\":\"<chunk-id>\",\"citation_ref\":\"c1\"}"
```

`/sources/resolve` rechecks tenant, RBAC, ACL, soft delete, document/version
identity, chunk identity, and version visibility before returning an excerpt.
Denied, missing, deleted, inactive, and ACL-restricted references share the
same safe denial semantics. Successful responses return `source_display_name`,
`source_type`, document/version/chunk IDs, page range, title path, retrieval
method, score, request ID, trace ID, and an authorized excerpt. The response
does not include raw `source_uri`, object keys, bucket paths, local paths, full
URLs, full document text, prompts, provider raw responses, SQL, API keys, or
bearer tokens.

Admin status display uses:

```powershell
curl.exe http://127.0.0.1:8000/documents/<document-id>/versions/<version-id>/status `
  -H "X-Request-ID: req-status-1" `
  -H "X-Trace-ID: trace-status-1" `
  -H "X-User-ID: admin-local-1" `
  -H "X-Tenant-ID: tenant-local-1" `
  -H "X-Roles: knowledge_admin" `
  -H "X-Permissions: document:manage"
```

Status responses expose only stable safe fields: document/version/job IDs,
status, chunk/vector counts, embedding provider/model/version/dim, index
status, attempt count, retry timestamps, stable `error_code`, safe
`error_summary`, request ID, and trace ID. They remain `document:manage`
endpoints and must not be exposed as ordinary user document enumeration.

Open WebUI is an entry point, not a governance boundary. Tenant isolation,
RBAC, ACL, citation visibility, source visibility, prompt-injection defense,
and audit decisions are backend responsibilities.

Lightweight custom UI or sidecar scope for MVP is limited to upload, query,
citation chips, Source Inspector, job/status display, and diagnostics/eval
entry links. Source Inspector, Knowledge Admin, Diagnostics, Eval Reports, and
future Agent Review screens must support WCAG 2.2 AA basics: keyboard focus,
focus restoration for drawers/sheets, `aria-live` or alert regions for async
state, non-color-only status labels, and non-hover-only citation/source actions.
Long document, version, chunk, request, and trace IDs must wrap or truncate
with a way to read the full value. Do not enable "copy answer with sources"
until the terminal final event or metadata chunk has arrived.

Out of scope for this phase: full custom React/Next.js management console,
document previewer, Graph RAG, multi-agent UI, Tool Review UI, Open WebUI
function/tool bridge, `/v1/embeddings`, image/audio endpoints, real provider
adapters, Agent `tool_call`/`tool_result` events, conversation summarization
through an LLM, and RAG citation eval runner.

## Upload API Local Smoke

`POST /upload` 需要已配置 PostgreSQL、Redis 和 MinIO，并且 migration 已升级到
`head`。上传只完成 raw file/object metadata/job 创建和入队，不会同步执行 parser、
chunk、embedding 或 vector indexing。

本地开发 header：

```powershell
$env:APP_ENV = "local"
$env:ENABLE_DEV_AUTH_HEADERS = "true"
```

示例请求：

```powershell
curl.exe -X POST http://127.0.0.1:8000/upload `
  -H "X-Request-ID: req-upload-1" `
  -H "X-Trace-ID: trace-upload-1" `
  -H "X-User-ID: user-123" `
  -H "X-Tenant-ID: tenant-abc" `
  -H "X-Permissions: document:upload" `
  -F "file=@policy.txt;type=text/plain" `
  -F "source_type=txt" `
  -F "acl={\"visibility\":\"tenant\"}" `
  -F "metadata={\"department\":\"HR\"}"
```

成功响应包含 `document_id`、`version_id`、`job_id` 和 `status=uploaded`。
如需上传同一 document 的新版本，显式增加 `-F "document_id=<existing-document-id>"`
并使用 `X-Permissions: document:manage`；系统不会根据 `source_uri` 自动合并文档。
权限不足返回 `DOCUMENT_UPLOAD_FORBIDDEN` 和 HTTP 403，且不会写 object storage、
document metadata 或 queue job。非法 JSON metadata 返回
`DOCUMENT_UPLOAD_INVALID_METADATA`。上传大小由 `UPLOAD_MAX_BYTES` 控制。

上传后可在数据库中观察 parser 阶段：

```sql
select id, tenant_id, document_id, version_id, status, error_code, attempt_count, last_attempt_at
from ingestion_jobs
order by created_at desc;

select id, status, metadata
from document_versions
order by created_at desc;
```

成功解析后，`document_versions.metadata.parsed_artifact_summary` 只包含安全摘要：
`section_count`、`title_paths`、`stage`、`checksum`，以及 PDF 的 `page_count/page_ranges`
或 DOCX 的 `heading_count/page_metadata`。失败时 `ingestion_jobs`、
`document_versions` 和 `documents` 会同步进入 `failed_terminal` 或
`failed_retryable`，管理员应查看 `ingestion_jobs.error_code`，例如 `DOCUMENT_PARSE_EMPTY_CONTENT`、
`DOCUMENT_PARSE_ENCODING_FAILED`、`DOCUMENT_PARSE_UNSUPPORTED_TYPE` 或
`DOCUMENT_PARSE_FAILED`、`DOCUMENT_STORAGE_READ_FAILED`。这些状态和摘要可以用于观察
PDF/DOCX 从 `parsing` 到 `parsed` 或失败状态的流转，但日志、audit 和 version metadata
不得包含正文全文。

## Database Migrations and Storage Smoke

Configure the database URL through the environment:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://<db_user>:<db_password>@<db_host>:<db_port>/<db_name>"
```

Apply the foundational governance schema:

```powershell
uv run alembic upgrade head
```

Current migrations create:

```text
tenants
users
roles
user_roles
audit_logs
documents
document_versions
ingestion_jobs
chunks
embedding_jobs
vector_records
retrieval_logs
chat_sessions
chat_messages
```

Application startup must not call `Base.metadata.create_all()`. Alembic is the
schema source of truth.

Run the storage smoke tests without external PostgreSQL by using the SQLite
async test path:

```powershell
uv run pytest tests/integration/storage/test_alembic_migrations.py
uv run pytest tests/integration/storage/test_governance_repositories.py
uv run pytest tests/integration/storage/test_document_repositories.py
```

These tests validate common SQLite-compatible DDL, async repository calls, and
DTO mapping. 真实 PostgreSQL 启动和服务编排由 `docker/compose.yaml` 覆盖。

## Local Auth Context

Public health endpoints do not require authentication. Future business endpoints
must receive `AuthenticatedRequestContextDep`, which includes both request IDs and
an `AuthContext`.

Enable development auth headers explicitly for local manual testing:

```powershell
$env:APP_ENV = "local"
$env:ENABLE_DEV_AUTH_HEADERS = "true"
```

Then send:

```text
X-Request-ID: req-local-1
X-Trace-ID: trace-local-1
X-User-ID: user-123
X-Tenant-ID: tenant-abc
X-Roles: admin,knowledge_manager
X-Department: HR
X-Permissions: document:read,retrieval:query
```

Production must not trust development headers. Leave
`ENABLE_DEV_AUTH_HEADERS` unset or false outside explicit local/test workflows.
The API also requires `APP_ENV` to be `local`, `dev`, `development`, `test`, or
`testing` before it accepts these headers.

JWT bearer auth is verified, not decoded blindly. Configure a real secret and
optional validation fields through environment variables:

```powershell
$env:JWT_SECRET = "replace-with-local-test-secret-at-least-32-bytes"
$env:JWT_ALGORITHM = "HS256"
$env:JWT_ISSUER = "local-dev"
$env:JWT_AUDIENCE = "local-api"
```

Supported claims:

```json
{
  "sub": "user-123",
  "tenant_id": "tenant-abc",
  "roles": ["admin", "knowledge_manager"],
  "department": "HR",
  "permissions": ["document:read", "retrieval:query"],
  "exp": 1779854400
}
```

`user_id` can replace `sub`, and `scope` can provide a space-separated fallback
for permissions only when the `permissions` claim is absent. Tokens with both
`sub` and `user_id` must use the same value. Missing `user_id`, `tenant_id`, or
`exp` fails before application services are called and returns the shared
response envelope without exposing token contents or resource existence.

Open WebUI service tokens are configured by hash and mapped to the same
`AuthContext` DTO before policy checks:

```powershell
$env:OPENWEBUI_SERVICE_TOKEN_HASHES_JSON = "[{""token_sha256"":""<sha256-of-provider-api-key>"",""user_id"":""openwebui-service"",""tenant_id"":""tenant-local-1"",""roles"":[""openwebui""],""department"":""platform"",""permissions"":[""document:read"",""retrieval:query""]}]"
```

If `permissions` is omitted, the backend grants only `document:read` and
`retrieval:query`. Do not put the plaintext service token in README, docs,
logs, audit metadata, test snapshots, query parameters, prompts, metadata
filters, or frontend authorization logic.

## Structured Logs

Local API startup configures `structlog` JSON output. Set log level through:

```powershell
$env:LOG_LEVEL = "INFO"
```

Each request writes one `api.request.completed` event. To observe it locally,
start the API and call a route:

```powershell
uv run fastapi dev apps/api/main.py
curl.exe -H "X-Request-ID: req-local-1" http://127.0.0.1:8000/health
```

The request log includes `request_id`, `trace_id`, `tenant_id`, `user_id`,
`session_id`, `method`, `path`, `status_code`, `latency_ms`, `error_code`,
`role_count`, and `permission_count`.
Public `/health` and `/ready` logs keep `tenant_id` and `user_id` as null.

Logs must not contain request bodies, response bodies, full query strings,
prompts, document contents, bearer tokens, API keys, passwords, credentials, or
other secrets. Shared redaction covers case-insensitive keys including
`authorization`, `access_token`, `api_key`, `token`, `secret`, `password`, and
`credential`, plus prompt, document, body, tool-argument, cookie, and local-path
fields.

## Error and Audit Foundation

Expected domain errors return the shared envelope with stable `error.code`,
`error.message`, and redacted `error.details`. Unexpected exceptions return the
generic `INTERNAL_ERROR` envelope and do not expose class names, tracebacks, or
secret values in the response.

Application services can record audit events through the `AuditPort` Protocol in
`packages.common.audit`. The current adapter is in-memory for tests and stores
structured `action`, `resource`, `tenant_id`, `user_id`, `status`,
`latency_ms`, and `error_code` fields with redacted metadata. Story 1.5 adds
`SqlAlchemyAuditPort` and `AuditLogRepository`; both reuse the shared redaction
rules before writing audit metadata to `audit_logs`.

Docker Compose 和真实外部依赖 readiness 已由 Story 1.6 实现。后续 ingestion、
embedding、retrieval 和 agent job 必须继续遵守同一套配置、权限和 payload 边界。
