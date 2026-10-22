# Optional LangChain components and LangGraph orchestration

AegisRAG keeps its own authorization, evidence, DTOs, provider ports and tool
governance. Optional adapters integrate actual framework components without
replacing those boundaries. The default installation and configuration use the
native implementations and do not import LangChain, LangGraph or LangSmith.

## Installation and independent selection

```bash
# Existing native installation
uv sync --dev --frozen

# Install either optional integration, or both
uv sync --dev --frozen --extra langchain --extra langgraph
```

Three independent adapter/backend switches select implementations. A fourth
setting chooses the model decision format:

| Setting | Default | Optional value | Scope |
| --- | --- | --- | --- |
| `LLM_ADAPTER` | `native` | `langchain` | `LLMProvider.generate/stream` |
| `EMBEDDING_ADAPTER` | `native` | `langchain` | API retrieval and embedding worker |
| `AGENT_ORCHESTRATION_BACKEND` | `native` | `langgraph` | Governed Agent scheduling |
| `AGENT_DECISION_MODE` | `json` | `native_tools` | Single model decision, independent of scheduler |

`LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` and their
version fields retain their existing meanings. An adapter name is not a model
provider identity. Unknown adapter names fail configuration; the LangChain
adapter requires one of the existing OpenAI-compatible provider selections.
Selecting LangGraph alone works with the default fake LLM for offline use.
`AGENT_DECISION_MODE=native_tools` requires `LLM_ADAPTER=langchain` and an endpoint
that supports native tool calling; it works with either orchestration backend.

Example for an already configured OpenAI-compatible endpoint:

```dotenv
LLM_ADAPTER=langchain
LLM_PROVIDER=openai_compatible
LLM_MODEL=your-chat-model
LLM_BASE_URL=https://your-model-service.example/v1
# Supply LLM_API_KEY through your deployment's secret management.

EMBEDDING_ADAPTER=langchain
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_BASE_URL=https://your-model-service.example/v1
EMBEDDING_DIM=768
VECTOR_INDEX_DIM=768
# Supply EMBEDDING_API_KEY if that endpoint requires authentication.

AGENT_ORCHESTRATION_BACKEND=langgraph
```

Install the LangChain extra in both the API and embedding-worker environments
when changing `EMBEDDING_ADAPTER`. Existing Dockerfiles install native defaults;
an optional-framework image must include the selected extras in its build-time
`uv sync --frozen --no-dev` command. Setting an environment variable alone does
not install dependencies. Missing extras produce an actionable startup/factory
error rather than silently falling back to native behavior.

### Docker Compose runtime options

From the repository root, use `--env-file .env -f docker/compose.yaml` when
invoking Compose. The env file supplies interpolation values; it is not copied
into images or passed wholesale to containers. The shared API environment
explicitly forwards the adapter/backend switches and these runtime settings to
the API, both workers, migration and opt-in bootstrap services:

| Setting | Default |
| --- | --- |
| `AGENT_DECISION_MODE` | `json` |
| `AGENT_DURABLE_ENABLED` | `false` |
| `AGENT_DURABLE_RETENTION_SECONDS` | `86400` |
| `AGENT_DURABLE_LEASE_SECONDS` | `30` |
| `AGENT_DEFAULT_MAX_STEPS` | `8` |
| `AGENT_DEFAULT_MAX_TOOL_CALLS` | `5` |
| `AGENT_DEFAULT_TIMEOUT_SECONDS` | `30` |
| `AGENT_PERSISTENCE_PHASE_TIMEOUT_SECONDS` | `5` |
| `AGENT_AUXILIARY_IO_TIMEOUT_SECONDS` | `1` |
| `RAG_AUXILIARY_IO_TIMEOUT_SECONDS` | `1` |
| `AGENT_REPEATED_ACTION_THRESHOLD` | `3` |

All durations are seconds. Missing or empty interpolation values retain these
defaults. Setting a value in the host env file only reaches a container when its
environment mapping includes that setting. Runtime settings do not install the
optional framework dependencies described above. Durable execution remains
opt-in and requires the [migration and budget prerequisites](operations/DURABLE_AGENT.md).
The [persistence-phase](operations/AGENT_PERSISTENCE_PHASES.md),
[Agent auxiliary I/O](operations/AGENT_AUXILIARY_IO.md) and
[RAG audit](operations/RAG_AUXILIARY_AUDIT.md) budgets retain their independent
scope; none is a new whole-request deadline.

