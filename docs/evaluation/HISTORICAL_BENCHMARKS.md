# Historical benchmark claims retained for reference

The values below are copied from the pre-challenge README. They are historical
claims, **not measurements produced or revalidated by the framework/product
readiness changes**. This checkout does not include a matching raw report,
revision, complete workload, provider settings, hardware and warmup/cache
conditions sufficient to reproduce these specific numbers. Do not use them as
current performance, quality, capacity or resume evidence.

The separate historical ADR 0006 reports different context-precision figures;
the repository does not establish that they share a workload or metric version.
Do not combine the two tables. A finite-sample judge score of 1.0 is not a
zero-hallucination guarantee. Reported local-model timing does not establish
speed on another machine or document set.

The old invocation and rate-limit note below are retained as historical context,
not current setup instructions or authorization to change a deployment. Use the
[current sequential benchmark guide](../operations/PIPELINE_BENCHMARK.md),
[Ragas metric guide](../evaluation.md), and
[deployment acceptance](../operations/DEPLOYMENT_ACCEPTANCE.md) for distinct
measurement contracts. No endpoint was called to produce this documentation.

## Copied README record


### RAG Quality (historical LLM-judge results)

The optional Ragas text evaluator has offline contracts using real Ragas metrics
and local mock transports. Full metrics require captured answers with exact
generation contexts; the current API supports live answer-relevancy evaluation
only. Set `RAGAS_DO_NOT_TRACK=true` before starting. See the [evaluation guide](../evaluation.md)
for inputs, provider configuration, errors and privacy limits. These integration
tests do not revalidate the historical benchmark numbers below or establish
real-model quality.

| Configuration | Faithfulness | Context Precision | 
|--------------|:---:|:---:|
| Baseline (dense-only, no rerank) | 0.80 | 0.35 |
| Hybrid (dense + sparse + RRF) | 0.90 | 0.45 |
| **Full pipeline (+ HyDE + LLM Reranker)** | **1.00** | **0.56** |
| **Full pipeline (+ HyDE + BGE Local)** | **1.00** | **0.56** |

> A judge score of 1.00 applies to the evaluated samples; it is not a guarantee of zero hallucinations.
> BGE Local: `BAAI/bge-reranker-v2-m3` (568M params), CPU inference ~15s first run, ~200ms cached. Set `RERANK_PROVIDER=bge_local` in `.env`.

### Latency (12-doc knowledge base, 10-query benchmark)

| Endpoint | p50 | p95 | p99 | Avg |
|----------|:---:|:---:|:---:|:---:|
| `/retrieve` | 120ms | 250ms | 400ms | 150ms |
| `/query` (end-to-end) | 4,500ms | 7,000ms | 9,000ms | 5,200ms |

> Run: `python evaluation/benchmark_pipeline.py`

### Load Test (50 concurrent users, 60s)

| Endpoint | p50 | p95 | Throughput | Success |
|----------|:---:|:---:|:---:|:---:|
| `/retrieve` | 4,947ms | 10,308ms | 2.5 req/s | 100% |
| `/query` | 10,764ms | 17,320ms | 2.5 req/s | 100% |

> Run: `python evaluation/load_test.py --users 50 --duration 60`
> `/query` latency dominated by external DeepSeek API calls. Rate limit raised to 10,000 req/60s for this test.

