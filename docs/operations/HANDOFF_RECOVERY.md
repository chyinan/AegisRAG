# Recover a committed embedding handoff

Use this bounded operation when document parsing/chunking committed successfully,
but a Redis outage prevented delivery of its embedding job and the original
ingestion RQ retry budget has expired. Restore Redis/worker availability first.
This is an explicit operator action, not an automatic sweeper or a general retry
button. It does not solve killed/OOM workers, terminal model failures or corrupted
queue entries.

## Required state and authorization

- Sign in normally to the document's tenant with `document:manage`
- Keep the `document_id`, exact `version_id`, and original ingestion `job_id` from
  the upload response. A later status response can instead contain an embedding
  job ID; do not substitute it
- The document/version must be active, the ingestion job and version must be
  `chunked`, and the existing linked embedding job must be `queued` with zero
  attempts
- Use the deployment's configured embedding queue. Renaming queues is still a
  separate drain/reconciliation operation

Using an authenticated HTTP client, send:

```http
POST /documents/{document_id}/versions/{version_id}/recover-handoff
Content-Type: application/json

{"ingestion_job_id": "the-original-upload-job-id"}
```

Use the same bearer authentication as other protected APIs. Keep credentials out
of terminal history, screenshots and issue reports; use your approved API client
or a protected client configuration file. There is no development-auth shortcut.

## Result and follow-through

A successful envelope returns `status=delivery_confirmed`, both application job
IDs, the deterministic RQ ID, configured queue name, `queue_status=queued`, and
request/trace IDs. It confirms a matching queued delivery and queue-list
membership at the observation instant. It does **not** mean the worker completed
or that the version is searchable. Poll the exact version's existing status
endpoint until `retrieval_ready`, or investigate its reported failure.

Repeating the operation while the same job is still queued is idempotent. The
same embedding job/chunks and task owner are retained; no document is reparsed,
no replacement application embedding job is created, and existing delivery retry
budgets, failed registries or task ownership are not reset. If the Redis job key
is missing, the normal producer creates a same-ID delivery with the configured
initial RQ retry budget. Database attempts stay unchanged. Missing/expired Redis
history cannot prove that a prior delivery never failed before entering the
application service; inspect logs and resolve that uncertainty before replaying. The authenticated operator and
outcome are recorded in the existing audit store.

| Result | Meaning and next action |
|---|---|
| `403 DOCUMENT_MANAGE_FORBIDDEN` | Use an already-authorized operator; do not widen document ACLs |
| `404 DOCUMENT_VERSION_NOT_FOUND` | Scope is missing, mismatched, foreign-tenant or deleted; verify the recorded IDs |
| `409 EMBEDDING_HANDOFF_NOT_RECOVERABLE` | State has advanced, been deleted, failed, or is inconsistent; read current status |
| `409 EMBEDDING_HANDOFF_QUEUE_CONFLICT` | Existing RQ entry is active/delayed/terminal, belongs to another queue, has mismatched identity/target, or is an orphaned queued hash; inspect it without deleting/replacing it |
| `503 EMBEDDING_HANDOFF_RECOVERY_UNAVAILABLE` | Delivery or its audit could not be confirmed; Redis may already have accepted it. Read status, restore connectivity and repeat deliberately |

A worker can start between the database check and queue confirmation. In that
case a conservative `409` is possible even though processing is progressing;
read status rather than resetting the task. If deletion races with delivery,
the existing tenant-scoped active-version worker claim remains authoritative.

## Verification boundaries

The SQLite/ASGI fault-injection tests cover authorization, scope, unchanged
checkpoints, lost acknowledgements, deduplication, conflicting RQ states and audit
failures. They do not stand in for live Redis/PostgreSQL.

The opt-in `tests/integration/storage/test_postgres_pilot.py` gate also exercises
operator recovery after the original ingestion retry budget is exhausted, then
runs the production embedding worker against Redis/RQ and PostgreSQL with
`JSONSerializer`. Source storage and model remain synthetic. Run it only against
the explicitly disposable database described in the pilot runbook; do not count
skipped probes as passed.
