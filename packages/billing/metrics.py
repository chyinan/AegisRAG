"""Process-local Prometheus counters; the SQL ledger remains authoritative."""

from prometheus_client import Counter

USAGE_TOKENS = Counter(
    "aegis_llm_usage_tokens_total",
    "Reported raw LLM tokens",
    ("provider", "model", "kind"),
)
PRICED_COST = Counter(
    "aegis_llm_priced_cost_total",
    "Configured-rate LLM cost",
    ("provider", "model", "currency"),
)
UNPRICED_CALLS = Counter(
    "aegis_llm_unpriced_calls_total",
    "LLM calls without a usable configured price",
    ("provider", "model"),
)
UNKNOWN_USAGE_CALLS = Counter(
    "aegis_llm_unknown_usage_calls_total",
    "LLM calls with unavailable usage",
    ("provider", "model"),
)
