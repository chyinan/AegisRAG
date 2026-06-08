from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from time import perf_counter as default_perf_counter
from typing import Protocol

from packages.agent.dto import (
    AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE,
    AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION,
    AGENT_FINAL_ANSWER_VALIDATION_FAILED,
    AgentCitationRef,
    FinalAnswerValidationRequest,
    FinalAnswerValidationResult,
    ToolInvocationStatus,
)
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext

logger = logging.getLogger(__name__)

FINAL_ANSWER_VALIDATION_ACTION = "agent.final_answer_validation"
_SOURCE_LIKE_TEXT = re.compile(
    r"("
    r"\b(document_id|version_id|chunk_id|source)\b\s*[:=]"
    r"|\[[^\]]*\b(doc|chunk|source)[^\]]*\]"
    r"|\bdoc[-_][A-Za-z0-9_.:-]+\b.*\bchunk[-_][A-Za-z0-9_.:-]+\b"
    r"|\bdoc[-_][A-Za-z0-9_.\-]+:[A-Za-z0-9_.\-]+:chunk[-_][A-Za-z0-9_.\-]+\b"
    r"|\bpage\s+\d+\b"
    r")",
    re.I,
)


class AgentObservationEvidence(Protocol):
    tool_name: str
    status: ToolInvocationStatus
    citation_refs: tuple[AgentCitationRef, ...]
    error_code: str | None
    result_status: str | None


class FinalAnswerValidator(Protocol):
    async def validate(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: FinalAnswerValidationRequest,
        observations: Sequence[AgentObservationEvidence],
    ) -> FinalAnswerValidationResult: ...


class StrictFinalAnswerValidator:
    def __init__(
        self,
        *,
        audit: AuditPort | None,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._audit = audit
        self._perf_counter = perf_counter or default_perf_counter

    async def validate(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: FinalAnswerValidationRequest,
        observations: Sequence[AgentObservationEvidence],
    ) -> FinalAnswerValidationResult:
        started = self._perf_counter()
        try:
            result = self._validate(
                request=request,
                observations=observations,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
            )
        except Exception:
            result = FinalAnswerValidationResult(
                status="invalid",
                answer=None,
                citations=(),
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=AGENT_FINAL_ANSWER_VALIDATION_FAILED,
                metadata={
                    "validation_status": "invalid",
                    "error_code": AGENT_FINAL_ANSWER_VALIDATION_FAILED,
                },
            )
        await self._record_validation_audit(context=context, request=request, result=result)
        return result

    def _validate(
        self,
        *,
        request: FinalAnswerValidationRequest,
        observations: Sequence[AgentObservationEvidence],
        latency_ms: float,
    ) -> FinalAnswerValidationResult:
        if not request.answer.strip():
            return _invalid_result(
                latency_ms=latency_ms,
                error_code=AGENT_FINAL_ANSWER_VALIDATION_FAILED,
                validated_citation_count=0,
                unsupported_citation_count=0,
                failed_tool_reference_count=0,
                citations=(),
            )
        if not request.citations and _SOURCE_LIKE_TEXT.search(request.answer):
            return _invalid_result(
                latency_ms=latency_ms,
                error_code=AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION,
                validated_citation_count=0,
                unsupported_citation_count=1,
                failed_tool_reference_count=0,
                citations=(),
            )
        failed_reference_count = _failed_tool_reference_count(request.citations, observations)
        unsupported = tuple(
            citation
            for citation in request.citations
            if not _citation_is_supported(citation, observations)
        )

        if failed_reference_count:
            return _invalid_result(
                latency_ms=latency_ms,
                error_code=AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE,
                validated_citation_count=len(request.citations) - len(unsupported),
                unsupported_citation_count=len(unsupported),
                failed_tool_reference_count=failed_reference_count,
                citations=request.citations,
            )

        if unsupported:
            return _invalid_result(
                latency_ms=latency_ms,
                error_code=AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION,
                validated_citation_count=len(request.citations) - len(unsupported),
                unsupported_citation_count=len(unsupported),
                failed_tool_reference_count=0,
                citations=request.citations,
            )

        return FinalAnswerValidationResult(
            status="valid",
            answer=request.answer,
            citations=request.citations,
            latency_ms=latency_ms,
            error_code=None,
            validated_citation_count=len(request.citations),
            unsupported_citation_count=0,
            failed_tool_reference_count=0,
            metadata=_safe_validation_metadata(
                status="valid",
                error_code=None,
                citations=request.citations,
                validated_citation_count=len(request.citations),
                unsupported_citation_count=0,
                failed_tool_reference_count=0,
            ),
        )

    async def _record_validation_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: FinalAnswerValidationRequest,
        result: FinalAnswerValidationResult,
    ) -> None:
        if self._audit is None:
            return
        status = _audit_status(result.status)
        resource_id = request.agent_run_id or context.request_id
        try:
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action=FINAL_ANSWER_VALIDATION_ACTION,
                    resource=AuditResource(
                        type="agent_run",
                        id=resource_id,
                        metadata={"agent_run_id": resource_id},
                    ),
                    status=status,
                    latency_ms=result.latency_ms,
                    error_code=result.error_code,
                    metadata=dict(result.metadata),
                )
            )
        except Exception as exc:
            logger.warning(
                "agent.final_answer_validation.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "agent_run_id": resource_id,
                    "validation_status": result.status,
                    "error_code": result.error_code,
                    "audit_error_type": type(exc).__name__,
                },
            )


