# Target-environment mixed concurrency acceptance

`evaluation/mixed_load.py` is an **opt-in measurement utility**, separate from
the backwards-compatible development-header `evaluation/load_test.py`.
It supports bearer-authenticated retrieve, query, chat, and RAG SSE traffic,
short/long output budgets, paced stream consumption, and intentional disconnects.
It does not start a server, clear caches, upload documents, start ingestion,
change authentication, or generate credentials.

**No target performance was measured by the accompanying MockTransport tests.**
They validate this script's accounting/protocol contracts only. Neither a passing
test, a stub-provider run, nor a report with `status: completed` establishes
production capacity, model quality, answer correctness, or tenant/ACL isolation.
The procedure below must be executed separately on an approved real target before
making a throughput or P95/P99 improvement claim.

## 1. Approval and environment record

Use an isolated, authorized target and a bounded budget. Real requests may invoke
paid LLM/embedding providers and chat may persist conversation state. Obtain the
operator's approval for the target, credentials, corpus, concurrency/duration,
provider spend, intentional disconnects, and any separately managed ingestion.
Do not run this against production merely because a service is reachable.

Record the following beside every report:

- Exact target revision, deployment/configuration revision, host/hardware, CPU,
  RAM, accelerator, API worker/replica count, reverse-proxy/ingress settings,
  HTTP version, keepalive, stream buffering, and client-to-server network path
- Actual provider/model/version and location, input/output token settings,
  context size, provider rate limits, timeouts/retries, admission in-flight and
  waiter limits, and embedding batch size/concurrency
- Database and vector-store version/type, pool size/overflow/timeout, connection
  budget, Redis/RQ worker count, document/chunk count and sizes, index state,
  corpus/ACL fixture revision, and whether authentication uses local JWT, OIDC,
  or existing service tokens
- UTC time synchronization, load-generator hardware/CPU/RSS/socket utilization,
  cache preparation evidence, approved cost ceiling, abort conditions, and
  identities' expected permissions

Provider concurrency/admission controls are process-local. API worker and replica
counts multiply available slots; a per-process cap does not establish a
cluster-wide provider or database budget. Keep aggregate demand within provider
quotas and the database's reserved connection budget. Start with a small smoke
stage and observe recovery before increasing load.

## 2. Explicit target, identities and requests

The config uses a required absolute `target_url`, with an optional API path
prefix. URL userinfo, query parameters and fragments are rejected, including
empty `?` or `#`. Do not place secrets in the path: the normalized URL is reported.
TLS verification remains enabled. Redirects are never followed, and environment
proxy settings are deliberately ignored (`trust_env=False`). Use an approved
directly reachable target rather than weakening TLS/authentication to get access.

Each identity has its own existing bearer token in an environment variable and
its expected server-derived `tenant_id`/`user_id`. Tokens must be distinct across
identities. The tool neither decodes JWT claims nor issues/refreshes tokens; use
the deployment's normal authorized credential provisioning and sufficient TTL
for all stages and drain. A 401/403 is a failure, never a reason to broaden roles.
Only Authorization and generated request/trace identifiers are sent as identity
context; development `X-User-ID`/`X-Tenant-ID` overrides are not sent.

Every request case explicitly names its identity and JSON payload. Different
tenants can therefore use different queries and filters. The following is a
template for `mixed-load.json`, **not an executed run**. Replace the target,
revision, identities, queries and ingestion reference with approved fixtures.
Provision `AEGIS_LOAD_A_TOKEN` and `AEGIS_LOAD_B_TOKEN` through the normal secret
manager, outside the JSON file, logs, shell history, and repository.

