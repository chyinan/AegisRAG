# Frozen IT operations domain regression

## What this establishes

`evaluation/datasets/it_ops_v1` contains 12 authored, synthetic IT policies and
16 frozen questions: 12 single-policy questions, two multi-policy questions and
two questions without an answer in the corpus. It contains no customer documents,
real people, operational credentials, or security attack scenarios. References
and required-term alternatives were written independently of the provider.

The manifest version-locks the exact bytes of `corpus.json`, `cases.json` and
`offline_profile.json` with SHA-256. IDs, versions, all input digests, effective
configuration digest and gate-policy digest travel with each report. Changing a
file without updating its manifest is rejected. Updating the manifest also makes
the old baseline incomparable. These hashes detect changes; they are not digital
signatures or independent proof that an operator's deployment matches its claim.

The checked-in `baseline.json` is a measured **offline_contract** baseline,
16/16 cases, with the three contract metrics at 1.0. It is not a live-model,
dense/BM25 retrieval, reranking, latency, faithfulness, or product-quality result.
Its fixture lookup matches authored corpus topic terms; its fake provider
extracts the actual packed prompt text. Neither reads cases, reference answers,
required terms or expected citations. Correct fixture wiring cannot establish
real retrieval or LLM quality.

## Run the checked offline contract

From the repository root with the project's existing development environment:

```bash
python -m packages.eval.domain_cli \
  --output evaluation/reports/it-ops-v1-run-001.json

python -m pytest tests/unit/eval/test_frozen_domain_evaluation.py \
  tests/unit/rag/test_evaluation_capture.py --no-cov
```

Every case calls `RagQueryApplicationService.query_with_capture` exactly once.
It executes the real authenticated query service, fixture retrieval, real chunk
hydration, context packing, prompt building, generation abstraction and citation
extraction. No-answer cases with no packed context do not invoke the fake LLM.
The default command compares the measured run against the checked baseline.
Reports are exclusively created, never overwritten. There is no baseline-update
flag and no regeneration of references or expectations to make a failure pass.

The CLI prints safe JSON containing mode, identity, aggregate metrics, comparison
and optional report filename. The optional report contains per-case
booleans, scores, source IDs, answer/context hashes and prompt digest. It excludes
questions, answers, reference answers, full contexts, prompt text, credentials and
provider exception text. Identifiers and hashes can still be sensitive metadata;
use an appropriate local report destination. This runner never publishes reports
or expands document access.

Exit codes:

- `0`: compatible baseline comparison passed
- `1`: compatible run failed an absolute threshold or regression limit
- `2`: invalid/changed inputs, incomparable baseline, or protected/existing output
- `3`: setup/factory/report failure with a sanitized error code
- `4`: a candidate live run has no baseline, so no regression pass is claimed

## Metrics and regression rules

- `no_answer_correctness`: the returned `no_answer` flag equals the frozen
  expectation, across all 16 cases
