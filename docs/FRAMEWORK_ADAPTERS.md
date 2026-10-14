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

Three independent settings select implementations:

| Setting | Default | Optional value | Scope |
| --- | --- | --- | --- |
| `LLM_ADAPTER` | `native` | `langchain` | `LLMProvider.generate/stream` |
| `EMBEDDING_ADAPTER` | `native` | `langchain` | API retrieval and embedding worker |
| `AGENT_ORCHESTRATION_BACKEND` | `native` | `langgraph` | `/agent/run` runtime |

`LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` and their
version fields retain their existing meanings. An adapter name is not a model
provider identity. Unknown adapter names fail configuration; the LangChain
adapter requires one of the existing OpenAI-compatible provider selections.
Selecting LangGraph alone works with the default fake LLM for offline use.

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

The first LangChain component integration supports text-only OpenAI-compatible
chat and embeddings. It does not expose framework agents, tool execution,
multimodal inputs, arbitrary callbacks, external tracing or caches. Unsupported
tool/multimodal model responses fail closed. Endpoint compatibility still needs
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

Each invocation has a new execution object and a new graph. Graph state contains
only a routing token. Authenticated context, input text, raw arguments, transient
prompt observations, answer text and citation evidence stay outside framework
state. Graph compilation explicitly disables checkpointers. Automatic LangSmith
tracing is disabled within adapter calls, even if ambient tracing variables are
set. Isolated framework tasks retain only the application tenant/RLS context,
OpenTelemetry context and request-log correlation fields, so ambient framework
collectors/callbacks cannot retain prompts. Adapters fail closed if process-global LangChain debug logging or OpenAI SDK
request/response DEBUG logging is enabled, without changing those global
settings. Existing redacted AegisRAG audit/event sinks remain authoritative.

The optional runtime does **not** implement durable checkpoints, interrupted-run
resume, human-in-the-loop approval, persistent graph memory, cross-request graph
state, arbitrary graph definitions or multi-agent scheduling. Adding those later
requires separate tenant-isolation, reauthorization, idempotency and restored
budget designs. Existing agent-run persistence is an application record, not a
LangGraph checkpoint. No performance improvement or output-equivalence claim is
made for either backend.

## Tests and version policy

```bash
# Prove native defaults and helpful missing-extra errors
uv run --frozen pytest tests/unit/test_framework_configuration.py \
  tests/unit/test_langchain_optional_dependencies.py

# Exercise actual installed frameworks against mocked HTTP and governed tools
uv run --frozen --extra langchain --extra langgraph pytest \
  tests/unit/llm/test_langchain_provider.py \
  tests/unit/embeddings/test_langchain_provider.py \
  tests/unit/agent/test_runtime_backend_contract.py \
  tests/unit/test_framework_configuration.py
```

The runtime matrix applies the same assertions to native and graph execution:
budgets, timeouts and cancellation, unauthorized tools, provenance/citation
failures, error paths, event order/redaction and request isolation. Provider
tests use real framework classes with local `httpx.MockTransport`; they do not
contact a live model API. SDK-dependent tests are skipped when extras are absent,
and CI explicitly installs both extras for the optional contract step.

Minimum supported bounds are `langchain-openai>=1.1.14,<2`,
`langchain-core>=1.3.3,<2`, `langgraph>=1.0.10,<2` and
`langgraph-checkpoint>=4.1.1,<5`. These include fixes for published upstream
security advisories, including transitive deserialization issues. `uv.lock`
records the resolved versions; use frozen installs for reproducibility.

The existing `eval` group's `langchain-openai<1` constraint is aligned with the
same 1.x family. Its `langchain-community<0.4.2` cap preserves the legacy
`ChatVertexAI` import used by Ragas 0.4. The eval group remains optional and is
not required by runtime adapters. Dependency alignment is not a claim that all
pre-existing Ragas evaluator paths or external integrations have been repaired.

Primary references:

- [ChatOpenAI integration](https://docs.langchain.com/oss/python/integrations/chat/openai)
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Tracing control](https://docs.langchain.com/langsmith/trace-without-env-vars)
- [OpenAI package metadata](https://pypi.org/pypi/langchain-openai/1.1.14/json)
- [Core package metadata](https://pypi.org/pypi/langchain-core/1.3.3/json)
- [LangGraph package metadata](https://pypi.org/pypi/langgraph/1.0.10/json)
- [Checkpoint package metadata](https://pypi.org/pypi/langgraph-checkpoint/4.1.1/json)