def _allowed_rag_search_evidence(
    observations: Sequence[AgentObservationEvidence],
) -> set[tuple[str, str, str, str | None, int | None, int | None]]:
    evidence: set[tuple[str, str, str, str | None, int | None, int | None]] = set()
    for observation in observations:
        if observation.tool_name != "rag_search":
            continue
        if observation.status is not ToolInvocationStatus.SUCCESS:
            continue
        if observation.result_status not in (None, "success"):
            continue
        evidence.update(citation.evidence_key for citation in observation.citation_refs)
    return evidence


def _citation_is_supported(
    citation: AgentCitationRef,
    observations: Sequence[AgentObservationEvidence],
) -> bool:
    if citation.tool_name not in (None, "rag_search"):
        return False
    if citation.observation_index is not None:
        if citation.observation_index >= len(observations):
            return False
        observation = observations[citation.observation_index]
        return _successful_rag_search_observation_contains(observation, citation)

    return any(
        _successful_rag_search_observation_contains(observation, citation)
        for observation in observations
    )


def _successful_rag_search_observation_contains(
    observation: AgentObservationEvidence,
    citation: AgentCitationRef,
) -> bool:
    if observation.tool_name != "rag_search":
        return False
    if observation.status is not ToolInvocationStatus.SUCCESS:
        return False
    if observation.error_code is not None:
        return False
    if observation.result_status not in (None, "success"):
        return False
    return any(ref.evidence_key == citation.evidence_key for ref in observation.citation_refs)


def _failed_tool_reference_count(
    citations: Sequence[AgentCitationRef],
    observations: Sequence[AgentObservationEvidence],
) -> int:
    count = 0
    for citation in citations:
        if citation.observation_index is None:
            if any(
                _failed_observation_contains_citation(observation, citation)
                for observation in observations
            ):
                count += 1
            continue
        if citation.observation_index >= len(observations):
            count += 1
            continue
        observation = observations[citation.observation_index]
        if _observation_is_failed_or_non_rag(observation):
            count += 1
    return count


def _failed_observation_contains_citation(
    observation: AgentObservationEvidence,
    citation: AgentCitationRef,
) -> bool:
    if not any(ref.evidence_key == citation.evidence_key for ref in observation.citation_refs):
        return False
    return _observation_is_failed_or_non_rag(observation)


def _observation_is_failed_or_non_rag(observation: AgentObservationEvidence) -> bool:
    if observation.tool_name != "rag_search":
        return True
    if observation.status is not ToolInvocationStatus.SUCCESS:
        return True
    if observation.error_code is not None:
        return True
    return observation.result_status not in (None, "success")


def _invalid_result(
    *,
    latency_ms: float,
    error_code: str,
    validated_citation_count: int,
    unsupported_citation_count: int,
    failed_tool_reference_count: int,
    citations: Sequence[AgentCitationRef],
) -> FinalAnswerValidationResult:
    return FinalAnswerValidationResult(
        status="invalid",
        answer=None,
        citations=(),
        latency_ms=latency_ms,
        error_code=error_code,
        validated_citation_count=max(validated_citation_count, 0),
        unsupported_citation_count=unsupported_citation_count,
        failed_tool_reference_count=failed_tool_reference_count,
        metadata=_safe_validation_metadata(
            status="invalid",
            error_code=error_code,
            citations=citations,
            validated_citation_count=max(validated_citation_count, 0),
            unsupported_citation_count=unsupported_citation_count,
            failed_tool_reference_count=failed_tool_reference_count,
        ),
    )


def _safe_validation_metadata(
    *,
    status: str,
    error_code: str | None,
    citations: Sequence[AgentCitationRef],
    validated_citation_count: int,
    unsupported_citation_count: int,
    failed_tool_reference_count: int,
) -> dict[str, object]:
    return {
        "validation_status": status,
        "error_code": error_code,
        "validated_citation_count": validated_citation_count,
        "unsupported_citation_count": unsupported_citation_count,
        "failed_tool_reference_count": failed_tool_reference_count,
        "citation_refs": list(_safe_citation_identifiers(citations)),
    }


def _safe_citation_identifiers(
    citations: Iterable[AgentCitationRef],
) -> Iterable[dict[str, object]]:
    for citation in citations:
        item: dict[str, object] = {
            "document_id": citation.document_id,
            "version_id": citation.version_id,
            "chunk_id": citation.chunk_id,
        }
        if citation.page_start is not None:
            item["page_start"] = citation.page_start
        if citation.page_end is not None:
            item["page_end"] = citation.page_end
        if citation.tool_name is not None:
            item["tool_name"] = citation.tool_name
        if citation.observation_index is not None:
            item["observation_index"] = citation.observation_index
        yield item


def _audit_status(status: str) -> AuditStatus:
    if status == "valid":
        return AuditStatus.SUCCESS
    if status == "degraded":
        return AuditStatus.DENIED
    return AuditStatus.FAILURE


def _elapsed_ms(elapsed_seconds: float) -> float:
    return round(max(elapsed_seconds, 0.0) * 1000, 3)
