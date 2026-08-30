# Service Token tool events

The OpenAI-compatible chat adapter can expose backend-confirmed tool events as safe metadata. Events are derived from the governed Tool Registry; clients cannot inject event status or tool results.

## Stream shape

`/v1/chat/completions` may emit a `chat.completion.chunk` with an empty `delta` and a `tool_event` object:

```json
{
  "object": "chat.completion.chunk",
  "tool_event": {
    "event": "tool_result",
    "agent_run_id": "run-1",
    "tool_call_id": "call-1",
    "tool_name": "rag_search",
    "status": "success",
    "latency_ms": 42.1,
    "error_code": null,
    "summary": "authorized citation-safe result"
  }
}
```

Allowed event names are `tool_call` and `tool_result`. The backend validates the tool name, status, latency, IDs, and error code before emitting the event.

## Final metadata

The final chunk can include `metadata.tool_event_summary` with counts only:

- `tool_event_count`
- `tool_call_count`
- `tool_result_count`
- `tool_error_count`
- `agent_run_id_count`
- `agent_run_id` when exactly one run is present

The contract deliberately excludes raw arguments, raw output, observations, prompts, query text, answers, chunk content, source URIs, object keys, local paths, SQL, vectors, provider payloads, tokens, secrets, ACLs, and roles.

Use `request_id` or `trace_id` to open the Governance workbench for the authorized audit timeline. Do not treat client-supplied event metadata as evidence of execution.
