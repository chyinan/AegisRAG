from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from time import perf_counter

from packages.auth.context import AuthContext
from packages.retrieval.dto import (
    MAX_RETRIEVAL_TOP_K,
    RetrievalCandidate,
    RetrievalFilterSet,
    RetrievalRequest,
    RetrievalResult,
)
from packages.retrieval.exceptions import RETRIEVAL_FORBIDDEN_FILTER, RetrievalError
from packages.retrieval.ports import CandidateRetriever
from packages.retrieval.service import RetrievalService
from tests.eval.retrieval.dto import (
    AttackType,
    FailureStage,
    RetrievalEvalCase,
    RetrievalEvalCaseResult,
    RetrievalEvalCorpusRecord,
    RetrievalEvalReport,
    RetrievalEvalReportSummary,
)
from tests.eval.retrieval.loader import RetrievalEvalDatasetError
from tests.eval.retrieval.reporting import build_report, write_json_report


class FixtureCandidateRetriever:
    def __init__(self, corpus: Sequence[RetrievalEvalCorpusRecord]) -> None:
        self._corpus = tuple(corpus)
        self.calls = 0

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        self.calls += 1
        case_id = request.request_id.removeprefix("eval-")
        candidates: list[RetrievalCandidate] = []
        for record in self._corpus:
            if case_id not in record.relevant_case_ids:
                continue
            candidates.append(
                RetrievalCandidate(
                    document_id=record.document_id,
                    version_id=record.version_id,
                    chunk_id=record.chunk_id,
                    source="synthetic",
                    source_type=record.source_type,
                    source_uri=record.source_uri,
                    page_start=record.page_start,
                    page_end=record.page_end,
                    title_path=record.title_path,
                    score=record.score,
                    retrieval_method=record.retrieval_method,
                    tenant_id=record.tenant_id,
                    acl=record.acl,
                    metadata=record.metadata,
                )
            )
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return candidates


async def run_retrieval_eval(
    cases: Sequence[RetrievalEvalCase],
    *,
    retriever: CandidateRetriever | None = None,
    service: RetrievalService | None = None,
    report_dir: Path | None = None,
    report_path: Path | None = None,
    top_k: int | None = None,
    counter: Callable[[], float] = perf_counter,
) -> RetrievalEvalReport:
    if not cases:
        raise RetrievalEvalDatasetError(code="empty_case_set", details={"case_count": 0})
    if top_k is not None:
        _validate_top_k_override(top_k)
    if service is None:
        service = RetrievalService(retriever=retriever or FixtureCandidateRetriever(()))

    results: list[RetrievalEvalCaseResult] = []
    for case in cases:
        request_top_k = top_k if top_k is not None else case.top_k
        request = RetrievalRequest(
            query=case.query,
            top_k=request_top_k,
            metadata_filter=case.metadata_filter,
            request_id=f"eval-{case.case_id}",
            trace_id=f"trace-{case.case_id}",
        )
        auth = AuthContext(
            user_id=case.user_id,
            tenant_id=case.tenant_id,
            roles=case.roles,
            department=case.department,
            permissions=case.permissions,
        )
        started = counter()
        try:
            result = await service.retrieve(request=request, auth=auth)
        except RetrievalError as exc:
            latency_ms = max((counter() - started) * 1000, 0.0)
            results.append(_failed_case_result(case, request_top_k, latency_ms, exc))
            continue
        latency_ms = max((counter() - started) * 1000, 0.0)
        result = result.model_copy(update={"latency_ms": latency_ms})
        results.append(evaluate_case(case, result))

    case_results = tuple(results)
    summary = _build_summary(cases=tuple(cases), results=case_results)
    report = build_report(results=case_results, summary=summary)
    if report_dir is not None or report_path is not None:
        write_json_report(report, report_dir=report_dir, report_path=report_path)
    return report


def _validate_top_k_override(top_k: int) -> None:
    if isinstance(top_k, bool) or top_k <= 0 or top_k > MAX_RETRIEVAL_TOP_K:
        raise RetrievalEvalDatasetError(
            code="invalid_top_k_override",
            details={"top_k": top_k, "max_top_k": MAX_RETRIEVAL_TOP_K},
        )


