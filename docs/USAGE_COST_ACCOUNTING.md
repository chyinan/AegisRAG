# Usage costs and monetary budgets

`packages/billing` adds provider-neutral accounting at the LLM provider boundary.
It is opt-in, does not call a pricing API, and makes no claim that the sample
rates below match a vendor. Native generation, native tool decisions, and final
streaming usage share the same ledger. The existing `TokenUsage` DTO and adapter
`usage_unavailable_count` flag remain the source of raw usage.

## Configuration

Apply migrations through `20261016_0020` before enabling accounting. Set:

```dotenv
COST_ACCOUNTING_ENABLED=true
COST_CURRENCY=USD
COST_IO_TIMEOUT_SECONDS=5
# Optional monthly tenant budget, measured in COST_CURRENCY
COST_MONTHLY_BUDGET=10
COST_MINIMUM_RESERVATION=0.01
LLM_MAX_OUTPUT_TOKENS=512
# Synthetic fake-provider example; these are not live vendor prices
COST_MODEL_PRICES_JSON='{"prices":[{"provider":"fake","model":"fake-llm","provider_version":"fake-v1","price_version":"internal-test-2026-10","currency":"USD","effective_from":"2026-10-01T00:00:00Z","input_per_million":"1.25","output_per_million":"5.00"}]}'
```

The catalog is a JSON object containing `prices`. Rates are decimal currency
units per one million input/output tokens. Currency is explicit and must match
`COST_CURRENCY`; no implicit FX conversion occurs. Each rate has an operator-owned
price version and timezone-aware effective date. Historical charges preserve a
full rate snapshot and provider/model version. Introducing a new rate requires a
new version and effective date; never mutate an already published version.
Within a model/currency/provider-version, duplicate versions or dates are rejected.
An exact provider-version rate takes precedence over a version-agnostic rate;
otherwise the newest eligible effective date wins. Rates are selected at call
start, including for streams that cross a rate-change boundary.

With no monthly budget, a missing rate allows usage collection and produces an
explicit unpriced count and null charge amount. It is never interpreted as a
zero-priced call. With a monthly budget, a missing pre-call price or output cap
fails before the provider is invoked. A positive reservation floor and enabled
accounting are required when a budget is configured.

## Accounting contract

- A fresh UUID identifies each actual provider invocation. Multiple Agent steps
  under the same request ID remain separate billable calls
- Raw input, output, and total token counts are preserved separately. Unknown
  usage stays unknown; totals are not invented from prompt text
- Money is stored as integer nanocurrency units, rounded upward once per call.
  SQL sums are exact integers. API money uses decimal strings
- Monthly UTC tenant/currency accounts atomically reserve capacity before calls.
  Concurrent requests share one counter; settlement claims the call once and
  atomically moves its hold to actual spend. Repeated settlement cannot double-book
- Reservation is the larger of the configured floor and an input/output cost
  forecast. The input forecast uses UTF-8 bytes plus an overhead allowance and
  includes native tool schemas; output uses the explicit request token cap
- These forecasts are operational safeguards, not a tokenizer/invoice guarantee.
  Actual usage can exceed a hold, for example with provider-specific overhead or
  an unexpected reported model version. Actual cost is still booked, negative
  remaining budget and over-budget status are exposed, and later calls are blocked
- Cancellation, provider failures, process loss, missing streaming usage, and
  unpriceable usage retain their pending reservation. Do not automatically release
  those holds or retry a charged provider call merely to repair accounting.
  Operator review of provider usage is required before correcting an uncertain
  hold; automated reconciliation or invoice import is not included
- A final streaming usage record is saved before the final chunk is forwarded.
  Closing before final records an incomplete/unknown-usage call. Settlement already
  in progress is shielded from cancellation and bounded by the I/O timeout
- A pre-call ledger failure prevents the provider invocation. Post-call persistence
  failure is a visible structured error, leaving the existing hold for review
- Adapter-internal retries may incur unreported provider charges. Only returned
  usage is priced. Use provider invoices for authoritative reconciliation

The ledger contains identifiers, operation, tokens, status, latency, and rate
snapshots. It does not persist prompts, answer text, API keys, or tool payloads.

## APIs and indicators

Authenticated callers with `cost:read` or `audit:read` can read:

- `GET /v1/costs/summary?start=<ISO8601>&end=<ISO8601>`: defaults to the current
  UTC month through now. Explicit intervals require timezones and may cover up to
  366 days. The end is exclusive
- `GET /v1/costs/budget`: current UTC month, configured limit, actual spend,
  outstanding reservations, remaining capacity, and over-budget state

Summary fields include successful/failed/incomplete provider calls, unknown usage,
unpriced calls, raw token totals, priced cost, pending reserved cost, success rate,
average observed latency, and priced cost per successful model call. Breakdowns
group operation, provider, model, and price version. Empty summaries return null
ratios rather than divide by zero.

The operation label is set at composition time with
`create_llm_provider(settings, operation="rag.query")`; the default is `llm`.
Applications can label query/chat/agent entrypoints without changing provider DTOs.
These are model-call indicators: a model success is not necessarily a supported
answer, and cost per successful model call is not end-to-end successful answer cost.
Partial costs have `cost_complete=false` and are explicitly identified by
unknown/unpriced/pending counts. Cost-per-success is null until the interval's
cost is complete, so an unpriced model is never presented as a free success.

`/metrics` also exposes process-local counters:

- `aegis_llm_usage_tokens_total{provider,model,kind}`
- `aegis_llm_priced_cost_total{provider,model,currency}`
- `aegis_llm_unpriced_calls_total{provider,model}`
- `aegis_llm_unknown_usage_calls_total{provider,model}`

Counters update only after committed first settlement. They reset on process
restart and are approximate float telemetry; SQL ledger/API totals are the
authoritative configured-rate account. No user/tenant identifiers are metric labels.
Structured `llm.usage.accounted` logs contain safe identifiers and counts only.

## Local verification

```bash
python -m pytest -q tests/unit/billing --no-cov
python -m ruff check packages/billing apps/api/factories/billing.py apps/api/routes/costs.py tests/unit/billing migrations/versions/20261016_0020_usage_costs.py
```

Tests use fake models and disposable SQLite databases. Coverage includes catalog
versions and decimal rounding, real RAG/Agent factory hooks, HTTP metrics reads,
stream finalization/closure, cancellation, retained uncertain holds, atomic
concurrent reservations, duplicate settlement, actual overage reporting, and
additive/reversible migration. They do not call a paid LLM API or validate real
vendor rates. PostgreSQL-specific RLS SQL is supplied by the migration; a physical
PostgreSQL integration was not exercised by these focused tests.