Offline coverage in `tests/unit/test_compose_agent_runtime_options.py` checks
YAML inheritance, declared defaults and a bounded missing/empty/custom-value
projection. It does not execute Docker Compose, build images or validate a
running deployment.

To return to native behavior, set the relevant adapter/backend variable to
`native`. Do not reuse an embedding index after changing model/version/dimension;
the existing embedding provenance and index-compatibility rules still apply.

## Component boundaries

- `packages/llm/adapters/langchain.py` adapts `ChatOpenAI.ainvoke/astream` to
  `GenerateResponse` and `GenerateChunk`. Framework messages never leave it
- `packages/embeddings/adapters/langchain.py` adapts
  `OpenAIEmbeddings.aembed_documents` to ordered `EmbeddingVector` DTOs, preserving
  chunk IDs and configured provider/model/version identity
- API factories and the embedding-worker factory select implementations;
  application services continue to depend on their existing ports
- `VectorStore`, `CandidateRetriever`, `Reranker`, retrieval filters, hydration,
  citation extraction and audit services are unchanged. There is no generic
  `Framework.run` interface and no alternative unfiltered retrieval path

The standard `generate`/`stream` contract supports text-only OpenAI-compatible
chat. Native tool calling uses a separate `DecisionProvider.decide` boundary;
text generation still rejects tool/multimodal responses. There is no arbitrary
framework agent executor, multimodal generation, callback forwarding, external
tracing or shared framework cache. Endpoint compatibility still needs
deployment-specific validation; not every server implements the same optional
stream-usage fields. `GenerateRequest.stream_options["include_usage"] = False`
can disable usage requests for an endpoint that does not support them.

Embedding tokenization, automatic splitting, averaging and hidden tokenizer
downloads are disabled. Raw response indices are validated and ordered before
LangChain drops them; batch count, dimensions and finite numeric values are
checked again at the DTO boundary. LangChain's public embedding interface does
not expose token usage, so the adapter explicitly records it as unavailable.
The configured dimension is validated rather than silently sent as an optional
vendor dimension-reduction parameter.

Provider retry ownership stays with AegisRAG: SDK retries are disabled and there
are at most `retry_budget + 1` attempts. As with native providers, each attempt
has `timeout_seconds`; the Agent runtime independently enforces its overall
deadline. Streaming retries are allowed only before any visible text. Consumer
close and caller cancellation close the cooperative SDK stream and propagate
cancellation without producing a final success chunk. An arbitrary injected
third-party component that suppresses cancellation is outside the supported
provider contract.

## Governed framework exports

These are real LangChain extension points, bound to one authenticated application
context at construction. They never accept a tenant/user override from invocation
input or Runnable config:

- `packages.retrieval.adapters.langchain.AegisRetriever` is a `BaseRetriever`
  over `RetrieveApplicationService`. Its `Document` values carry canonical
  citation identity, safe source/page/title/score/ACL provenance and only an
  authorized excerpt of at most 1,200 characters (`content_scope=authorized_excerpt`)
- `packages.rag.adapters.langchain.AegisQueryRunnable` is a `Runnable` over
  `RagQueryApplicationService`. `ainvoke` returns `QueryResponse`; `astream`
  preserves the application `RagStreamEvent` token/citation/error/final sequence
- `await packages.agent.adapters.langchain.export_registry_tools(...)` exports
  an explicit allowlist as real `AegisStructuredTool` objects. A nonblank
  `agent_run_id` is required. Calls return the safe `ToolExecutionResult` envelope;
  a LangChain ToolCall produces a `ToolMessage` with envelope content/artifact

These APIs are async-only. Sync `invoke`/`run` fails explicitly. `abatch` and
streaming preserve the same authorization and evidence path. Service operations
serialize within each export, and tools exported together share a gate so one
request's database session is not used concurrently. Registry validation,
permissions, rate limits, timeout and audit apply on every execution. Private
`prompt_context` is excluded from exported tool envelopes.

Explicit and ambient callbacks are ignored inside these boundaries; each call
runs in isolated context with tracing disabled. Process-global debug settings
fail closed without being modified. A caller-owned outer chain can still observe
its own inputs and outputs: the caller must separately disable unsafe tracing.
The exports are not permission to send private data to other tools or providers.
Ingestion and chat-memory services retain their existing application interfaces;
there are no generic framework loader or memory replacements.

