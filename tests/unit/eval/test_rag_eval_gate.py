from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.eval.rag.dto import RagEvalReportSummary
from tests.eval.rag.gate import (
    RagEvalGateConfig,
    RagEvalGateError,
    RagEvalGateThresholds,
    decide_rag_eval_gate,
    load_rag_eval_gate_config,
    write_rag_eval_gate_report,
)


def test_thresholds_reject_bool_numbers_unknown_fields_and_invalid_rates() -> None:
    with pytest.raises(ValidationError):
        RagEvalGateThresholds.model_validate(
            {
                "min_retrieval_hit_rate": True,
                "min_citation_coverage": 0.9,
                "min_no_answer_correctness": 0.85,
                "require_acl_isolation_passed": True,
                "require_prompt_injection_passed": True,
                "max_failed_count": 0,
            }
        )

    with pytest.raises(ValidationError):
        RagEvalGateThresholds.model_validate(
            {
                "min_retrieval_hit_rate": 0.8,
                "min_citation_coverage": 1.1,
                "min_no_answer_correctness": 0.85,
                "require_acl_isolation_passed": True,
                "require_prompt_injection_passed": True,
                "max_failed_count": 0,
                "unexpected": "field",
            }
        )

    with pytest.raises(ValidationError):
        RagEvalGateThresholds.model_validate(
            {
                "min_retrieval_hit_rate": 0.8,
                "min_citation_coverage": 0.9,
                "min_no_answer_correctness": 0.85,
                "require_acl_isolation_passed": True,
                "require_prompt_injection_passed": True,
                "max_failed_count": -1,
            }
        )


def test_load_gate_config_reports_safe_validation_error(tmp_path: Path) -> None:
    config_path = tmp_path / "secret-config.json"
    config_path.write_text(
        json.dumps(
            {
                "gate_name": "rag-ci-smoke",
                "config_id": "broken-v1",
                "thresholds": {
                    "min_retrieval_hit_rate": True,
                    "min_citation_coverage": 0.9,
                    "min_no_answer_correctness": 0.85,
                    "require_acl_isolation_passed": True,
                    "require_prompt_injection_passed": True,
                    "max_failed_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RagEvalGateError) as exc_info:
        load_rag_eval_gate_config(config_path)

    message = str(exc_info.value)
    assert exc_info.value.code == "invalid_gate_config"
    assert "secret-config.json" in message
    assert str(tmp_path) not in message
    assert "min_retrieval_hit_rate" in message
    assert '{"thresholds"' not in message


def test_decide_rag_eval_gate_passes_when_summary_meets_thresholds() -> None:
    config = _config()

    decision = decide_rag_eval_gate(
        summary=_summary(),
        failure_cases=(),
        config=config,
    )

    assert decision.passed is True
    assert decision.failed_metric_names == ()
    assert all(metric.passed for metric in decision.metrics)


def test_decide_rag_eval_gate_fails_with_safe_metric_details() -> None:
    decision = decide_rag_eval_gate(
        summary=_summary(
            retrieval_hit_rate=0.5,
            citation_coverage=0.75,
            failed_count=1,
            acl_isolation_passed=False,
        ),
        failure_cases=(("case-1", "retrieval"),),
        config=_config(),
    )

    assert decision.passed is False
    assert decision.failed_metric_names == (
        "retrieval_hit_rate",
        "citation_coverage",
        "acl_isolation_passed",
        "failed_count",
    )
    assert decision.failed_case_ids == ("case-1",)
    assert decision.failure_stages == ("retrieval",)
    assert "query" not in decision.model_dump_json()


def test_write_gate_report_contains_git_config_dataset_and_safe_runner_summary(
    tmp_path: Path,
) -> None:
    report_path = write_rag_eval_gate_report(
        runner_summary=_summary(),
        decision=decide_rag_eval_gate(summary=_summary(), failure_cases=(), config=_config()),
        config=_config(),
        dataset_path=Path("tests/eval/datasets/rag_smoke.json"),
        report_dir=tmp_path,
        commit_sha="abc123",
        branch="feature/eval-gate",
    )

    payload_text = report_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert payload["report_type"] == "rag_ci_smoke_gate"
    assert payload["commit_sha"] == "abc123"
    assert payload["branch"] == "feature/eval-gate"
    assert payload["dataset"]["path"] == "tests/eval/datasets/rag_smoke.json"
    assert payload["config"]["gate_name"] == "rag-ci-smoke"
    assert payload["runner_summary"]["case_count"] == 20
    assert str(tmp_path) not in payload_text
    assert "query" not in payload_text
    assert "full answer" not in payload_text.lower()
    assert "chunk content" not in payload_text
    assert report_path.name.startswith("rag-ci-smoke-")


def test_write_gate_report_does_not_overwrite_same_second_reports(tmp_path: Path) -> None:
    first_path = write_rag_eval_gate_report(
        runner_summary=_summary(),
        decision=decide_rag_eval_gate(summary=_summary(), failure_cases=(), config=_config()),
        config=_config(),
        dataset_path=Path("tests/eval/datasets/rag_smoke.json"),
        report_dir=tmp_path,
        commit_sha="abc123",
        branch="main",
    )
    second_path = write_rag_eval_gate_report(
        runner_summary=_summary(),
        decision=decide_rag_eval_gate(summary=_summary(), failure_cases=(), config=_config()),
        config=_config(),
        dataset_path=Path("tests/eval/datasets/rag_smoke.json"),
        report_dir=tmp_path,
        commit_sha="abc123",
        branch="main",
    )

    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()


def _config() -> RagEvalGateConfig:
    return RagEvalGateConfig(
        gate_name="rag-ci-smoke",
        config_id="prd-success-metrics-v1",
        thresholds=RagEvalGateThresholds(
            min_retrieval_hit_rate=0.8,
            min_citation_coverage=0.9,
            min_no_answer_correctness=0.85,
            require_acl_isolation_passed=True,
            require_prompt_injection_passed=True,
            max_failed_count=0,
        ),
    )


def _summary(
    *,
    retrieval_hit_rate: float = 0.9,
    citation_coverage: float = 1.0,
    no_answer_correctness: float = 1.0,
    failed_count: int = 0,
    acl_isolation_passed: bool = True,
    prompt_injection_passed: bool = True,
) -> RagEvalReportSummary:
    return RagEvalReportSummary(
        case_count=20,
        passed_count=20 - failed_count,
        failed_count=failed_count,
        retrieval_hit_rate=retrieval_hit_rate,
        citation_coverage=citation_coverage,
        required_citation_count=4,
        matched_required_citation_count=4,
        no_answer_correctness=no_answer_correctness,
        no_answer_case_count=2,
        acl_isolation_passed=acl_isolation_passed,
        prompt_injection_passed=prompt_injection_passed,
        average_latency_ms=1.2,
    )