- `citation_coverage`: fraction of expected chunk IDs present in the returned
  citations, on the 14 answerable cases. Every citation must also be present in
  the exact generation capture with matching document/version/chunk identity;
  the capture also must match the frozen corpus content and version identities
  (including the packer's newline-join contract). Unsupported claims or inconsistent
  captures fail the case
- `answer_required_terms`: fraction of frozen term-alternative groups present in
  the answer, case-insensitive with whitespace normalized, on answerable cases

The latter two metrics are deterministic reference contracts, not semantic
correctness or factual grounding scores. A long answer could include every term
without being useful. Citation coverage does not judge whether a cited passage
actually supports a claim. The two no-answer questions are ordinary out-of-corpus
questions, not a comprehensive test of unsupported-answer behavior.

A query exception or timeout becomes a failed case with a stable error code and
zero in every applicable metric; it stays in the denominator. Cancellation
propagates. A compatible run must meet every absolute threshold and every
aggregate baseline maximum drop. Per-case drops are checked as well, so gains on
one question cannot hide regressions on another. The frozen offline profile uses
1.0 minimums and zero tolerated drop. Profiles lock both thresholds and runtime
configuration identity; weakening a profile does not silently reuse its baseline.

A baseline is comparable only when its evaluation contract, dataset/corpus IDs
and versions, manifest/case/corpus hashes, mode, profile identity/hash, gate-policy
hash and configuration hash all match. The complete case-ID set must also match.
Offline and live reports cannot be compared. The CLI rejects an incompatible
baseline before importing the live factory or making generation calls.

## Trusted real-application runs

Real quality remains unmeasured until an operator provisions an approved,
isolated corpus and authorized providers. The CLI deliberately supplies no
implicit admin identity, development-header authentication, credential discovery,
second `/retrieve`, source-excerpt reconstruction, or direct SQL query.

Prepare an operator-owned live profile with the same schema as
`offline_profile.json` and `mode: "live_application"`. Choose an explicit profile
ID/version, timeout, top-k, thresholds and regression tolerances. Its
`configuration_sha256` must hash a canonical, complete **non-secret** effective
configuration using `configuration_digest`: provider/model/version, embedding
model/dimension, retrieval/fusion/rerank settings, query rewrite, packing/prompt
configuration, generation parameters and relevant index/corpus revisions.
Never put keys, tokens, personal data or credential-bearing URLs in this object.
The profile's byte hash also locks top-k, thresholds and timeout.

Supply a trusted, installed local Python module exposing an async context manager
factory, called as `factory(dataset=dataset, profile=profile)`. It must:

1. Verify the normal authenticated operator/service context and its permissions,
   and maintain the ordinary application tenant/request lifecycle
2. Verify that the intended corpus content and document/version/chunk identities
   are ingested and available through the normal retrieval/hydration chain; return
   the verified locked `corpus.json` SHA-256 as `corpus_sha256`
3. Compose the real `RagQueryApplicationService` with configured production
   provider abstractions, retrieval, repository and audit ports. The normal
   `apps.api.factories.domain.get_rag_query_application_service` async generator
   is an available composition point; own its lifetime and any tenant scope
4. Return `DomainApplicationBinding(service=..., context_factory=...,
   corpus_sha256=..., configuration_sha256=..., mode="live_application")`
5. Have `context_factory(request_id)` return a fresh
   `AuthenticatedRequestContext` for the approved identity, and close sessions,
   provider clients and other resources when the context manager exits

The runner checks the binding's declared corpus/config hashes against the locked
inputs/profile before any case. That check does not independently audit the
factory's assertions. The factory is trusted local code, explicitly imported only
for a live profile. Selecting it authorizes executing that code; use a reviewed
module. The runner does not create users, ingest data, install dependencies,
change settings or obtain additional access on the operator's behalf.

```bash
# First approved live run: safe candidate report, exit 4 (unbaselined)
python -m packages.eval.domain_cli \
  --profile operator_it_ops_profile.json \
  --factory approved_evaluation_factory:compose \
  --output evaluation/reports/it-ops-live-candidate-001.json

# After human review and explicit promotion of that candidate to a baseline:
python -m packages.eval.domain_cli \
  --profile operator_it_ops_profile.json \
  --factory approved_evaluation_factory:compose \
  --baseline reviewed_it_ops_live_baseline.json \
  --output evaluation/reports/it-ops-live-run-002.json
```

There is no bundled live baseline. No real providers were called to produce the
checked-in offline baseline. Version changes require review of inputs and a
separate new baseline; do not overwrite the v1 references or lower expectations
in response to a product regression.

## Optional Ragas over the same generation

`query_with_capture` returns `CapturedQuery`: the answer/citations plus the actual
ordered packed context text, chunk identities and prompt SHA-256 from that same
non-streaming call. Its raw context is an in-process, opt-in DTO, absent from
normal query/API/audit responses. It does not change public response schemas.

Python callers can explicitly collect captures in memory while running the domain
cases, then pass those existing captures to
`evaluate_domain_captures_with_ragas`:

```python
captures = {}
report = await run_domain_evaluation(
    dataset=dataset, profile=profile, profile_sha256=profile_sha256,
    binding=approved_binding, capture_consumer=captures.__setitem__,
)
# Only after authorizing these exact texts for the configured judge/embedding sites:
scores = await evaluate_domain_captures_with_ragas(
    dataset=dataset, captures=captures, evaluator=approved_ragas_evaluator,
)
```

The adapter binds captures to the exact ordered case IDs, uses each
`CapturedQuery.ragas_result()`, and does not call generation or retrieval again.
Duplicate question text remains bound by case order. Ragas is lazy/optional and
retains the [privacy and provider controls](../evaluation.md). The adapter returns
only named numeric judge scores, case IDs, pass flags and failure stages. It does
not automatically save or export raw captures. The generic older Ragas report
functions can contain raw text; do not use them as this runner's safe export.

A judge call sends question/answer/context/reference text to the configured
endpoints and may incur costs. It requires the operator's appropriate authority;
this adapter and the default offline CLI do not grant that permission. Judges
may score an empty-context/no-answer sample poorly or be undefined for it; keep
those judge outcomes distinct from deterministic no-answer correctness.

## Retired reconstructed-context methodology

`evaluation/eval_ragas_v2.py` is retired and exits 2 with a migration notice. Its
former `/query` followed by `/retrieve` plus container SQL did not recover the
exact ordered packed generation context. It also discovered a provider key from
a local file and could write raw answer/context reports. The retired entry point
now imports no provider SDK, reads no credentials and performs no HTTP or database
work. Do not restore that method or use its historical numbers as current quality
evidence. Existing native smoke fixtures and historical benchmarks remain
separate contracts and are not revalidated by the frozen-domain run.
