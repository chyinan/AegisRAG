# Queue failure and stuck-delivery controls

The document-management operator API covers the configured ingestion and embedding RQ queues. It uses the existing deterministic delivery IDs and JSON serializer. The automatic scheduler, worker retry policy, and `recover-handoff` contract are unchanged.

## Inspect deliveries

`GET /v1/queue/jobs?limit=50` requires the existing `document:manage` permission (or another permission accepted by the document-management policy). The response uses the standard request envelope. Each item contains:

- persisted job/document/version identifiers, queue name and queue delivery ID
- RQ status, domain status, domain attempt count and remaining RQ retries
- `state`: `failed`, `stale`, `stuck`, or `terminated`
- age, last heartbeat, safe domain error code and allowed operator actions

A queued delivery is stale when its enqueue age reaches `QUEUE_JOB_TIMEOUT_SECONDS`. A scheduled delivery is stale only when its scheduled time is overdue by that interval. A started delivery is potentially stuck when its last heartbeat is older than that interval; if no heartbeat is available, its start/enqueue time is used. Stuck detection is an operational signal, not proof that the process is dead.

Inspection reads failed, started, queued, scheduled and canceled registries without running RQ registry cleanup. Each request examines at most `limit` delivery IDs (1–100), including entries which are not returned. Follow `next_cursor` until it is null, even when a page has no items. `scanned_count` counts this tenant's valid deliveries examined on that page. Registry pagination is live and best-effort: concurrent queue changes may shift a page, so refresh from the beginning after an action. Raw payloads, document text and worker exception tracebacks are never included.

## Controlled retry

`POST /v1/queue/jobs/{ingestion|embedding}/{queue_job_id}/retry`

Request body:

```json
{"expected_attempt_count": 2, "expected_queue_status": "failed"}
```

Use the attempt count and RQ status returned by inspection. The service returns `409 QUEUE_JOB_CHANGED` when the snapshot is outdated.

Retry is available only for a failed/stopped RQ delivery in the failed registry, with a due `failed_retryable` domain job and at least one remaining RQ retry. One explicit operator retry consumes one existing retry. It queues the same RQ job, payload and deterministic ID; it does not reset domain attempts or replenish a retry budget. The next ordinary worker claim increments the domain attempt count.

Exhausted or semantic-terminal failures require diagnosis and a separately authorized recovery workflow. They cannot be revived with this endpoint. A committed chunked-to-embedding handoff continues to use the separate `POST /v1/documents/{document_id}/versions/{version_id}/recover-handoff` endpoint and its narrower unstarted-handoff rules.

## Termination

`POST /v1/queue/jobs/{ingestion|embedding}/{queue_job_id}/terminate`

The body has the same expected attempt count and queue status fields. Queued/scheduled/failed/stopped deliveries are canceled using RQ, retained for inspection, and their matching unfinished domain job is marked `failed_terminal` with `QUEUE_OPERATOR_TERMINATED`. Attempts and retry budget remain intact. An already canceled delivery can be reconciled again if a prior database acknowledgment was lost.

For a started delivery, the API sends RQ's stop-job command and returns `stop_requested`. This is not a termination confirmation, and the domain attempt is not prematurely marked terminal. Refresh inspection; after RQ reports `stopped` or `failed`, submit termination with the refreshed snapshot to record the confirmed terminal domain state. If the worker is unreachable, the stop request may remain pending and infrastructure intervention is needed. Completed/chunked ingestion work and completed embedding work cannot be terminated through these controls.

Every list, retry and termination request records the authenticated operator, request/trace IDs, safe outcome and latency in audit. Queue mutations use Redis WATCH for conditional state changes. Redis and SQL commits are separate: if a response is unavailable, refresh the queue and document status before retrying.

## Verification

Run the positive service, real-RQ/mock-Redis, SQLite persistence, and route tests without credentials or external API calls:

```bash
python -m pytest --no-cov -q \
  tests/unit/ingestion/test_queue_operations.py \
  tests/unit/data/test_queue_operations_adapter.py \
  tests/integration/storage/test_queue_operations_persistence.py \
  tests/integration/api/test_queue_ops_routes.py
```

The Redis I/O in these tests is mocked; this is not physical Redis acceptance evidence.
