# Integration tests

The default suite exercises API, worker and storage boundaries using controlled
in-process dependencies. Tests requiring disposable PostgreSQL/Redis resources
are explicitly gated; see their fixture requirements before enabling them.

## Optional framework parity

```bash
uv run --frozen --extra langchain --extra langgraph pytest \
  tests/integration/framework tests/integration/framework_exports --no-cov
```

The framework matrix runs real provider classes and installed LangChain/LangGraph
components with `httpx.MockTransport`. It covers all eight independent
LLM/embedding/orchestration selections plus four valid native-tool-decision
configurations. Application services, retrieval scope enforcement, Registry,
evidence validation and routes remain real; persistence is controlled locally.
The default native combination runs without optional extras.

The live endpoint smoke is skipped unless the operator explicitly enables it and
provides its separate settings. Do not enable it with deployment credentials or
private prompts implicitly. See [framework capabilities, version CI and endpoint
validation](../../docs/FRAMEWORK_ADAPTERS.md) for the exact commands, scope and
limits. Ordinary CI never sets the live-endpoint opt-in.