## Native structured Agent decisions

`create_agent_stepper(settings=..., llm_provider=..., registry=...)` selects the
JSON stepper by default. In `native_tools` mode it builds tool schemas from the
Registry and calls `ChatOpenAI.bind_tools` through the LangChain decision adapter.
`DecisionRequest`, `DecisionResponse`, `DecisionToolSchema` and `DecisionProvider`
are application contracts; framework messages stay behind the adapter.

Each step must contain exactly one native function call. The reserved
`aegis_final_answer` function carries `final_answer` and `final_citations`; it is
only a candidate until the unchanged final-answer validator accepts it. Unknown,
parallel, mixed text-and-tool, malformed, refused or truncated decisions fail
closed. Model-supplied tool arguments never bypass the Registry. Schemas do not
contain handlers or authenticated context. Provider usage, redacted errors,
timeouts, cancellation and bounded retry ownership retain the text provider
contract. Exact generated text is not a cross-provider equivalence guarantee.

## Agent SSE lifecycle

`POST /agent/run/stream` uses the same authentication, budgets, Registry and final
validation as `POST /agent/run`. It emits application-owned events rather than
raw model decisions:

```text
started -> tool_call -> tool_result -> ... -> final
                                          -> error
```

Sequence IDs are connection-local and ordered. Tool events contain safe progress;
only a validated terminal answer may appear in `final`. The run result is persisted
before the terminal success event. Consumer disconnect/cancellation cancels the
owned runtime and performs bounded terminal cleanup; it never emits a success
for abandoned work. `Last-Event-ID` replay is not supported. This SSE connection
is separate from the explicit durable lifecycle described below.

## LangGraph execution and security

`packages/agent/adapters/langgraph.py` implements `AgentRuntimePort` using an
actual compiled `StateGraph`:

```text
START -> decision -> controlled tool -> decision
                 -> final validation -> END
                 -> stopped/failed -> END
```

Both runtimes reuse the same guarded `_decide`, `_execute_tool` and
`_validate_result` operations from `AgentRuntime`. `AgentStepper.next_step`
still returns one decision. It never contains a second agent loop.

The shared operations retain the native ordering and semantics of max steps,
max tool calls, overall deadline, repeated-action detection, registry schema and
permission/rate limits, tool auditing, event redaction and strict final-answer
validation. Graph recursion is bounded separately and sized above the domain
step budget. The graph invokes the real registered tools; no framework tool
executor bypasses the registry.

For the ordinary `/agent/run` and `/agent/run/stream` path, each invocation has a new execution object and a new graph. Graph state contains
only a routing token. Authenticated context, input text, raw arguments, transient
prompt observations, answer text and citation evidence stay outside framework
state. Graph compilation explicitly disables checkpointers. Automatic LangSmith
tracing is disabled within adapter calls, even if ambient tracing variables are
set. Isolated framework tasks retain only the application tenant/RLS context,
OpenTelemetry context and request-log correlation fields, so ambient framework
collectors/callbacks cannot retain prompts. Adapters fail closed if process-global LangChain debug logging or OpenAI SDK
request/response DEBUG logging is enabled, without changing those global
settings. Existing redacted AegisRAG audit/event sinks remain authoritative.

## Explicit durable execution

The separate opt-in `/agent/durable` lifecycle implements versioned private
business snapshots, revision/lease-fenced recovery, a tool-result ledger and
explicit human approval. Both schedulers share these transitions. Its LangGraph
path uses a real SQL-backed checkpointer with only opaque run references,
revisions and routing state; business snapshots never enter framework state.

Original budgets survive restart and approval waits. Default wall-clock runs
retain their original absolute deadline; opt-in active runs separately bound
execution time and approval expiry, with a configured background worker available
for queued execution. Permission, revocation and evidence checks run again;
stale approvals and terminal runs
cannot resume. Completed tool results are reused, while uncertain side-effecting
dispatches require intervention rather than automatic replay. This is not an
exactly-once side-effect guarantee, arbitrary graph definition or multi-agent
scheduler. See [durable API, storage and recovery operations](operations/DURABLE_AGENT.md)
for storage introduced in `0018`, current-release schema preparation through
`0021`, supported tools, privacy, retention and acceptance limits. No performance improvement or exact output-equivalence claim is made.

