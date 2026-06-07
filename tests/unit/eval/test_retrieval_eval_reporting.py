from __future__ import annotations

import json
from pathlib import Path

from tests.eval.retrieval.dto import (
    FailureStage,
    RetrievalEvalCaseResult,
    RetrievalEvalReportSummary,
)
from tests.eval.retrieval.reporting import build_report, write_json_report


def test_build_report_summarizes_metrics_without_sensitive_fields() -> None:
    report = build_report(
        results=(
            _case_result("case-hit", passed=True, failure_stage=None),
            _case_result("case-miss", passed=False, failure_stage="threshold"),
        ),
        summary=RetrievalEvalReportSummary(
            case_count=2,
            passed_count=1,
            failed_count=1,
            retrieval_hit_rate=0.5,
            acl_isolation_passed=True,
            no_answer_passed=True,
            prompt_injection_passed=True,
            average_latency_ms=1.5,
            top_k={"min": 5, "max": 5, "values": (5,)},
        ),
    )

    dumped = report.model_dump_json()

    assert "case-hit" in dumped
    assert "query" not in dumped
    assert "chunk content" not in dumped.lower()
    assert report.summary.failed_count == 1


def test_write_json_report_uses_safe_relative_filename(tmp_path: Path) -> None:
    report = build_report(
        results=(_case_result("case-hit", passed=True, failure_stage=None),),
        summary=RetrievalEvalReportSummary(
            case_count=1,
            passed_count=1,
            failed_count=0,
            retrieval_hit_rate=1.0,
            acl_isolation_passed=True,
            no_answer_passed=True,
            prompt_injection_passed=True,
            average_latency_ms=1.0,
            top_k={"min": 5, "max": 5, "values": (5,)},
        ),
    )

    report_path = write_json_report(report, report_dir=tmp_path)

    assert report_path.parent == tmp_path
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["case_count"] == 1
    assert str(tmp_path) not in json.dumps(payload)


def _case_result(
    case_id: str,
    *,
    passed: bool,
    failure_stage: FailureStage | None,
) -> RetrievalEvalCaseResult:
    return RetrievalEvalCaseResult(
        case_id=case_id,
        request_id=f"eval-{case_id}",
        trace_id=f"trace-{case_id}",
        tenant_id="tenant-alpha",
        user_id="user-alpha",
        top_k=5,
        latency_ms=1.0,
        passed=passed,
        failure_stage=failure_stage,
        matched_documents=("doc-1",) if passed else (),
        matched_chunks=("chunk-1",) if passed else (),
    )
