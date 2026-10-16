# Sequential pipeline benchmark: interpreting the numbers

`evaluation.benchmark_pipeline` is a small operator measurement tool for an
approved synthetic development deployment. It sends the fixed synthetic topic
questions in the source to `/retrieve` and then `/query`, sequentially. It does
not provision a corpus, start a server, change providers, enable authentication
modes, or measure answer quality.

## Before invoking it

The current invocation contract retains the existing development identity
headers. Use only an isolated development deployment that you are authorized to
exercise. The script is not a production-login client and does not establish an
authenticated deployment acceptance result. It cannot enable development headers
on the server. Do not change a production authentication setting to make it run.

Review the target's configured embedding, rewriting, reranking and generation
providers before invoking either endpoint; these server-side calls may transmit
the synthetic questions or incur provider charges. No live endpoint, credentials
or paid provider is exercised by the accompanying tests.

```bash
uv run python -m evaluation.benchmark_pipeline \
  --api-url http://127.0.0.1:8000 \
  --rounds 3 --queries 5 --request-timeout-seconds 120
```

`--api-url` is the actual destination for both endpoints. It accepts a direct
HTTP(S) origin without a path/query/fragment or URL credentials. Rounds must be
1–100, queries 1–10 (the fixed workload length), and timeout finite in (0, 120]
seconds. Defaults preserve three rounds, five questions and 120 seconds.

The requests are never retried. When retrieval fails, that pair's query request
is skipped, preserving the previous request sequence and avoiding an additional
generation call. A query failure does not erase its preceding successful
retrieval. Each request has a cooperative total timeout in addition to HTTPX's
I/O timeouts; this is not a hard bound on blocking synchronous code or process
suspension and is not a total-run timeout.

## Report schema 2

One uniquely named UTC-timestamped JSON report is written under
`evaluation/reports`. Existing files are not overwritten. The report contains no
returned answer, document text, response error body or request headers.

Each endpoint has independent counts:

- `planned`: rounds × selected questions
- `attempts`: actual HTTP attempts, including failures
- `successes`: successful HTTP responses with the expected JSON field shape
- `failures`: attempted requests that failed
- `skipped`: planned requests not sent because the preceding retrieval failed
- `errors`: stable error categories and counts, without raw exception text

The mean and p50/p95/p99/min/max are measured in milliseconds and use only that
endpoint's successful requests. Percentiles use the nearest-rank definition.
Empty success sets produce JSON `null`, not zero or an exception. Inspect counts
before quoting a percentile: failed/time-out requests are not represented in the
successful-latency distribution, and a small sample does not establish an SLO.

`completed_pairs` requires both endpoints to succeed. The observed sequential
pairs-per-second value divides completed pairs by the measured wall-clock run
interval, including failed request waits. Zero measured elapsed time produces
`null`. This is **not concurrent service throughput, server capacity or a load
test**. Client creation/report serialization are outside that interval; network
connection establishment during a request is included.

Schema 2 intentionally removes the old misleading `throughput_qps` field.
Consumers of old reports must not combine it with the new sequential-pair rate.
The endpoint mean/percentile keys remain, but empty values are now nullable and
counts are explicit. `status=complete` means every planned pair completed the HTTP
and response-shape checks, not that answers are correct, cited or useful.

Exit codes: 0 for all planned pairs complete; 1 for an incomplete measurement;
2 for invalid arguments; 130 for keyboard interruption. An interrupted request may
have reached the server and is not automatically repeated. The normal report is
written after the measurement loop; a process kill/interruption or report-write
failure can prevent a report from being saved.

## Evidence and appropriate use

The unit/CLI regressions use `httpx.MockTransport` and controlled clocks. They
verify destination selection, denominator/percentile arithmetic, partial/empty
failure reports, cooperative cancellation and invocation validation. They are not
latency or quality results for AegisRAG.

For a useful operator experiment, record the repository revision, dependency
versions, exact command, corpus/index readiness, provider configuration, hardware,
network location, concurrency and warmup/cache conditions alongside the report.
This file cannot attest those external facts. Avoid comparing reports with
uncontrolled differences or presenting a fixed synthetic workload as production
traffic.

Use [deployment acceptance](DEPLOYMENT_ACCEPTANCE.md) for the separate known-answer
workflow and [Ragas evaluation](../evaluation.md) for real judge-metric contracts.
Neither is replaced by this latency measurement tool.