```json
{
  "target_url": "https://approved-target.example/api",
  "target_revision": "EXACT_DEPLOYED_COMMIT",
  "request_timeout_s": 120,
  "think_time_s": 0.1,
  "identities": [
    {"label": "tenant-a-reader", "tenant_id": "tenant-a", "user_id": "reader-a", "token_env": "AEGIS_LOAD_A_TOKEN"},
    {"label": "tenant-b-reader", "tenant_id": "tenant-b", "user_id": "reader-b", "token_env": "AEGIS_LOAD_B_TOKEN"}
  ],
  "cases": [
    {"name": "retrieve-a", "identity": "tenant-a-reader", "endpoint": "/retrieve", "output_profile": "retrieval", "weight": 3,
     "payload": {"query": "Approved tenant A fixture question", "top_k": 10}},
    {"name": "short-a", "identity": "tenant-a-reader", "endpoint": "/query/stream", "output_profile": "short", "weight": 2,
     "payload": {"query": "Summarize the tenant A fixture in one sentence", "top_k": 10, "max_output_tokens": 128}},
    {"name": "long-b", "identity": "tenant-b-reader", "endpoint": "/query/stream", "output_profile": "long",
     "payload": {"query": "Explain the tenant B fixture in detail with its documented steps and citations", "top_k": 10, "max_output_tokens": 1024}},
    {"name": "chat-b", "identity": "tenant-b-reader", "endpoint": "/chat/stream", "output_profile": "short",
     "payload": {"query": "Summarize the tenant B fixture", "max_output_tokens": 128}},
    {"name": "slow-a", "identity": "tenant-a-reader", "endpoint": "/query/stream", "output_profile": "long", "behavior": "slow_reader", "slow_read_delay_s": 0.1,
     "payload": {"query": "Explain the tenant A fixture in detail", "max_output_tokens": 1024}},
    {"name": "disconnect-b", "identity": "tenant-b-reader", "endpoint": "/query/stream", "output_profile": "long", "behavior": "disconnect", "disconnect_after_token_events": 3,
     "payload": {"query": "Explain the tenant B fixture in detail", "max_output_tokens": 1024}}
  ],
  "stages": [
    {"name": "cold-smoke", "users": 2, "duration_s": 30, "cache_state": "cold"},
    {"name": "warm-baseline", "users": 4, "duration_s": 60, "cache_state": "warm"},
    {"name": "warm-step-16", "users": 16, "duration_s": 120, "cache_state": "warm"},
    {"name": "warm-ingestion-32", "users": 32, "duration_s": 120, "cache_state": "warm", "ingestion_state": "external_active", "external_ingestion_ref": "APPROVED_INGESTION_RUN_ID"}
  ]
}
```

Inspect the config and environment before the explicit opt-in:

```bash
python evaluation/mixed_load.py --config mixed-load.json --allow-real-load \
  --output-dir evaluation/reports
```

Without `--allow-real-load` the CLI exits 2 before reading config or constructing
an HTTP client. Programmatic calls also require `allow_real_load=True`, unless
the supplied transport is an actual `httpx.MockTransport`; such reports are
always labelled `mock_transport_contract_only`, even if opt-in was supplied.
No arbitrary endpoint or automatic upload is supported. Ordinary config fields
are strict and unknown fields fail validation.

## 3. Workload, stages and cache meaning

