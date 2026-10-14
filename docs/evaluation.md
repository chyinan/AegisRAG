# Ragas text evaluation

The optional evaluator runs four **real Ragas 0.4.3 text metrics** with an
OpenAI-compatible judge endpoint. It is separate from the existing deterministic
`tests.eval.rag.run_ci_smoke` gates. Those gates passing does not demonstrate
that a real judge agrees with an answer.

## Supported metrics and data

| Metric | Required inputs | Model dependencies |
|---|---|---|
| `context_precision` | question, retrieved contexts, reference answer | Judge LLM |
| `context_recall` | question, retrieved contexts, reference answer | Judge LLM |
| `faithfulness` | question, generated answer, retrieved contexts | Judge LLM |
| `answer_relevancy` | question, generated answer | Judge LLM + embeddings |

`reference_contexts` may be recorded but does **not** replace `reference_answer`
for the two reference-based metrics. These metrics judge support for the reference
answer; they do not compute document-ID hit rate. An empty retrieved-context list
stays empty rather than being replaced with a fictional blank chunk.

Only the four allowlisted, single-turn **text** metrics are supported. Strings
that look like URLs or file paths remain text; the evaluator does not enable
multimodal prompts, fetch those URLs or read those files.

## Install and configure

```bash
uv sync --dev --group eval --frozen
export RAGAS_DO_NOT_TRACK=true
export RAGAS_LLM_MODEL=gpt-4o-mini
export RAGAS_LLM_API_KEY='your-judge-key'
# For an OpenAI-compatible local or alternative judge:
# export RAGAS_LLM_BASE_URL=http://localhost:8080/v1
# export RAGAS_LLM_MODEL=your-local-model

# Needed only when answer_relevancy is selected:
export RAGAS_EMBEDDING_MODEL=text-embedding-3-small
export RAGAS_EMBEDDING_API_KEY='your-embedding-key'
# export RAGAS_EMBEDDING_BASE_URL=http://localhost:8081/v1
```

PowerShell equivalent for the mandatory privacy flag:
`$env:RAGAS_DO_NOT_TRACK = "true"`.

Set the flag **before starting the process**. Ragas caches its effective tracking
setting; changing the environment in a running process may require a restart.
The evaluator fails closed if Ragas tracking, Helicone, environment-enabled
framework callback hooks (including W&B/Comet), or payload-capable SDK/framework
DEBUG logging are enabled. For judge scoring, ambient LangSmith tracers, collectors and inherited
callbacks are isolated, and caching is disabled. The evaluator does not rewrite
global environment, logging or tracing settings.

Normal judge/embedding requests still transmit the configured inputs to those
endpoints and may incur their usual fees. Use only data those providers are
authorized to receive. This is not an API-side ACL or tenant-bypass mechanism.

## Evaluate captured, exact generation contexts

The current `/query` API returns answer/citation metadata, **not the complete
packed text used for generation**. `/retrieve` omits chunk text, and
`/sources/resolve` exposes excerpts. Fetching another retrieval result or a source
excerpt cannot prove what the evaluated generation actually saw. Consequently,
the CLI does not silently reconstruct contexts or access the database directly.

Use an authorized capture from the same generation run for context-dependent
metrics. Prepare a dataset:

```json
{
  "dataset_name": "synthetic-example",
  "dataset_version": "v1",
  "cases": [{
    "case_id": "q01",
    "question": "What is the return policy?",
    "reference_answer": "Returns are accepted within 30 days with a receipt."
  }]
}
```

Prepare `responses.json`, keyed by exactly the same unique case IDs:

```json
{
  "q01": {
    "answer": "Returns are accepted within 30 days with a receipt.",
    "retrieved_contexts": ["Returns are accepted within 30 days with a receipt."]
  }
}
```

The contexts must be the actual ordered text contexts from that generation, not
reference answers or newly retrieved passages. This file is caller-supplied
evidence; the CLI cannot independently prove its provenance. Duplicate/missing
IDs, extra captures, invalid types and missing mandatory references are rejected.
Duplicate question text is allowed; captures stay bound to case IDs.

```bash
uv run --group eval python evaluation/evaluate.py \
  --dataset dataset.json --responses responses.json \
  --output evaluation/reports --pass-threshold 0.70
```

This mode does not call AegisRAG, but still calls the configured judge and, if
selected, the embedding endpoint. No paid/external judge is used by the tests.

## Live answer-relevancy only

Because answer relevancy needs no contexts, it can use the current API directly:

```bash
export RAGAS_API_KEY='your-aegis-access-token'
uv run --group eval python evaluation/evaluate.py \
  --api-url http://localhost:8000 --dataset dataset.json \
  --metrics answer_relevancy --api-timeout 60 --judge-timeout 60
```

The CLI uses authenticated `POST /query` and reads the `data.answer` envelope.
Live evaluation of context-dependent metrics fails before any API/judge request;
provide `--responses` instead. There is no new public full-context endpoint in
this change. Older auxiliary scripts (`quick_eval.py`, `evaluate_patched.py`,
`eval_minimal.py`, `benchmark.py`) are not validated entry points for this repair.

