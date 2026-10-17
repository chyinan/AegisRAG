"""Optional judge adapter over existing same-generation in-memory captures only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from packages.eval.domain_dataset import DomainDataset, DomainEvaluationError, FrozenModel
from packages.rag.capture import CapturedQuery

if TYPE_CHECKING:
    from packages.eval.ragas_evaluator import RagasEvaluator


class SafeDomainRagasCase(FrozenModel):
    case_id: str
    scores: dict[str, float]
    passed: bool
    failure_stage: str | None


class SafeDomainRagasReport(FrozenModel):
    dataset_id: str
    dataset_version: str
    aggregate_scores: dict[str, float]
    cases: tuple[SafeDomainRagasCase, ...]


async def evaluate_domain_captures_with_ragas(
    *,
    dataset: DomainDataset,
    captures: Mapping[str, CapturedQuery],
    evaluator: RagasEvaluator,
) -> SafeDomainRagasReport:
    """Explicit opt-in transmits text to the operator-authorized judge endpoints.

    No capture is persisted, no application/service is called, and no contexts are
    reconstructed. The optional Ragas privacy/timeout controls still apply.
    """
    from packages.eval.ragas_evaluator import EvalCase

    if set(captures) != {case.case_id for case in dataset.cases}:
        raise DomainEvaluationError("domain_capture_case_mismatch")
    if any(capture.capture_version != "packed-context-v1" for capture in captures.values()):
        raise DomainEvaluationError("domain_capture_version_mismatch")
    position = 0

    async def from_capture(question: str) -> tuple[str, tuple[str, ...]]:
        nonlocal position
        if position >= len(dataset.cases) or question != dataset.cases[position].question:
            raise DomainEvaluationError("domain_capture_order_mismatch")
        case = dataset.cases[position]
        position += 1
        return captures[case.case_id].ragas_result()

    report = await evaluator.evaluate(
        cases=tuple(
            EvalCase(
                case_id=case.case_id, question=case.question, reference_answer=case.reference_answer
            )
            for case in dataset.cases
        ),
        run_fn=from_capture,
        dataset_name=dataset.manifest.dataset_id,
        dataset_version=dataset.manifest.dataset_version,
    )
    return SafeDomainRagasReport(
        dataset_id=dataset.manifest.dataset_id,
        dataset_version=dataset.manifest.dataset_version,
        aggregate_scores=dict(report.aggregate_scores),
        cases=tuple(
            SafeDomainRagasCase(
                case_id=result.case.case_id,
                scores={score.name: score.score for score in result.scores},
                passed=result.passed,
                failure_stage=result.failure_stage.value if result.failure_stage else None,
            )
            for result in report.results
        ),
    )
