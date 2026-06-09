from __future__ import annotations

import pytest

from packages.diagnostics.dto import (
    DiagnosticsLookupRequest,
    DiagnosticsReport,
    DiagnosticsStageSummary,
    DiagnosticsSummary,
    FailureStage,
)
from packages.diagnostics.exceptions import DIAGNOSTICS_INVALID_LOOKUP, DiagnosticsError


def test_lookup_request_requires_request_or_trace_id() -> None:
    with pytest.raises(DiagnosticsError) as exc_info:
        DiagnosticsLookupRequest()

    assert exc_info.value.code == DIAGNOSTICS_INVALID_LOOKUP
    assert exc_info.value.status_code == 400
    assert "query" not in str(exc_info.value.details).lower()


def test_lookup_request_normalizes_ids_and_include_report() -> None:
    request = DiagnosticsLookupRequest(
        request_id=" req-1 ",
        trace_id=" trace-1 ",
        include_report=True,
    )

    assert request.request_id == "req-1"
    assert request.trace_id == "trace-1"
    assert request.include_report is True


def test_summary_and_report_dump_only_safe_allowlisted_fields() -> None:
    summary = DiagnosticsSummary(
        tenant_id="tenant-1",
        user_id="user-1",
        request_id="req-1",
        trace_id="trace-1",
        action="rag.query",
        status="failure",
        top_k=5,
        result_count=2,
        highest_rerank_score=0.91,
        citation_count=1,
        context_item_count=3,
        context_source_count=2,
        generation_provider="fake",
        generation_model="fake-model",
        generation_version="fake-v1",
        prompt_token_count=11,
        completion_token_count=7,
        total_token_count=18,
        event_count=4,
        latency_ms=123.4,
        failure_stage=FailureStage.GENERATION,
        error_code="LLM_PROVIDER_FAILED",
    )
    report = DiagnosticsReport(
        lookup=DiagnosticsLookupRequest(request_id="req-1", include_report=True),
        summary=summary,
        stages=(
            DiagnosticsStageSummary(
                name=FailureStage.GENERATION,
                status="failure",
                latency_ms=50.0,
                error_code="LLM_PROVIDER_FAILED",
                counts={"total_token_count": 18},
            ),
        ),
        next_steps=("python -m pytest tests/unit/rag/test_query_service.py -q",),
        generated_at="2026-06-09T00:00:00+08:00",
    )

    payload = report.model_dump(mode="json")

    assert payload["summary"]["request_id"] == "req-1"
    assert payload["stages"][0]["counts"] == {"total_token_count": 18}
    assert "full query" not in str(payload).lower()
    assert "query text" not in str(payload).lower()
    assert "answer" not in str(payload).lower()
    assert "chunk_content" not in str(payload).lower()
    assert "source_uri" not in str(payload).lower()
    assert "provider_raw_response" not in str(payload).lower()


def test_stage_summary_accepts_stable_retrieval_timeline_stages_and_safe_decisions() -> None:
    stage = DiagnosticsStageSummary(
        name=FailureStage.RRF_MERGE,
        status="degraded",
        latency_ms=3.5,
        error_code="RRF_THRESHOLD_FILTERED",
        counts={
            "dense_input_count": 8,
            "sparse_input_count": 6,
            "deduped_count": 5,
            "filtered_count": 0,
            "threshold_decision": "no_answer",
        },
    )

    payload = stage.model_dump(mode="json")

    assert FailureStage.SPARSE_RETRIEVAL.value == "sparse_retrieval"
    assert payload == {
        "name": "rrf_merge",
        "status": "degraded",
        "latency_ms": 3.5,
        "error_code": "RRF_THRESHOLD_FILTERED",
        "counts": {
            "dense_input_count": 8,
            "sparse_input_count": 6,
            "deduped_count": 5,
            "filtered_count": 0,
            "threshold_decision": "no_answer",
        },
    }
    assert "query" not in str(payload).lower()
    assert "chunk" not in str(payload).lower()
    assert "sql" not in str(payload).lower()


def test_stage_summary_rejects_unallowlisted_count_keys() -> None:
    with pytest.raises(ValueError, match="count key"):
        DiagnosticsStageSummary(
            name=FailureStage.RERANK,
            status="failure",
            counts={"provider_payload_count": 1},
        )


def test_stage_summary_rejects_raw_error_code_text() -> None:
    with pytest.raises(ValueError, match="error_code"):
        DiagnosticsStageSummary(
            name=FailureStage.RRF_MERGE,
            status="failure",
            error_code="select * from chunks where tenant_id = 'secret'",
        )
