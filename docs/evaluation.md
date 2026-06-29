# RAG Evaluation Guide

AegisRAG uses [RAGAS](https://docs.ragas.io) (Retrieval Augmented Generation Assessment)
to measure pipeline quality against four industry-standard metrics.

## Metrics

| Metric | Measures | Target |
|--------|----------|--------|
| **Context Precision** | Are retrieved chunks relevant to the question? | > 0.70 |
| **Context Recall** | Did we retrieve all relevant information? | > 0.70 |
| **Faithfulness** | Is the answer grounded in retrieved context? | > 0.80 |
| **Answer Relevancy** | Does the answer address the question? | > 0.70 |

### Metric Definitions

**Context Precision** — The proportion of retrieved chunks that are actually
relevant to the query. Low precision means the pipeline is retrieving noise.

**Context Recall** — The proportion of relevant chunks that were successfully
retrieved. Low recall means the pipeline is missing important information.

**Faithfulness** — Whether every claim in the generated answer can be attributed
to the retrieved context. Low faithfulness means hallucinations.

**Answer Relevancy** — Whether the answer directly addresses the user's question
rather than going off-topic or providing tangential information.

## Quick Start

### 1. Install dependencies

```powershell
uv sync --group eval
```

### 2. Prepare a dataset

Create a JSON file following this format:

```json
{
  "dataset_name": "my_domain_qa",
  "dataset_version": "v1",
  "cases": [
    {
      "case_id": "q01",
      "question": "What is the return policy?",
      "reference_answer": "Returns are accepted within 30 days with receipt.",
      "reference_contexts": [
        "Our return policy allows returns within 30 days of purchase."
      ]
    }
  ]
}
```

- `reference_answer` and `reference_contexts` are optional but improve metric accuracy.
- `reference_contexts` is used for context recall scoring.

### 3. Run evaluation

```powershell
python evaluation/evaluate.py \
  --api-url http://localhost:8000 \
  --dataset evaluation/dataset/sample.json \
  --output evaluation/reports/
```

The evaluator sends each question to the AegisRAG API, collects answers and
retrieved contexts, then computes metrics using the configured judge LLM.

### 4. Interpret results

```text
==================================================
Passed: 4/5
  context_precision: ✅ 0.8500
  context_recall:    ✅ 0.7800
  faithfulness:      ✅ 0.9200
  answer_relevancy:  ❌ 0.6500
==================================================
```

In this example, answer relevancy is below threshold — the LLM may be generating
off-topic responses. Check the per-question details in the markdown report.

### Minimal Evaluation (Lightweight)

For quick evaluation without full RAGAS dependency resolution, use
`evaluation/eval_minimal.py`:

```powershell
python evaluation/eval_minimal.py
```

This script:
- Queries the AegisRAG API directly (`/query` + `/retrieve`)
- Fetches actual chunk text from PostgreSQL
- Uses DeepSeek as LLM-judge for Faithfulness and Precision scoring
- Outputs per-question and aggregate scores

### Reranker Configuration for Evaluation

Set `RERANK_PROVIDER=llm` in docker-compose to use the LLM-based reranker.
Latest benchmark (12 docs, DeepSeek judge, LLM reranker):

| Metric | Score |
|--------|:-----:|
| Faithfulness | 1.00 ✅ |
| Context Precision | 0.56 ⚠️ |

Faithfulness at 1.00 means zero hallucinations — every claim is traceable to
retrieved context. Precision can be further improved by switching to a
dedicated cross-encoder reranker (BGE-Reranker-v2-m3 via
`RERANK_PROVIDER=openai_compatible`).

## Benchmarking

Compare different retrieval configurations:

```powershell
python evaluation/benchmark.py \
  --api-url http://localhost:8000 \
  --dataset evaluation/dataset/sample.json \
  --configs default,high_recall,strict
```

Pre-defined configs:
- `default` — top_k=10, no threshold
- `high_recall` — top_k=20, no threshold
- `strict` — top_k=5, score_threshold=0.7

Custom configs:

```powershell
python evaluation/benchmark.py \
  --dataset evaluation/dataset/sample.json \
  --custom-config '{"name":"rerank_on","top_k":10}' \
  --configs default
```

## Configuration

### Judge Model

The evaluator uses an LLM to judge quality. Configure via environment variables:

```powershell
# Use OpenAI
$env:RAGAS_LLM_MODEL = "gpt-4o-mini"
$env:RAGAS_LLM_API_KEY = "sk-..."

# Use local model via OpenAI-compatible endpoint
$env:RAGAS_LLM_MODEL = "qwen2.5-7b"
$env:RAGAS_LLM_BASE_URL = "http://localhost:8080/v1"
$env:RAGAS_LLM_API_KEY = "not-needed"
```

### Pass Threshold

```powershell
python evaluation/evaluate.py --pass-threshold 0.75 ...
```

Cases with average metric score below threshold are marked as failed.

### Custom Metrics

```powershell
python evaluation/evaluate.py --metrics faithfulness,answer_relevancy ...
```

Available: `context_precision`, `context_recall`, `faithfulness`, `answer_relevancy`.

## CI Integration

Add to your CI pipeline:

```yaml
- name: RAG Quality Gate
  run: |
    uv sync --group eval
    python evaluation/evaluate.py \
      --api-url ${{ secrets.RAG_API_URL }} \
      --dataset evaluation/dataset/ci_smoke.json \
      --pass-threshold 0.75
```

The script exits with code 1 if any case fails the threshold.

## Output Format

### JSON Report

```json
{
  "report_type": "rag_quality_runner",
  "generated_at": "2026-06-27T12:00:00Z",
  "summary": {
    "case_count": 5,
    "passed_count": 4,
    "failed_count": 1,
    "context_precision": 0.85,
    "context_recall": 0.78,
    "faithfulness": 0.92,
    "answer_relevancy": 0.65
  },
  "cases": [
    {
      "case_id": "q01",
      "question": "...",
      "answer": "...",
      "passed": true,
      "scores": {
        "faithfulness": 0.95,
        "answer_relevancy": 0.88
      }
    }
  ]
}
```

The JSON format is compatible with the Eval Evidence API (`GET /eval/reports`).

### Markdown Report

A human-readable report is also generated at `evaluation/reports/eval_*.md`
with per-question breakdowns and score tables.

## Reproducing Results

1. Start AegisRAG: `docker compose --env-file .env -f docker/compose.yaml up -d`
2. Ingest seed data (if using demo corpus)
3. Run: `python evaluation/evaluate.py --dataset evaluation/dataset/sample.json`
4. Reports at `evaluation/reports/`

To reproduce benchmark results across configurations, run `benchmark.py` with
the same dataset and `--repeat 3` for stability.
