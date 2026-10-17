from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.coverage_provenance import PHASES, record


def test_complete_coverage_retains_checkout_head_base_and_line_counts(tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    xml.write_text('<coverage lines-valid="100" lines-covered="98"/>')
    result = record(
        xml,
        {
            "GITHUB_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40,
            "PR_BASE_SHA": "c" * 40,
            "COVERAGE_PHASES": json.dumps(dict.fromkeys(PHASES, "success")),
        },
    )
    assert result["checkout_sha"] == "a" * 40
    assert result["pull_request_head_sha"] == "b" * 40
    assert result["pull_request_base_sha"] == "c" * 40
    assert result["all_phases_succeeded"] is True
    assert result["report_complete"] is True
    assert result["xml_sha256"] == hashlib.sha256(xml.read_bytes()).hexdigest()
    assert result["lines_valid"] == 100 and result["lines_covered"] == 98


def test_partial_or_missing_reports_cannot_be_mistaken_for_complete(tmp_path: Path) -> None:
    result = record(
        tmp_path / "absent.xml",
        {
            "GITHUB_SHA": "private-invalid-value",
            "COVERAGE_PHASES": '{"unit":"success","framework":"failure"}',
        },
    )
    assert result["report_present"] is False
    assert result["all_phases_succeeded"] is False
    assert result["report_complete"] is False
    assert result["checkout_sha"] is None
    assert "private-invalid-value" not in repr(result)
    assert "lines_covered" not in result


def test_invalid_phase_shape_is_rejected_instead_of_marked_complete(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="JSON object"):
        record(tmp_path / "absent.xml", {"COVERAGE_PHASES": "[]"})


def test_ci_preserves_final_coverage_evidence_and_fails_upload_errors() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text()
    assert "fail_ci_if_error: true" in workflow
    assert "fail_ci_if_error: false" not in workflow
    evidence = workflow.split("      - name: Upload Python coverage evidence\n", 1)[1].split(
        "      - name:", 1
    )[0]
    assert "if: always()" in evidence
    assert "coverage.xml" in evidence and ".coverage" in evidence
    assert "coverage-provenance.json" in evidence
    assert workflow.index("Record Python coverage provenance") > workflow.index(
        "Actual Ragas offline contracts"
    )