`--judge-timeout` / `RAGAS_TIMEOUT_SECONDS` bounds each metric independently.
`--api-timeout` bounds generation separately. Cancellation reaches asynchronous
judge and embedding requests; no synchronous worker thread continues a request
behind a cancelled evaluation. SDK and Ragas transport retries are disabled;
Ragas' bounded JSON-format repair may still make extra judge calls within the
metric deadline.

## Python interface

```python
from packages.eval.ragas_evaluator import EvalCase, RagasEvaluator

async def run_case(question: str) -> tuple[str, list[str]]:
    # Call your authorized pipeline and retain its exact generation contexts.
    return "Example answer", ["Example generation context"]

evaluator = RagasEvaluator(
    llm_model="your-judge-model",
    llm_base_url="http://localhost:8080/v1",
    llm_api_key="your-local-service-key",
    metrics=("faithfulness",),
    timeout_seconds=60,
    generation_timeout_seconds=60,
)
report = await evaluator.evaluate(
    cases=[EvalCase(case_id="q01", question="Example question")],
    run_fn=run_case,
)
```

The evaluator constructs row-oriented `SingleTurnSample` / `EvaluationDataset`
objects and awaits Ragas' `single_turn_ascore`. Each metric gets only its needed
dependencies. A narrow adapter prefetches answer-relevancy embeddings
asynchronously after the original Ragas question prompt, then lets the **unchanged
upstream metric** calculate the score from those vectors. It does not copy a
metric formula. This avoids Ragas 0.4.3's synchronous embedding I/O inside its
async relevance scorer, and avoids the synchronous evaluator's event-loop patch.

The optional group is pinned to the already locked Ragas 0.4.3 for this adapter.
The upstream legacy wrapper APIs emit deprecation warnings; future upgrades need
compatibility tests and a deliberate adapter change. No broader dependency
upgrade is implied. In particular, the inherited Ragas multimodal advisory
[GHSA-95ww-475f-pr4f](https://github.com/advisories/GHSA-95ww-475f-pr4f) is **not**
claimed fixed by this text-only repair. The inherited pandas lock is also unchanged.

## Results and failure semantics

- A case passes when the arithmetic mean of all requested metric scores meets
  the threshold **and** no requested metric failed
- Per-case `scores` and `aggregate_scores` keep the actual Ragas metric names
- Metric exceptions, timeouts and non-finite results become a finite `0.0` with
  a stable `metric_errors` code and `failure_stage=runner`; they fail even when
  the threshold is zero. Failed metric zeroes remain in the aggregate
- Generation failures and missing required references are failed cases with
  `generation` or `dataset` stages and no metric scores; they are excluded from
  metric means. Cancellation propagates instead of becoming a failed case
- Finite upstream values are retained, including negative cosine relevance;
  undefined/non-finite values are never emitted as JSON NaN
- `average_latency_ms` measures generation only, including failed generation
  attempts; it excludes judge time. It is zero for an empty Python input
- Reports contain questions and answers by design. Treat the local JSON/Markdown
  files and captured responses as potentially sensitive; exceptions/logs do not
  include raw provider bodies, prompts or keys
- The existing Eval Evidence reader can display counts, latency and failure
  stages. Its different `retrieval_hit_rate`, `citation_coverage` and
  `no_answer_correctness` fields remain null: these are not aliases for the
  Ragas metrics. Read the raw JSON/Markdown for actual Ragas scores

The CLI writes JSON and Markdown, exits 1 when a case fails, and rejects an empty
CLI dataset rather than reporting a vacuous quality pass. Historical benchmark
numbers elsewhere in the repository are not revalidated by this repair. A judge
score of 1.0 on a finite dataset is not a guarantee of zero hallucinations.

## Reproducible offline contracts

```bash
RAGAS_DO_NOT_TRACK=true uv run --dev --group eval --frozen pytest \
  tests/unit/eval/test_ragas_evaluator.py \
  tests/unit/eval/test_ragas_cli.py --no-cov

# Existing, separate native deterministic evaluation gates:
uv run python -m tests.eval.rag.run_ci_smoke \
  --dataset tests/eval/datasets/rag_smoke.json \
  --config tests/eval/config/rag_smoke_gate.json --report-dir tests/eval/reports
uv run python -m tests.eval.rag.run_ci_smoke --extended \
  --config tests/eval/config/rag_extended_gate.json --report-dir tests/eval/reports
```

The Ragas contracts exercise real Ragas prompts/parsers/metrics, LangChain and
OpenAI SDK serialization using deterministic local HTTP transports and synthetic
content. They block real network connections, compare the async adapter against
unmodified upstream scoring, and test aggregation, errors, timeout/cancellation,
callback isolation and the CLI-to-report path. They establish integration
correctness, not real-model answer quality or performance. CI installs the eval
group explicitly so these tests cannot silently count as optional-dependency
skips.

### Provider compatibility limits

The judge must support Ragas' JSON-style outputs and answer-relevancy's standard
three completions (`n=3`). Some OpenAI-compatible services do not implement that
parameter. Embedding inputs are sent as text with tokenizer-based automatic
splitting disabled, so no tokenizer download is needed; input longer than the
provider's limit fails the metric rather than silently using chunk-averaged
embeddings. Select compatible models/endpoints and size inputs accordingly.
