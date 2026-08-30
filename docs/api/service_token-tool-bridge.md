# Service Token tool bridge

The Service Token adapter accepts modern OpenAI `tools`/`tool_choice` fields and legacy `functions`/`function_call` fields. It normalizes declarations into safe candidates and executes only through the backend Tool Registry.

## Permissions

The caller must have:

- `agent:run`
- the selected tool's permission, such as `agent:tool:rag_search`

The default local Service Token profile has `document:read,retrieval:query` only, so tool execution is denied until the Agent permissions are explicitly granted.

## Request example

```json
{
  "model": "aegisrag",
  "messages": [{"role": "user", "content": "Find the leave policy."}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "rag_search",
      "description": "Search authorized knowledge.",
      "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}
    }
  }],
  "tool_choice": {"type": "function", "function": {"name": "rag_search"}}
}
```

The bridge validates declaration shape, selected tool, schema summary, tenant context, RBAC, rate limit, timeout, and Agent run limits. The model never receives a direct Python callable or an authorization decision path.

## Response and denial behavior

Successful tool execution returns a safe assistant message and citation metadata. RAG citations contain document/version/chunk IDs, display name, page range, retrieval method, and score. The bridge does not return raw tool output.

Denied or invalid calls use structured errors with a stable code, request/trace IDs, and safe reason metadata. Typical codes are `SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN`, `SERVICE_TOKEN_TOOL_NOT_AVAILABLE`, and `SERVICE_TOKEN_TOOL_SELECTION_UNSUPPORTED`.

For audit details, use the `agent_run_id`, `tool_call_id`, `request_id`, or `trace_id` in the Governance workbench. Never put provider keys, access tokens, prompts, source URIs, local paths, or document excerpts in a Service Token request.
