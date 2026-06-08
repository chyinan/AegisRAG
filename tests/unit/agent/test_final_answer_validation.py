from __future__ import annotations

import pytest

from packages.agent.dto import (
    AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE,
    AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION,
    AgentCitationRef,
    FinalAnswerValidationRequest,
    ToolInvocationStatus,
)
from packages.agent.final_answer import (
    FINAL_ANSWER_VALIDATION_ACTION,
    StrictFinalAnswerValidator,
)
from packages.agent.runtime import AgentObservationSummary
from packages.auth.context import AuthContext
from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext


@pytest.mark.asyncio
async def test_validator_accepts_citations_from_successful_rag_search_observation() -> None:
    audit = InMemoryAuditPort()
    citation = _citation(observation_index=0)
    validator = StrictFinalAnswerValidator(audit=audit, perf_counter=lambda: 10.0)

    result = await validator.validate(
        context=_context(),
        request=FinalAnswerValidationRequest(
            agent_run_id="run-1",
            answer="The policy requires approval.",
            citations=(citation,),
        ),
        observations=(
            AgentObservationSummary(
                tool_name="rag_search",
                status=ToolInvocationStatus.SUCCESS,
                citation_refs=(citation,),
                result_status="success",
                latency_ms=2.0,
            ),
        ),
    )

    assert result.status == "valid"
    assert result.answer == "The policy requires approval."
    assert result.citations == (citation,)
    assert result.validated_citation_count == 1
    assert result.unsupported_citation_count == 0
    assert audit.events[-1].action == FINAL_ANSWER_VALIDATION_ACTION
    assert audit.events[-1].status is AuditStatus.SUCCESS
    assert audit.events[-1].resource.id == "run-1"
    assert "The policy requires approval" not in str(audit.events[-1].metadata)


@pytest.mark.asyncio
async def test_validator_rejects_invented_or_cross_run_citation() -> None:
    citation = _citation(document_id="doc-invented", observation_index=None)
    validator = StrictFinalAnswerValidator(audit=InMemoryAuditPort(), perf_counter=lambda: 10.0)

    result = await validator.validate(
        context=_context(),
        request=FinalAnswerValidationRequest(
            agent_run_id="run-1",
            answer="Unsupported answer.",
            citations=(citation,),
        ),
        observations=(
            AgentObservationSummary(
                tool_name="rag_search",
                status=ToolInvocationStatus.SUCCESS,
                citation_refs=(_citation(),),
                result_status="success",
                latency_ms=2.0,
            ),
        ),
    )

    assert result.status == "invalid"
    assert result.answer is None
    assert result.error_code == AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION
    assert result.unsupported_citation_count == 1
    assert "Unsupported answer" not in str(result.metadata)


@pytest.mark.asyncio
async def test_validator_rejects_source_like_text_without_structured_citations() -> None:
    validator = StrictFinalAnswerValidator(audit=InMemoryAuditPort(), perf_counter=lambda: 10.0)

    result = await validator.validate(
        context=_context(),
        request=FinalAnswerValidationRequest(
            agent_run_id="run-1",
            answer="Approved. document_id=doc-1 chunk_id=chunk-1",
            citations=(),
        ),
        observations=(),
    )

    assert result.status == "invalid"
    assert result.error_code == AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION
    assert result.unsupported_citation_count == 1
    assert "doc-1" not in str(result.metadata)


@pytest.mark.parametrize(
    ("status", "result_status", "error_code"),
    [
        (ToolInvocationStatus.FAILURE, "error", "TOOL_HANDLER_FAILED"),
        (ToolInvocationStatus.DENIED, None, "TOOL_PERMISSION_DENIED"),
        (ToolInvocationStatus.SUCCESS, "error", "DOMAIN_ERROR"),
    ],
)
@pytest.mark.asyncio
async def test_validator_rejects_failed_denied_or_structured_error_tool_references(
    status: ToolInvocationStatus,
    result_status: str | None,
    error_code: str | None,
) -> None:
    citation = _citation(observation_index=0)
    audit = InMemoryAuditPort()
    validator = StrictFinalAnswerValidator(audit=audit, perf_counter=lambda: 10.0)

    result = await validator.validate(
        context=_context(),
        request=FinalAnswerValidationRequest(
            agent_run_id="run-1",
            answer="Do not trust failed tool output.",
            citations=(citation,),
        ),
        observations=(
            AgentObservationSummary(
                tool_name="rag_search",
                status=status,
                citation_refs=(citation,),
                result_status=result_status,
                error_code=error_code,
                latency_ms=2.0,
            ),
        ),
    )

    assert result.status == "invalid"
    assert result.answer is None
    assert result.error_code == AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE
    assert result.failed_tool_reference_count == 1
    assert audit.events[-1].status is AuditStatus.FAILURE
    assert "Do not trust failed tool output" not in str(audit.events[-1].metadata)


def test_validation_result_metadata_forbids_raw_payload_fields() -> None:
    from pydantic import ValidationError

    from packages.agent.dto import FinalAnswerValidationResult

    with pytest.raises(ValidationError):
        FinalAnswerValidationResult(
            status="valid",
            answer="safe",
            latency_ms=1.0,
            metadata={"raw_answer": "do not persist"},
        )


def _citation(
    *,
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    chunk_id: str = "chunk-1",
    observation_index: int | None = 0,
) -> AgentCitationRef:
    return AgentCitationRef(
        document_id=document_id,
        version_id=version_id,
        chunk_id=chunk_id,
        source="policy",
        page_start=1,
        page_end=1,
        tool_name="rag_search",
        observation_index=observation_index,
    )


def _context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-1",
            roles=("analyst",),
            department="risk",
            permissions=("agent:run",),
        ),
    )