def evaluate_case(
    case: RetrievalEvalCase,
    result: RetrievalResult,
) -> RetrievalEvalCaseResult:
    expected_documents = set(case.expected_documents)
    expected_chunks = set(case.expected_chunks)
    matched_documents = tuple(
        sorted(
            {
                candidate.document_id
                for candidate in result.candidates
                if candidate.document_id in expected_documents
            }
        )
    )
    matched_chunks = tuple(
        sorted(
            {
                candidate.chunk_id
                for candidate in result.candidates
                if candidate.chunk_id in expected_chunks
            }
        )
    )
    has_expected_hit = bool(matched_documents or matched_chunks)

    if case.answerable:
        passed = has_expected_hit
        failure_stage: FailureStage | None = None if passed else "threshold"
    else:
        passed = not has_expected_hit
        failure_stage = None if passed else _failure_stage_for_attack(case.attack_type)

    return RetrievalEvalCaseResult(
        case_id=case.case_id,
        request_id=result.request_id,
        trace_id=result.trace_id,
        tenant_id=result.tenant_id,
        user_id=result.user_id,
        top_k=result.top_k,
        latency_ms=result.latency_ms or 0.0,
        passed=passed,
        failure_stage=failure_stage,
        matched_documents=matched_documents,
        matched_chunks=matched_chunks,
    )


def _failed_case_result(
    case: RetrievalEvalCase,
    top_k: int,
    latency_ms: float,
    exc: RetrievalError,
) -> RetrievalEvalCaseResult:
    failure_stage: FailureStage = (
        "permission" if exc.code == RETRIEVAL_FORBIDDEN_FILTER else "runner"
    )
    return RetrievalEvalCaseResult(
        case_id=case.case_id,
        request_id=f"eval-{case.case_id}",
        trace_id=f"trace-{case.case_id}",
        tenant_id=case.tenant_id,
        user_id=case.user_id,
        top_k=top_k,
        latency_ms=latency_ms,
        passed=False,
        failure_stage=failure_stage,
    )


def _failure_stage_for_attack(attack_type: AttackType) -> FailureStage:
    if attack_type == "acl_isolation":
        return "permission"
    if attack_type == "prompt_injection":
        return "runner"
    return "no_answer"


def _build_summary(
    *,
    cases: tuple[RetrievalEvalCase, ...],
    results: tuple[RetrievalEvalCaseResult, ...],
) -> RetrievalEvalReportSummary:
    result_by_case_id = {result.case_id: result for result in results}
    passed_count = sum(1 for result in results if result.passed)
    answerable_cases = [case for case in cases if case.answerable]
    answerable_passed = sum(
        1 for case in answerable_cases if result_by_case_id[case.case_id].passed
    )
    top_k_values = tuple(sorted({result.top_k for result in results}))
    average_latency = (
        sum(result.latency_ms for result in results) / len(results)
        if results
        else 0.0
    )
    return RetrievalEvalReportSummary(
        case_count=len(results),
        passed_count=passed_count,
        failed_count=len(results) - passed_count,
        retrieval_hit_rate=answerable_passed / len(answerable_cases) if answerable_cases else 1.0,
        acl_isolation_passed=_all_attack_type_passed("acl_isolation", cases, result_by_case_id),
        no_answer_passed=all(
            result_by_case_id[case.case_id].passed for case in cases if not case.answerable
        ),
        prompt_injection_passed=_all_attack_type_passed(
            "prompt_injection",
            cases,
            result_by_case_id,
        ),
        average_latency_ms=average_latency,
        top_k={
            "min": min(top_k_values) if top_k_values else 0,
            "max": max(top_k_values) if top_k_values else 0,
            "values": top_k_values,
        },
    )


def _all_attack_type_passed(
    attack_type: AttackType,
    cases: tuple[RetrievalEvalCase, ...],
    result_by_case_id: dict[str, RetrievalEvalCaseResult],
) -> bool:
    matching = [case for case in cases if case.attack_type == attack_type]
    return all(result_by_case_id[case.case_id].passed for case in matching)
