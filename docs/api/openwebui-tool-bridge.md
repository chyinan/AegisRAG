# Open WebUI Function/Tool Bridge

`POST /v1/chat/completions` now accepts OpenAI-compatible `tools`,
`tool_choice`, legacy `functions`, and legacy `function_call` fields, but the
backend treats them as untrusted declarations only. The route does not execute
client-provided code, does not trust client schemas, and does not let Open
WebUI bypass Tool Registry governance.

## Supported Request Fields

- `tools=[{"type":"function","function":{...}}]`
- `tool_choice="auto" | "none" | "required" | {"type":"function","function":{"name":"..."}}`
- `functions=[{...}]` as compatibility input only
- `function_call="auto" | "none" | {"name":"..."}` as compatibility input only

The backend normalizes declarations into safe candidates with only:

- `name`
- `description`
- schema summary: `type`, `property_names`, `required`, `property_count`
- declaration type
- choice mode

Raw client schema, raw arguments, prompts, message history, ACL, roles,
permissions, `tenant_id`, `user_id`, `source_uri`, local paths, tokens, and
secrets are not forwarded into Tool Registry metadata, prompts, logs, or public
responses.

## Validation Rules

Requests are rejected when they include:

- mixed modern and legacy declaration styles in the same request
- duplicate tool names
- non-`function` tool types
- blank descriptions
- non-object `parameters`
- oversized schemas
- forced choices that do not reference a declared tool

Sensitive schema fields such as `tenant_id`, `user_id`, `roles`,
`permissions`, `acl`, `token`, `secret`, `source_uri`, `file_path`, `prompt`,
and `raw_output` are stripped from the safe schema summary.

## Permission Model

Open WebUI service tokens remain backend identities, not trust boundaries.

- Tool bridge entry requires `agent:run`
- `rag_search` also requires `agent:tool:rag_search`
- `calculator` also requires `agent:tool:calculator`
- `file_reader` also requires `agent:tool:file_reader`
- `rag_search` still enforces `document:read` and `retrieval:query`

The default Open WebUI service token example in `.env.example` keeps only
`document:read,retrieval:query`, so tool declarations are denied until explicit
Agent permissions are added.

## Execution Shape

The backend currently supports a conservative single-tool execution path:

- forced tool choice by name
- `auto` or `required` only when exactly one tool is declared
- `none` falls back to the normal RAG chat path

For successful tool execution, Open WebUI receives only safe observation
summary text plus allowlisted metadata:

- `agent_run_id`
- `tool_call_id`
- `tool_name`
- `status`
- `latency_ms`
- `error_code`
- `request_id`
- `trace_id`
- safe citation identifiers for `rag_search`

`rag_search` returns only citation-safe identifiers. `calculator` returns a
bounded safe result summary. `file_reader` returns only an allowlisted safe
summary, never raw file content.

## Denial Shape

Unknown tools, unavailable tools, missing `agent:run`, and missing
`agent:tool:*` do not expose internal distinction to the client. The response
uses a stable safe error contract:

```json
{
  "error": {
    "code": "OPENWEBUI_TOOL_NOT_AVAILABLE",
    "details": {
      "request_id": "req-1",
      "trace_id": "trace-1",
      "error_code": "OPENWEBUI_TOOL_NOT_AVAILABLE",
      "next_step": "Review governance-safe audit details with the request_id."
    }
  }
}
```

Audit metadata can still record backend-only reason codes such as
`missing_agent_run_permission`, `TOOL_PERMISSION_DENIED`, or
`tool_unavailable`.

## Streaming

When `stream=true`, tool paths reuse the 9.2 Open WebUI tool event contract:

- safe `tool_call` chunk
- safe `tool_result` chunk
- final chunk with `metadata.tool_event_summary`
- terminal `data: [DONE]`

See `docs/api/openwebui-tool-events.md` for the chunk-level allowlist and
governance fallback behavior.

## Focused Verification

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/rag/test_openwebui_adapter.py tests/unit/rag/test_streaming.py -q
.venv\Scripts\python.exe -m pytest tests/unit/agent/test_openwebui_bridge.py tests/unit/agent -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_openwebui_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py tests/unit/test_readme_expectations.py -q
```
