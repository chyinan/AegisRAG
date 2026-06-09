# Open WebUI Tool Event Contract

`POST /v1/chat/completions` keeps the OpenAI-compatible streaming shape while
optionally surfacing backend-confirmed Agent tool event summaries. This is a
visibility bridge only. It does not let Open WebUI declare tools, call the Tool
Registry, choose Python functions, or expand permissions.

Streaming tool chunks use normal `data: {...}` chat completion chunks and still
end with `data: [DONE]`:

```json
{
  "object": "chat.completion.chunk",
  "tool_event": {
    "event": "tool_result",
    "agent_run_id": "run-1",
    "tool_call_id": "call-1",
    "tool_name": "rag_search",
    "status": "error",
    "latency_ms": 12.5,
    "error_code": "TOOL_PERMISSION_DENIED",
    "request_id": "req-1",
    "trace_id": "trace-1",
    "next_step": "Open Audit Explorer with this request_id."
  },
  "metadata": {
    "tool_event": {
      "event": "tool_result",
      "tool_call_id": "call-1",
      "tool_name": "rag_search",
      "status": "error",
      "error_code": "TOOL_PERMISSION_DENIED",
      "request_id": "req-1",
      "trace_id": "trace-1"
    }
  },
  "choices": [{"index": 0, "delta": {}, "finish_reason": null}]
}
```

Allowed event fields:

```text
event
agent_run_id
tool_call_id
tool_name
status
latency_ms
error_code
request_id
trace_id
next_step
audit_ref
review_ref
```

Token chunks must not contain `tool_event` or tool metadata. Final chunks may
include `metadata.tool_event_summary` with `tool_event_count`,
`tool_call_count`, `tool_result_count`, `tool_error_count`,
`agent_run_id_count`, and a single `agent_run_id` when there is exactly one.

The adapter and sidecar/governance fallback must not expose or copy raw tool
arguments, raw output, observations, queries, answers, prompts, chunk text,
source URIs, object keys, local paths, SQL, vectors, embeddings, provider
payloads, tokens, secrets, ACLs, roles, permissions, or raw exceptions.

Fallback UI:

- Open WebUI can copy `tool_event`, `tool_events`, or
  `metadata.tool_event_summary` JSON into `/governance`.
- Governance/sidecar parsing uses explicit allowlists and stale-state clearing.
- Audit Explorer fallback renders safe lookup rows and copy/export payloads.
- Review Queue fallback can seed safe review identifiers and summaries only.
- Source Evidence does not resolve tool events and never displays raw tool
  output.

Focused validation:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/rag/test_openwebui_adapter.py tests/unit/rag/test_streaming.py -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_openwebui_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/agent/test_runtime.py -q
.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q
node tests/unit/web/sidecar_behavior_runner.js
```