## Capability and validation matrix

| Capability | Native | LangChain / LangGraph | Boundary |
| --- | --- | --- | --- |
| Chat text and token stream | Native provider | Real `ChatOpenAI` | Same generation DTOs, usage/error/cancellation contracts |
| Embedding, API and worker | Native provider | Real `OpenAIEmbeddings` | Ordered vectors, dimensions and provenance |
| RAG retrieval/query/chat | Existing application services | Same services and optional LC exports | Tenant/ACL/metadata filtering and hydration before evidence |
| Agent JSON decisions | Supported | Supported | Single decision per step; Registry owns execution |
| Agent native-tool decisions | No native-provider implementation | LC adapter; either scheduler | Opt-in, exactly one function per step |
| Agent orchestration | Governed runtime | Real compiled `StateGraph` | Shared budgets, tool policy and strict final validation |
| Agent SSE | Supported | Supported | Persisted terminal result; bounded disconnect cleanup |
| Explicit durable resume / approval | Opt-in business snapshots + ledger | Same business state + real reference-only checkpoints | Storage introduced in 0018; current schema 0021; original budgets; reauthorization; no unsafe side-effect replay |
| Retriever / Runnable / tools for ecosystem consumers | Application ports | Real `BaseRetriever`, `Runnable`, `StructuredTool` | Bound context, async-only, no callback forwarding |
| Ingestion and chat memory | Existing application services | No replacement exported | Existing provenance and session ownership remain authoritative |
| Live endpoint compatibility | Operator opt-in only | Operator opt-in only | Not inferred from mocked HTTP or framework imports |

```bash
# Native defaults and helpful missing-extra errors
uv run --frozen pytest tests/unit/test_framework_configuration.py \
  tests/unit/test_langchain_optional_dependencies.py

# Actual SDK, routes, application services, worker and ecosystem consumers
uv run --frozen --extra langchain --extra langgraph pytest \
  tests/integration/framework tests/integration/framework_exports \
  tests/unit/llm tests/unit/embeddings tests/unit/agent \
  tests/unit/framework_exports tests/integration/api/test_durable_agent_routes.py \
  tests/integration/storage/test_agent_stream_persistence.py tests/review
```

`test_backend_matrix.py` crosses all eight independent LLM/embedding/orchestration
selections in JSON mode, plus the four valid LangChain-native-tool combinations.
It executes `/retrieve`, `/query`, `/chat`, their streams, `/agent/run`, Agent SSE,
and the embedding worker using real framework classes over `httpx.MockTransport`
and controlled persistence. Assertions cover authorized citations, filtering,
no-evidence behavior, provider errors, budgets, tool permissions, cancellation,
terminal ordering, and embedding provenance rather than exact generated text.
Framework-specific cases skip when extras are absent; the native combination
continues running. Compatibility CI sets `AEGISRAG_REQUIRE_FRAMEWORKS=1` so missing
frameworks cannot silently turn that gate into skips.

CI has three required isolated version jobs: `locked`, `minimum`, and
`latest-compatible`. `scripts/framework_compat.py` reads requirements directly
from `pyproject.toml`: the minimum job pins all five declared framework components
to their lower bounds, and the latest-compatible job resolves upgrades inside the declared
ranges. The version-edge jobs run with `uv run --no-sync` after resolution so
execution cannot silently restore locked versions. Each runs `uv pip check`
and uploads a package-version report. No live provider credentials are supplied.
Publishing images depends on the framework gate as well as backend/web checks.
An authored CI job is not evidence that its environment has already passed.

Declared framework bounds are `langchain-openai>=1.1.14,<2`,
`langchain-core>=1.3.3,<2`, `langgraph>=1.0.10,<2`,
`langgraph-checkpoint>=4.1.1,<5`, and `langgraph-prebuilt>=1.0.8,<2`;
`uv.lock` records the reproducible baseline. Prebuilt is an explicit consumer
dependency because the exported tools are exercised through its real `ToolNode`;
it is installed together with LangGraph, not as a standalone feature. Its minimum
1.0.8 comes from LangGraph 1.0.10's own declared lower bound. The explicit
five-component minimum has been exercised through real SDK/graph consumers with
synthetic transports; dependency metadata alone was not used as acceptance.