- `users` is the number of concurrent closed-loop request slots, not a fixed
  arrival rate or the number of distinct bearer identities. Slots share a
  deterministic weighted case list and select `(worker_index + request_index)
  modulo its length. Fast cases can therefore dominate completions. This is
  not an open-loop load generator and is susceptible to coordinated omission
- A worker starts no new request after the shared stage dispatch deadline. Its
  current request drains to completion, deadline, or planned disconnect. Stages
  execute serially after previous workers finish; clients/connections are reused
  across stages. No stage is dropped just because an earlier request failed
- `duration_s` is a dispatch window, not a hard process runtime. HTTPX phase
  timeouts and a cooperative whole-request `asyncio.timeout` both apply.
  Blocking/cancellation-resistant code is not forcibly killed. Drain includes
  network/client cleanup and is not a measured server queue-drain interval
- Fixed load: use one stage or repeat the same `users`. Staircase: use increasing
  `users` with steady intervals. Run independently repeated baseline and candidate
  trials with identical fixtures/settings; include recovery stages and reverse
  ordering when useful to expose warm-up and carryover effects
- Optional `max_requests_per_user` bounds smoke/contract runs; it can finish a
  stage before the configured duration. A capped stage is not steady-duration
  performance evidence. Inspect actual elapsed and sample counts
- `cache_state` is an **operator declaration only** (`unknown`, `cold`, `warm`).
  The script never flushes/restarts anything. Document separately which caches
  were reset or warmed, the authorized procedure, and hit/miss observations.
  Distinguish application/retrieval/model/index/OS caches. An uncached query or a
  stage name alone does not prove a cold service; use `unknown` if unverified
- `short`/`long` are intended workloads. `max_output_tokens` (1–4096 on current
  API) is an upper bound, not a measured output length. Confirm actual answer
  character lengths, provider token usage, no-answer rate, and citation quality
  separately. Early refusal/no-answer is not evidence of long-generation speed
- A slot's first chat request omits `session_id`, allowing the real memory service
  to create it. Only a completely successful, context-validated result can supply
  the server-issued ID for reuse by that worker/identity within the stage. State
  is reset for each stage, and a changed ID on reuse is a failure. A configured
  shared `session_id` is rejected. A failed/disconnected first chat may create
  persisted state without delivering its ID, so the next attempt may create
  another session. Budget storage and arrange separately authorized retention/
  cleanup; this script does not delete conversations. Corpus/ACL correctness and
  persistence still require independent functional/security checks

## 4. SSE completion, TTFT, slow readers and disconnects

HTTP 2xx alone is insufficient. JSON endpoints must return a non-error envelope,
matching request/trace and tenant/user context, and expected basic result fields.
Retrieve candidates must declare the expected tenant. This detects obvious
context leakage, but does not prove document ACL correctness or answer quality.

Streaming endpoints must return `text/event-stream`. The reader handles UTF-8
fragmentation, LF/CRLF lines, comments, BOM, and multi-line data fields. Parsed
event type must match the JSON `event`. Every event's request/trace must match;
the final identity must match the case. Success requires exactly one final with
`status: success`, a string answer and citation list, no preceding error, and
reading through clean EOF. Missing/truncated final, final error, malformed data,
unexpected events, events after final, wrong content type, transport interruption,
and context mismatch are failures even when the HTTP status was 200. This is a
minimal wire contract, not exhaustive DTO/citation/schema or semantic validation.

TTFT is client time from immediately before HTTP dispatch to parsing the first
**nonempty token delta**. Headers, comments, citations and empty deltas do not
count. SSE token events are not necessarily model tokenizer tokens. A valid final
without token deltas can succeed with null TTFT; it is never given a fabricated
zero. Client timing includes pool/network/server work and buffering; it is not a
provider's model-only first-token metric.

`slow_reader` pauses consumption after each complete SSE event, including final.
Its completion latency includes that client delay. Proxies, kernel buffers,
HTTPX buffering and event coalescing may absorb it: this exercises a paced
application consumer, **not proof of TCP backpressure**. For realistic slow
networks, use an independently approved traffic-shaping proxy and record its
settings, while monitoring server/provider resources and time to release slots.

`disconnect` closes the response after the configured count of nonempty token
events, before consuming final. It is recorded as `planned_disconnect`, never
as successful throughput or successful completion latency. If the stream fails
or ends before that threshold, it is an unexpected failure. A client-side close
does not prove server/model cancellation: correlate sampled disconnect request
IDs with provider in-flight slots, DB checkout, open streams, token/cost use,
and recovery after disconnect. The intended close can race with already buffered
server completion; record that limitation rather than claiming cancellation.

## 5. Parallel ingestion without automatic side effects

This script intentionally issues no upload, indexing, worker-control or queue
mutation requests. To measure contention during ingestion:

1. Independently authorize and start a bounded, known corpus ingestion using the
   deployment's normal upload/ingestion workflow and a separate least-privileged
   ingestion identity. Record document/chunk/embedding counts, run/job IDs,
   provider cost budget, worker configuration, start/end times and failure state
2. Set the overlapping stage to `ingestion_state: external_active` and set
   `external_ingestion_ref` to that approved run's evidence identifier. The marker
   is mandatory for this label but is **not automatically verified**
3. Verify from external job/worker telemetry that ingestion was actually active
   throughout the relevant portion of the stage. A backlog alone does not prove
   that parsing/embedding/DB writes were running. If it finished before the stage,
   reject the contention claim and schedule an authorized overlap trial
4. Keep the query fixtures stable. Deliberately test newly ingested content only
   in a separate freshness/correctness scenario. Compare idle and ingestion-active
   runs at the same query mix/concurrency and capture post-ingestion recovery
5. Confirm ingestion completion/failures, queue recovery, connection/slot release,
   and output quality after the request workload has drained

The label cannot be used as evidence of concurrent ingestion without those
separate timestamps and job records. This utility supplies no ingestion results.

## 6. Required server telemetry and gaps

The report supplies stage UTC windows and safe request IDs; it does **not** scrape
metrics, infer server queue time, or read databases. Align external metric/log/
trace captures with those windows and retain per-process/replica dimensions.
Do not record tokens, authorization headers, raw prompts/documents or SQL bind
values in telemetry. Use authorized observability accounts and aggregate data.

- HTTP: collect the existing `/metrics` `http_requests_total` and
  `http_request_duration_seconds` with method/handler/status and ingress logs.
  HTTP 2xx counters can include SSE failures and must not replace the client's
  final-validated success counts
- Provider admission: collect in-flight and waiting counts, admission rejects/
  timeouts, and a histogram/span from admission request to lease acquisition.
  `ProviderRuntime.in_flight`/`waiting` are process-local properties, not evidence
  that a Prometheus exporter or wait histogram exists. If the target does not
  expose these, add approved server-side instrumentation or mark this evidence
  **missing**. Do not derive admission wait from end-to-end latency subtraction
- Provider retries/rate limits: collect actual upstream attempts, 429/5xx/timeouts,
  Retry-After/backoff and exhausted budgets separately from logical API calls.
  This client performs **zero automatic retries**, including on 429/503, stream
  failure or disconnect, so it cannot count server/provider internal retries
- Ingestion queues: sample queued/started/failed/retry counts, oldest queued job
  age, enqueue-to-start latency, active worker count, batch size, processed
  document/chunk/embedding rate, and per-job start/end/status. Use the actual RQ/
  application queue tooling; queue length is not queue waiting time
- Database: record configured SQLAlchemy pool size/overflow/timeout per process,
  checked-out/idle connections, checkout timeout counts and **connection-acquire
  duration**. Time the awaited connection acquisition, not construction of an
  `AsyncSession`; SQLAlchemy pool occupancy alone does not measure pool wait.
  If no acquisition histogram/span exists, label it missing. Never claim it was
  measured by this HTTP client
- PostgreSQL: capture aggregate active/idle/idle-in-transaction sessions,
  connection count/headroom, locks and `wait_event_type`/`wait_event`, transaction
  age, query latency and CPU/I/O. Server lock/query waits are different from the
  application's pool acquisition wait. Avoid collecting private SQL/query text
- Host and client: API event-loop lag, CPU/RSS, file descriptors/open sockets,
  Redis latency, disk/network utilization, provider tokens/cost, load-generator
  saturation, and post-disconnect/post-load return to baseline. Client pool wait
  and exhausted generator CPU can limit load before the service saturates

Missing provider/DB/queue wait telemetry is an acceptance gap, not a zero value.
The report explicitly says server metrics were not collected. A complete capacity
claim requires that external evidence, including the absence or presence of
resource leaks after failures/cancellation.

## 7. Reporting, comparison and stopping rules

Reports use schema 1 with `measurement_source`, target revision, normalized URL,
safe identity/case labels, output budgets, stage UTC windows and outcomes. Tokens,
prompts, answer text, exception text and actual tenant/user IDs are omitted.
Treat operator-provided labels, revision and ingestion references as non-secret;
do not put secrets into them. Reports are unique, exclusive-created UTF-8 JSON
files; memory grows with attempts and latency samples. Failure examples and
intentional-disconnect correlation IDs are limited to 20 per stage.

For each stage and case:

- `attempts = successful + failed + planned_disconnects`; retries are always zero
- `attempts_per_s = all terminal attempts / actual stage elapsed`
- `successful_per_s = fully validated successes / actual stage elapsed`, with
  failures and planned disconnects excluded. Elapsed includes dispatch, think
  time and drain, excluding report I/O; per-case rates use the same full-stage
  denominator and therefore add to the aggregate rate
- `unexpected_failure_fraction = failed / attempts`; intentional disconnects
  remain in the denominator but never count as failure-free service successes.
  Also inspect each case separately so disconnects do not dilute failure rates
- `terminal_latency_all_attempts_ms` includes every terminal outcome, including
  planned closes; `completion_latency_successful_only_ms` includes complete
  successful requests only. Do not compare them as the same population
- TTFT distributions explicitly distinguish all token-observed outcomes from
  successful-only streams. JSON endpoints and streams with no delta add no TTFT
  sample. Inspect `sample_count` and nulls before making percentile claims
- Percentiles use sorted nearest rank `ceil(n*p/100)-1` without interpolation.
  For `[1,2,3,4]`, P50 is 2. This differs from the old `load_test.py` legacy-index
  algorithm and populations; those reports are not directly interchangeable
- `successful_answer_chars` provides observed final-answer lengths, not provider
  token counts. Pair these with external token-usage/no-answer/quality evidence

`completed` means that the configured request plan had no unexpected terminal
failures and every stage produced attempts; it does not enforce an SLO. A plan
containing only achieved disconnects can complete with zero successful QPS.
Any failed/partial/empty stage makes the run incomplete and exits 1. Invalid
configuration/no opt-in exits 2 before requests; report-write failure exits 1.
Interrupt/cancellation propagates after joining worker cleanup and does not
produce a fictional complete report. Keep external monitoring running until
actual server/provider/DB/queue recovery is established.

Before a trial, set concrete target-specific SLOs and abort conditions: minimum
successful QPS, successful-only P95/P99 completion and TTFT, unexpected error
budget, 429/timeout/admission rejection budget, DB/queue wait limits, resource
headroom, quality/ACL checks, and spend ceiling. Thresholds are not invented by
this utility and are not automatically evaluated. Stop the workload if an abort
condition is observed; do not increase capacity limits blindly to hide errors.

Compare baseline/candidate at the same offered mix, workload lengths, corpus,
topology, caches and ingestion state. Retain repeated trial reports and external
telemetry, record sample counts and variability, and examine per-case/per-tenant
results to avoid hiding tail regressions behind an aggregate. Higher completion
rate caused by faster failures, shorter/no-answer outputs, cache changes,
different model/provider quotas or skipped ingestion is not an improvement.

## 8. Offline contract checks

```bash
python -m pytest tests/unit/evaluation/test_mixed_load.py \
  tests/unit/evaluation/test_load_test_contracts.py -o addopts='' -q
ruff check --no-force-exclude evaluation/mixed_load.py tests/unit/evaluation/test_mixed_load.py
mypy --explicit-package-bases evaluation/mixed_load.py tests/unit/evaluation/test_mixed_load.py
```

Tests use only HTTPX MockTransport, fake clocks and synthetic streams. They cover
opt-in before client construction, config/target validation, per-identity token
and payload routing, redirects/no retries, JSON and SSE failures after HTTP 2xx,
fragmented streams, strict UTF-8, TTFT/completion accounting, slow consumption,
real ChatMemoryService session creation/reuse with a fake repository, intentional
disconnect cleanup, cancellation propagation, staged drain, external-ingestion
labels, report privacy and independent runs. **These are stub/contract results,
not a real target or model load benchmark.** Existing historical performance
figures are not updated by these tests.
