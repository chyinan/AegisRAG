# Observability & Monitoring

AegisRAG ships with Prometheus metrics and a pre-configured Grafana dashboard for
real-time API observability.

## Architecture

```
AegisRAG API (:8000/metrics)  →  Prometheus (:9090)  →  Grafana (:3101)
```

## Quick Start

```powershell
# Ensure observability stack is running
docker compose --env-file .env -f docker/compose.yaml up -d prometheus grafana
```

- **Grafana**: http://localhost:3101 (admin / admin)
- **Prometheus**: http://localhost:9090
- **Metrics endpoint**: http://localhost:8000/metrics

The "AegisRAG — API Observability" dashboard is auto-provisioned.

## Dashboard Panels

| Panel | Type | Description |
|-------|------|-------------|
| Request Rate | Stat | Overall requests/second |
| Avg Latency | Stat | Average API latency in ms |
| Error Rate | Stat | 5xx error percentage |
| Requests/sec by Endpoint | Time series | Throughput per handler |
| Latency by Endpoint (p95) | Time series | p95 latency per endpoint |
| Status Codes Distribution | Bar gauge | HTTP status code breakdown |
| Request Size Distribution | Heatmap | Request body size distribution |
| Slowest Endpoints (p99) | Table | Top slow endpoints |

## Metrics Collected

Prometheus metrics exposed by the FastAPI app via `prometheus-fastapi-instrumentator`:

- `http_requests_total` — request counter by method, handler, status
- `http_request_duration_seconds` — latency histogram
- `http_request_size_bytes` — request body size histogram
- `http_response_size_bytes` — response body size histogram

## Load Testing

The [closed-loop measurement guide](LOAD_MEASUREMENT.md) describes the
existing development-header workload, its authorization limits, schema-2
reports and nonzero failure exits. Run only against an approved isolated
development target; do not relax server authentication to use this utility.
Requests may invoke paid providers configured by that target.

```powershell
# Defaults: 10 workers, 30-second pair-dispatch window
python evaluation/load_test.py --api-url http://localhost:8000

# Explicit workload; started request pairs drain after the dispatch window
python evaluation/load_test.py --users 50 --duration 60 --request-timeout 120
```

The actual elapsed time, including drain and think time, determines the
reported completion rate. Client latency includes failed terminal attempts;
inspect error counts and the labeled percentile algorithm before comparing
results. HTTP success does not validate answers or establish service capacity.

### Historical sample (5 concurrent users, 20s, reported DeepSeek backend)

These pre-existing figures are preserved, not rerun or independently verified
by the current offline contracts. A matching source revision, workload, raw
report and environment record are needed before using them as current evidence.

| Endpoint | p50 Latency | p95 Latency | Throughput | Success Rate |
|----------|:-----------:|:-----------:|:----------:|:------------:|
| `/retrieve` | 79ms | 2,582ms | 0.5 req/s | 100% |
| `/query` (end-to-end) | 6,257ms | 16,200ms | 0.5 req/s | 100% |

> The original sample attributed `/query` latency to external generation.
> Whether a local model is faster depends on its hardware, model and workload;
> no such comparison was measured in this acceptance run.

## Adding Custom Metrics

To add business-level metrics (e.g., RAG retrieval count, citation accuracy), 
use the instrumentator's `add_metric()`:

```python
from prometheus_client import Counter, Histogram

rag_retrieval_count = Counter("rag_retrieval_total", "Total RAG retrievals")
rag_faithfulness = Histogram("rag_faithfulness_score", "Faithfulness score")
```
