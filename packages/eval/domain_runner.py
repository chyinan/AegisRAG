"""Trusted, same-generation domain evaluation with safe evidence and regression gates.

No API reconstruction, secondary retrieval, database access, or judge is performed.
Raw captures live only in the caller's memory; reports never serialize their text.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, ValidationError

from packages.common.context import AuthenticatedRequestContext
from packages.eval.domain_dataset import (
    METRICS,
    Digest,
    DomainCase,
    DomainDataset,
    DomainEvaluationError,
    DomainProfile,
    FrozenModel,
    Identifier,
    _read_json,
    configuration_digest,
    sha256_bytes,
)
from packages.rag.capture import CapturedQuery
from packages.rag.dto import QueryCommand
from packages.rag.query import RagQueryApplicationService

CONTRACT_VERSION: Literal["domain-capture-v1"] = "domain-capture-v1"


@dataclass(frozen=True)
class DomainApplicationBinding:
    """Operator-owned composition, with verified corpus and effective config hashes.

    A live factory must authenticate normally and verify the ingested corpus/config
    before returning this binding. Hashes are attestations, not permission grants.
    """

    service: RagQueryApplicationService
    context_factory: Callable[[str], AuthenticatedRequestContext]
    corpus_sha256: str
    configuration_sha256: str
    mode: Literal["offline_contract", "live_application"]


class DomainIdentity(FrozenModel):
    contract_version: Literal["domain-capture-v1"] = CONTRACT_VERSION
    dataset_id: Identifier
    dataset_version: Identifier
    corpus_id: Identifier
    corpus_version: Identifier
    manifest_sha256: Digest
    cases_sha256: Digest
    corpus_sha256: Digest
    profile_id: Identifier
    profile_version: Identifier
    profile_sha256: Digest
    configuration_sha256: Digest
    gate_policy_sha256: Digest
    mode: Literal["offline_contract", "live_application"]


class SafeContextEvidence(FrozenModel):
    document_id: str
    version_id: str
    chunk_ids: tuple[str, ...]
    sha256: Digest


class DomainCaseEvidence(FrozenModel):
    case_id: Identifier
    passed: bool
    error_code: Literal["query_failed", "capture_inconsistent", "expectation_missed"] | None = None
    no_answer: bool | None = None
    answer_sha256: Digest | None = None
    prompt_sha256: Digest | None = None
    contexts: tuple[SafeContextEvidence, ...] = ()
    citation_chunk_ids: tuple[str, ...] = ()
    no_answer_correctness: float = Field(ge=0, le=1)
    citation_coverage: float | None = Field(default=None, ge=0, le=1)
    answer_required_terms: float | None = Field(default=None, ge=0, le=1)


class DomainReport(FrozenModel):
    report_version: Literal[1] = 1
    report_type: Literal["frozen_domain_evaluation"] = "frozen_domain_evaluation"
    generated_at: str
    identity: DomainIdentity
    case_count: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    aggregate_scores: dict[str, float]
    cases: tuple[DomainCaseEvidence, ...]

    @property
    def absolute_passed(self) -> bool:
        return self.passed_count == self.case_count


class DomainGateMetric(FrozenModel):
    metric: str
    actual: float
    minimum: float
    baseline: float
    delta: float
    max_drop: float
    passed: bool


class DomainRegressionDecision(FrozenModel):
    decision: Literal["pass", "fail", "incomparable"]
    reason: Literal["checked", "identity_mismatch", "case_set_mismatch"]
    failed_case_ids: tuple[str, ...] = ()
    metrics: tuple[DomainGateMetric, ...] = ()


def evaluation_identity(
    dataset: DomainDataset,
    profile: DomainProfile,
    profile_sha256: str,
) -> DomainIdentity:
    manifest = dataset.manifest
    return DomainIdentity(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        corpus_id=manifest.corpus_id,
        corpus_version=manifest.corpus_version,
        manifest_sha256=dataset.manifest_sha256,
        cases_sha256=manifest.files["cases.json"],
        corpus_sha256=manifest.files["corpus.json"],
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_sha256=profile_sha256,
        configuration_sha256=profile.configuration_sha256,
        gate_policy_sha256=configuration_digest(profile.model_dump(mode="json")),
        mode=profile.mode,
    )


async def run_domain_evaluation(
    *,
    dataset: DomainDataset,
    profile: DomainProfile,
    profile_sha256: str,
    binding: DomainApplicationBinding,
    capture_consumer: Callable[[str, CapturedQuery], None] | None = None,
) -> DomainReport:
    """Run one authenticated production query/capture per case, with no re-query."""
    identity = evaluation_identity(dataset, profile, profile_sha256)
    if (
        binding.corpus_sha256 != identity.corpus_sha256
        or binding.configuration_sha256 != identity.configuration_sha256
        or binding.mode != identity.mode
    ):
        raise DomainEvaluationError("domain_binding_identity_mismatch")
    results: list[DomainCaseEvidence] = []
    for case in dataset.cases:
        try:
            async with asyncio.timeout(profile.query_timeout_seconds):
                context = binding.context_factory(f"domain-{uuid4().hex}")
                captured = await binding.service.query_with_capture(
                    context=context,
                    command=QueryCommand(query=case.question, top_k=profile.top_k),
                )
        except Exception:
            # Keep a failed case in every applicable denominator. Exception text
            # can contain prompts, configuration, private text, or provider bodies.
            results.append(
                DomainCaseEvidence(
                    case_id=case.case_id,
                    passed=False,
                    error_code="query_failed",
                    no_answer_correctness=0.0,
                    citation_coverage=None if case.expected_no_answer else 0.0,
                    answer_required_terms=None if case.expected_no_answer else 0.0,
                )
            )
            continue
        if capture_consumer is not None:
            capture_consumer(case.case_id, captured)
        results.append(
            score_domain_capture(dataset=dataset, case=case, captured=captured, profile=profile)
        )
    aggregate = {
        metric: _mean(
            [float(value) for result in results if (value := getattr(result, metric)) is not None]
        )
        for metric in METRICS
    }
    return DomainReport(
        generated_at=datetime.now(tz=UTC).isoformat(),
        identity=identity,
        case_count=len(results),
        passed_count=sum(result.passed for result in results),
        aggregate_scores=aggregate,
        cases=tuple(results),
    )


def score_domain_capture(
    *,
    dataset: DomainDataset,
    case: DomainCase,
    captured: CapturedQuery,
    profile: DomainProfile,
) -> DomainCaseEvidence:
    response = captured.response
    captured_ids = {
        (item.document_id, item.version_id, chunk_id)
        for item in captured.contexts
        for chunk_id in item.chunk_ids
    }
    citation_ids = {
        (citation.document_id, citation.version_id, citation.chunk_id)
        for citation in response.citations
    }
    consistent = (
        not citation_ids - captured_ids
        and not response.unsupported_claims
        and (not response.no_answer or not response.citations)
        and bool(captured.contexts) == (captured.prompt_sha256 is not None)
        and captured.capture_version == "packed-context-v1"
        and _capture_matches_corpus(dataset=dataset, captured=captured)
    )
    no_answer_score = float(response.no_answer == case.expected_no_answer)
    citation_score: float | None = None
    terms_score: float | None = None
    if not case.expected_no_answer:
        expected = set(case.expected_chunk_ids)
        actual = {citation.chunk_id for citation in response.citations}
        citation_score = len(expected & actual) / len(expected) if consistent else 0.0
        normalized_answer = " ".join(response.answer.casefold().split())
        terms_score = sum(
            any(" ".join(term.casefold().split()) in normalized_answer for term in alternatives)
            for alternatives in case.required_terms
        ) / len(case.required_terms)
    scores = dict(zip(METRICS, (no_answer_score, citation_score, terms_score), strict=True))
    passed = consistent and all(
        value is None or value >= profile.minimum_scores[metric] for metric, value in scores.items()
    )
    return DomainCaseEvidence(
        case_id=case.case_id,
        passed=passed,
        error_code=None
        if passed
        else ("expectation_missed" if consistent else "capture_inconsistent"),
        no_answer=response.no_answer,
        answer_sha256=sha256_bytes(response.answer.encode()),
        prompt_sha256=captured.prompt_sha256,
        contexts=tuple(
            SafeContextEvidence(
                document_id=item.document_id,
                version_id=item.version_id,
                chunk_ids=item.chunk_ids,
                sha256=item.sha256,
            )
            for item in captured.contexts
        ),
        citation_chunk_ids=tuple(sorted(citation.chunk_id for citation in response.citations)),
        no_answer_correctness=no_answer_score,
        citation_coverage=citation_score,
        answer_required_terms=terms_score,
    )


def _capture_matches_corpus(*, dataset: DomainDataset, captured: CapturedQuery) -> bool:
    corpus = {
        (chunk.document_id, chunk.version_id, chunk.chunk_id): chunk for chunk in dataset.corpus
    }
    for item in captured.contexts:
        identities = [(item.document_id, item.version_id, chunk_id) for chunk_id in item.chunk_ids]
        if not identities or any(identity not in corpus for identity in identities):
            return False
        # ContextPacker joins adjacent source chunks with one newline. This
        # explicit contract detects ordinary corpus/version drift at generation.
        if item.content != "\n".join(corpus[identity].content for identity in identities):
            return False
    return True


def compare_domain_baseline(
    *,
    report: DomainReport,
    baseline: DomainReport,
    profile: DomainProfile,
) -> DomainRegressionDecision:
    """Fail closed on different inputs/settings; gate aggregate AND per-case drops."""
    if report.identity != baseline.identity or (
        report.identity.gate_policy_sha256 != configuration_digest(profile.model_dump(mode="json"))
    ):
        return DomainRegressionDecision(decision="incomparable", reason="identity_mismatch")
    current = {case.case_id: case for case in report.cases}
    previous = {case.case_id: case for case in baseline.cases}
    if (
        len(current) != len(report.cases)
        or len(previous) != len(baseline.cases)
        or (set(current) != set(previous))
    ):
        return DomainRegressionDecision(decision="incomparable", reason="case_set_mismatch")
    failed = {case.case_id for case in report.cases if not case.passed}
    for case_id, case in current.items():
        for metric in METRICS:
            actual, prior = getattr(case, metric), getattr(previous[case_id], metric)
            if (actual is None) != (prior is None):
                return DomainRegressionDecision(decision="incomparable", reason="case_set_mismatch")
            if (
                actual is not None
                and prior is not None
                and actual + profile.max_baseline_drop[metric] + 1e-12 < prior
            ):
                failed.add(case_id)
    metrics = tuple(
        DomainGateMetric(
            metric=metric,
            actual=report.aggregate_scores[metric],
            minimum=profile.minimum_scores[metric],
            baseline=baseline.aggregate_scores[metric],
            delta=report.aggregate_scores[metric] - baseline.aggregate_scores[metric],
            max_drop=profile.max_baseline_drop[metric],
            passed=report.aggregate_scores[metric] >= profile.minimum_scores[metric]
            and (
                report.aggregate_scores[metric] + profile.max_baseline_drop[metric] + 1e-12
                >= baseline.aggregate_scores[metric]
            ),
        )
        for metric in METRICS
    )
    return DomainRegressionDecision(
        decision="pass" if not failed and all(metric.passed for metric in metrics) else "fail",
        reason="checked",
        failed_case_ids=tuple(sorted(failed)),
        metrics=metrics,
    )


def load_domain_report(path: Path) -> DomainReport:
    _, value = _read_json(path)
    try:
        report = DomainReport.model_validate(value)
        if set(report.aggregate_scores) != set(METRICS):
            raise DomainEvaluationError("domain_report_invalid")
        if report.case_count != len(report.cases) or report.passed_count != sum(
            case.passed for case in report.cases
        ):
            raise DomainEvaluationError("domain_report_invalid")
        if len({case.case_id for case in report.cases}) != report.case_count:
            raise DomainEvaluationError("domain_report_invalid")
        for metric in METRICS:
            values = [
                float(value)
                for case in report.cases
                if (value := getattr(case, metric)) is not None
            ]
            if not values or abs(_mean(values) - report.aggregate_scores[metric]) > 1e-12:
                raise DomainEvaluationError("domain_report_invalid")
        return report
    except ValidationError as exc:
        raise DomainEvaluationError("domain_report_invalid") from exc


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
