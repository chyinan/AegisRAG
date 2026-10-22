# Closed-loop HTTP load measurements

`evaluation/load_test.py` measures the terminal HTTP outcomes and client-observed
latency of the existing `/retrieve` → `/query` workload. It is an operator
measurement utility, **not service-capacity, retrieval-quality, answer-quality,
or production-load acceptance evidence**. Synthetic unit tests verify the
measurement contracts only; they do not exercise a running service or providers.

## Usage and authorization

Run only against an environment you are authorized to exercise. The tool issues
real requests when run normally; `/query` may invoke configured paid providers.
The examples below are instructions, not evidence of an executed live run.

```bash
python evaluation/load_test.py --users 10 --duration 30 --api-url http://localhost:8000
python evaluation/load_test.py --users 50 --duration 60 --request-timeout 120 \
  --api-url http://localhost:8000 --output-dir evaluation/reports
```

The existing defaults remain 10 users, a 30-second dispatch window, the localhost
base URL, and a 120-second HTTPX timeout. Users must be a positive integer;
duration and timeout must be positive finite seconds, without arbitrary caps.
Fractional duration/timeout values are accepted. `--api-url` is honored for both
endpoints and recorded as the normalized base URL; an optional path prefix is
preserved. Only absolute HTTP(S) URLs without userinfo, query, or fragment are
accepted. Do not place secrets in a URL path, since the base URL is reported.

The existing development identity headers are unchanged (`admin`, tenant
`default`, roles `admin,platform_admin`, permissions
`document:read,retrieval:query`). The tool adds no server permissions and does not
provide production authentication. Use only the development configuration that
already supports these headers; do not disable server authentication to run it.
A rejection such as HTTP 401/403 is a failed measurement request, not a reason to
retry with broader permissions. This change does not alter authorization policy.

## Workload and time

- Every worker shares the same monotonic pair-dispatch deadline
- Worker `i` begins at question `i % len(QUERIES)` and advances round-robin;
  Python hash randomization no longer changes question selection
- Each started pair sends `/retrieve`, then `/query` even when retrieval failed
- A pair started before the deadline completes both requests and the existing
  fixed one-second think time, even if it crosses the deadline
- No new pair starts after the dispatch window. Scheduling and response speed
  still affect total pairs and interleaving; this is closed-loop load, not a fixed
  arrival-rate generator or a predetermined total-request count
- `dispatch_window_s` is the requested duration. `actual_elapsed_s` includes all
  work and think time after the shared start, excluding client setup/report I/O
- `drain_s = max(0, actual_elapsed_s - dispatch_window_s)` includes finishing
  requests, final think time, scheduling and client-side overhead. It is not a
  separately measured server queue-drain duration
- Throughput is terminal request count divided by actual elapsed, not configured
  duration. Successful-throughput fields use only successful requests

`--request-timeout` applies to each HTTPX request's connection/pool/write/read
phases and also an `asyncio.timeout` cooperative whole-request deadline. Keeping
the historical 120-second default while adding the whole-request deadline is a
stricter bound: a cooperative request can no longer use 120 seconds in each phase
independently. It does not impose a hard process wall-clock limit or terminate
synchronous blocking or cancellation-resistant code. Up to two requests in the
final pair plus think time can extend a worker beyond its dispatch window.
Requests are not retried. Caller cancellation/interrupt propagates; the tool does
not fabricate a complete report for an interrupted run.

## Outcomes and exit codes

Each run has independent in-memory state. Only HTTP **200–299** is successful;
redirect following is disabled, including when an injected client normally
follows redirects. HTTP status counts include every returned HTTP response.
Errors are counted by safe category, with no response bodies/headers, exception
messages, request text, or development identity values in the report:

- `http_status`: a returned HTTP response outside 200–299, including 3xx
- `httpx_timeout`: an HTTPX per-phase timeout
- `request_deadline`: the cooperative request deadline, or another `TimeoutError`
- `transport_error`: another HTTPX transport/protocol error
- `unexpected_error`: another ordinary request/client exception

Each terminal attempt contributes one latency sample and exactly one success or
error. A failure without a response contributes no fictional HTTP status code.
Unexpected ordinary exceptions are represented as failed attempts rather than
leaking their messages; cancellation propagates instead of becoming an error.
Neither a 2xx result nor `completed` validates response schema or answer quality.

| Run status | Meaning | Exit code |
| --- | --- | --- |
| `completed` | At least one attempt; all terminal attempts were 2xx | 0 |
| `partial` | Both successful and failed terminal attempts | 1 |
| `failed` | Attempts occurred; none succeeded | 1 |
| `empty` | No terminal attempts (e.g. scheduling exhausted a tiny window) | 1 |

Invalid configuration exits 2 before starting a client. Report-write failure
exits 1. Cancellation and interrupts do not yield a normal measurement report.

## JSON and latency comparability

Every completed run, including `partial`, `failed`, or `empty`, writes a separate
UTF-8 JSON file named with a UTC microsecond timestamp and random UUID. Exclusive
file creation refuses overwrite even if a name collides. Reports use
`schema_version: 2`; the old top-level `config`, `retrieve`, `query`, and
`combined_throughput_qps` fields remain, with additional explicit outcome/time
metadata. Old reports without these metadata cannot establish these contracts.

`retrieve` and `query` retain `total`, `errors`, `throughput_qps`, `p50_ms`,
`p95_ms`, `p99_ms`, and `avg_ms`, and add independent success, status/error,
sample-count and min/max fields. JSON stores unrounded numeric results;
`null` means there were no latency samples, not a measured zero latency.

The latency population is **all terminal attempts, including failures**. The
measurement begins immediately before a request and ends when the response or
exception completes; it includes client/pool/network overhead. Failed requests
can dominate this distribution, so always inspect successes and error/status
counts alongside latency. Think time is included in elapsed throughput time but
not in individual request latency. Raw samples are kept in memory for exact
percentiles, so memory use grows with terminal attempts; reports contain only
aggregates.

For historical continuity, percentiles preserve the old algorithm: sort `n`
samples ascending and select zero-based index `min(floor(n * p / 100), n - 1)`.
There is no interpolation. The report labels this as
`sorted_zero_based_floor_n_p_over_100_clamped`. This is **not nearest-rank**: for
`[1, 2, 3, 4]`, p50 is 3 (nearest-rank would be 2). Do not combine this script's
all-attempt legacy-index percentiles with a successful-only nearest-rank sequential
latency benchmark without explicitly reconciling both population and algorithm.

## Offline validation

The ordinary unit suite uses HTTPX `MockTransport` and fake clocks only. It
verifies origin selection, independent repeated runs, classifications, drain,
percentiles, request timeouts, cancellation propagation, report collision
handling and nonzero exit cases, with no server or provider access:

```bash
python -m pytest tests/unit/evaluation/test_load_test_contracts.py -o addopts='' -q
```

This command is a reproducible check, not a claim that an environment has been
load-tested. Existing historic performance documents are not refreshed by these
synthetic contracts.
