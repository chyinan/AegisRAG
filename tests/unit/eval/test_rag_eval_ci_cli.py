from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.rag import run_ci_smoke
from tests.eval.rag.dto import RagEvalReportSummary

DATASET = Path("tests/eval/datasets/rag_smoke.json")
CONFIG = Path("tests/eval/config/rag_smoke_gate.json")


def test_ci_smoke_cli_success_prints_compact_safe_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_ci_smoke.main(
        [
            "--dataset",
            str(DATASET),
            "--config",
            str(CONFIG),
            "--report-dir",
            str(tmp_path),
        ]
    )

    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert exit_code == 0
    assert payload["decision"] == "pass"
    assert payload["case_count"] == 20
    assert payload["failed_case_ids"] == []
    assert payload["report_file"].startswith("rag-ci-smoke-")
    assert list(tmp_path.glob("rag-ci-smoke-*.json"))
    assert "How many annual leave" not in stdout
    assert "Synthetic HR policy" not in stdout
    assert "D:\\" not in stdout


def test_ci_smoke_cli_threshold_failure_returns_1_and_writes_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "gate.json"
    config_path.write_text(
        json.dumps(
            {
                "gate_name": "strict-gate",
                "config_id": "strict-v1",
                "thresholds": {
                    "min_retrieval_hit_rate": 1.0,
                    "min_citation_coverage": 1.0,
                    "min_no_answer_correctness": 1.0,
                    "require_acl_isolation_passed": True,
                    "require_prompt_injection_passed": True,
                    "max_failed_count": -1,
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = run_ci_smoke.main(
        [
            "--dataset",
            str(DATASET),
            "--config",
            str(config_path),
            "--report-dir",
            str(tmp_path),
        ]
    )

    stdout = capsys.readouterr().out
    assert exit_code == 2
    assert "invalid_gate_config" in stdout
    assert str(tmp_path) not in stdout


def test_ci_smoke_cli_returns_1_for_valid_threshold_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedRunnerReport:
        summary = RagEvalReportSummary(
            case_count=20,
            passed_count=20,
            failed_count=0,
            retrieval_hit_rate=0.5,
            citation_coverage=1.0,
            required_citation_count=4,
            matched_required_citation_count=4,
            no_answer_correctness=1.0,
            no_answer_case_count=2,
            acl_isolation_passed=True,
            prompt_injection_passed=True,
            average_latency_ms=1.2,
        )
        cases = ()

    async def failed_summary_runner(*args: object, **kwargs: object) -> FailedRunnerReport:
        return FailedRunnerReport()

    monkeypatch.setattr(run_ci_smoke, "run_rag_eval", failed_summary_runner)

    config_path = tmp_path / "gate.json"
    config_path.write_text(
        json.dumps(
            {
                "gate_name": "strict-gate",
                "config_id": "strict-v1",
                "thresholds": {
                    "min_retrieval_hit_rate": 1.0,
                    "min_citation_coverage": 1.0,
                    "min_no_answer_correctness": 1.0,
                    "require_acl_isolation_passed": True,
                    "require_prompt_injection_passed": True,
                    "max_failed_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = run_ci_smoke.main(
        [
            "--dataset",
            str(DATASET),
            "--config",
            str(config_path),
            "--report-dir",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["decision"] == "fail"
    assert "retrieval_hit_rate" in payload["failed_metric_names"]
    assert payload["report_file"].startswith("rag-ci-smoke-")


def test_ci_smoke_cli_dataset_or_config_error_returns_2_without_absolute_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_ci_smoke.main(
        [
            "--dataset",
            "tests/eval/datasets/missing.json",
            "--config",
            str(CONFIG),
        ]
    )

    stdout = capsys.readouterr().out
    assert exit_code == 2
    assert "file_not_found" in stdout
    assert "D:\\" not in stdout


def test_ci_smoke_cli_unexpected_runner_error_returns_3_without_raw_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_runner(*args: object, **kwargs: object) -> object:
        raise RuntimeError("secret query C:\\Users\\person\\token.txt")

    monkeypatch.setattr(run_ci_smoke, "run_rag_eval", fail_runner)

    exit_code = run_ci_smoke.main(["--dataset", str(DATASET), "--config", str(CONFIG)])

    stdout = capsys.readouterr().out
    assert exit_code == 3
    assert "rag eval gate runner error: runner" in stdout
    assert "secret query" not in stdout
    assert "C:\\Users" not in stdout
