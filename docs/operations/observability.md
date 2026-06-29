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

Benchmark concurrent user load with `evaluation/load_test.py`:

```powershell
# Quick smoke test (5 users, 20 seconds)
python evaluation/load_test.py

# Stress test (50 users, 60 seconds)
python evaluation/load_test.py --users 50 --duration 60
```

### Sample Results (5 concurrent users, 20s test, DeepSeek backend)

| Endpoint | p50 Latency | p95 Latency | Throughput | Success Rate |
|----------|:-----------:|:-----------:|:----------:|:------------:|
| `/retrieve` | 79ms | 2,582ms | 0.5 req/s | 100% |
| `/query` (end-to-end) | 6,257ms | 16,200ms | 0.5 req/s | 100% |

> Note: `/query` latency is dominated by external LLM calls (DeepSeek API). With a
> local LLM, p50 latency would be significantly lower.

## Adding Custom Metrics

To add business-level metrics (e.g., RAG retrieval count, citation accuracy), 
use the instrumentator's `add_metric()`:

```python
from prometheus_client import Counter, Histogram

rag_retrieval_count = Counter("rag_retrieval_total", "Total RAG retrievals")
rag_faithfulness = Histogram("rag_faithfulness_score", "Faithfulness score")
```