These jobs check specific five-component profiles, not every Cartesian combination
within the declared ranges. In particular, LangGraph 1.0.10 with prebuilt 1.0.13
passes dependency-metadata checks but fails to import `ToolNode`: prebuilt requires
runtime APIs missing from that LangGraph release. A clean `uv pip check` or version
report is therefore not runtime compatibility evidence. The ToolNode consumers'
LangGraph/prebuilt guard skips only when the optional LangGraph distribution itself
is absent (a shared namespace is insufficient), and required-framework mode makes
the guard fail on that absence. Once the distribution is present, this guard
propagates import errors instead of skipping. The required integration fixture
also imports prebuilt. Mocked transport tests provide no live-provider proof.

### Local version-edge evidence (2026-10-15)

The following **specific resolved environments** passed the same local offline
contract subset, with fresh default+dev+LangChain+LangGraph installations and
`uv pip check`. Evaluation dependencies were excluded from these edge jobs.

| Profile | core | langchain-openai | LangGraph | checkpoint | prebuilt | Local result |
| --- | --- | --- | --- | --- | --- | --- |
| Exact minimum | 1.3.3 | 1.1.14 | 1.0.10 | 4.1.1 | 1.0.8 | 1312 passed; 2 live-provider skips; 4 deferred deselections |
| Latest-compatible resolved that date | 1.6.11 | 1.7.2 | 1.2.15 | 4.3.0 | 1.1.1 | 1312 passed; 2 live-provider skips; 4 deferred deselections |

These runs used Python **3.12.14**, not the authored hosted CI's Python 3.11.
They exercised all eight independent LLM/embedding/orchestration combinations,
the four meaningful native-tool-decision combinations, and real framework
consumers with synthetic transports. They do not establish live-provider
interoperability, model quality, physical-database acceptance, or every allowed
version combination. Latest-compatible resolution also upgraded transitive
packages; it was not a five-package-only change. Local HF-offline and explicit
provider-disabled flags are stricter than the hosted workflow, so this is not a
claim that hosted CI ran.

The first integrated five-component minimum aggregate had 1311 passes and one existing handler-entry timeout;
the exact unchanged node and the complete unchanged aggregate subsequently passed.
The original failure remains in the delivery evidence and its root cause is not
established. The two remaining skips are explicit live-provider opt-ins, not
installed-framework import failures. The deferred review file and four related
cases remain outside acceptance. The frozen-lock and full evaluation-group results, including any pending or
failed checks, are reported separately in the cumulative delivery report; none of
these local runs is a production-readiness certification.

The optional Ragas group uses the same OpenAI 1.x adapter family and its existing
`langchain-community<0.4.2` compatibility cap. Runtime adapter compatibility is
not a claim that every external Ragas integration is supported.

## Explicit real-endpoint validation

Default tests and CI never contact a live model service. The opt-in smoke test
sends only two short synthetic chat requests and one embedding request per
adapter, with zero retries and a 30-second per-call timeout. Running both adapters
may incur six provider requests and associated charges. It validates nonempty
text, stream termination, and embedding shape/identity, not model quality, native
tool-call support, provider throughput, production load, or deployment security.

After the operator has approved the destination, synthetic data transmission and
cost, supply these settings through the environment or approved secret injection:

- `AEGISRAG_REAL_ENDPOINT_ALLOWED=1`
- `AEGISRAG_REAL_BASE_URL` (explicit OpenAI-compatible base URL)
- `AEGISRAG_REAL_API_KEY` (never put a real key in the repository or CLI history)
- `AEGISRAG_REAL_CHAT_MODEL`, `AEGISRAG_REAL_EMBEDDING_MODEL`
- `AEGISRAG_REAL_EMBEDDING_DIM` (expected positive output dimension)
- Optional `AEGISRAG_REAL_STREAM_USAGE=1` when the endpoint supports usage chunks

```bash
uv run --frozen --extra langchain --extra langgraph pytest \
  tests/integration/framework/test_real_endpoint_opt_in.py --no-cov
```

No application credentials are reused implicitly. Missing explicit configuration
fails the opted-in test; otherwise it is skipped. Keep provider logs and test
artifacts free of keys and private prompts. The operator must separately validate
the exact model/version and enabled optional capabilities before deployment.

Primary framework references:

- [ChatOpenAI integration](https://docs.langchain.com/oss/python/integrations/chat/openai)
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Tracing control](https://docs.langchain.com/langsmith/trace-without-env-vars)
