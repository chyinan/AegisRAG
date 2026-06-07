from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import get_args

from tests.eval.rag.dto import FailureStage
from tests.eval.rag.loader import load_rag_eval_dataset
from tests.eval.rag.reporting import (
    build_rag_eval_report,
    summarize_rag_eval_dataset,
    write_json_report,
    write_rag_eval_report,
)
from tests.eval.rag.runner import run_rag_eval

DATASET = Path("tests/eval/datasets/rag_smoke.json")


def test_summarize_rag_eval_dataset_uses_safe_fields_only() -> None:
    dataset = load_rag_eval_dataset(DATASET)

    report = summarize_rag_eval_dataset(dataset)
    dumped = report.model_dump_json()
    report_strings = set(_strings(report.model_dump(mode="json")))

    assert report.summary.case_count == 20
    assert report.summary.answerable_count >= 16
    assert report.summary.no_answer_count >= 2
    assert report.summary.acl_case_count >= 2
    assert report.summary.prompt_injection_case_count >= 2
    assert report.summary.citation_expected_count >= 3
    assert "query" not in dumped
    assert "content" not in dumped
    assert "must_include_terms" not in dumped
    assert "raw prompt" not in dumped.lower()
    assert "Bearer " not in dumped
    assert "sk-" not in dumped
    assert "C:\\" not in dumped
    for case in dataset.cases:
        assert case.query not in report_strings
        answer_terms = (
            case.expected_answer.must_include_terms + case.expected_answer.must_not_include_terms
        )
        for term in answer_terms:
            assert term not in report_strings
    for record in dataset.corpus:
        assert record.content not in report_strings


def test_write_json_report_uses_safe_report_payload(tmp_path: Path) -> None:
    dataset = load_rag_eval_dataset(DATASET)
    report = summarize_rag_eval_dataset(dataset)

    report_path = write_json_report(report, report_dir=tmp_path)

    payload_text = report_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    payload_strings = set(_strings(payload))
    assert payload["summary"]["case_count"] == 20
    assert payload["summary"]["dataset_version"] == "rag-smoke-v1"
    assert str(tmp_path) not in payload_text
    for case in dataset.cases:
        assert case.query not in payload_strings
    for record in dataset.corpus:
        assert record.content not in payload_strings


def test_write_json_report_does_not_overwrite_same_second_reports(tmp_path: Path) -> None:
    dataset = load_rag_eval_dataset(DATASET)
    report = summarize_rag_eval_dataset(dataset)

    first_path = write_json_report(report, report_dir=tmp_path)
    second_path = write_json_report(report, report_dir=tmp_path)

    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()


def test_write_rag_eval_report_uses_safe_full_runner_payload(tmp_path: Path) -> None:
    dataset = load_rag_eval_dataset(DATASET)
    runner_report = asyncio.run(run_rag_eval(dataset.cases, dataset.corpus))
    report = build_rag_eval_report(
        results=runner_report.cases,
        summary=runner_report.summary,
    )

    report_path = write_rag_eval_report(report, report_dir=tmp_path)

    payload_text = report_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    payload_strings = set(_strings(payload))
    assert payload["report_type"] == "rag_quality_runner"
    assert payload["summary"]["case_count"] == 20
    assert str(tmp_path) not in payload_text
    for case in dataset.cases:
        assert case.query not in payload_strings
    for record in dataset.corpus:
        assert record.content not in payload_strings


def test_write_rag_eval_report_does_not_overwrite_same_second_reports(tmp_path: Path) -> None:
    dataset = load_rag_eval_dataset(DATASET)
    report = asyncio.run(run_rag_eval(dataset.cases[:1], dataset.corpus))

    first_path = write_rag_eval_report(report, report_dir=tmp_path)
    second_path = write_rag_eval_report(report, report_dir=tmp_path)

    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()


def test_failure_stage_enum_reserves_future_runner_stages() -> None:
    stages = set(get_args(FailureStage))

    assert {
        "retrieval",
        "rerank",
        "context_packing",
        "prompt_build",
        "generation",
        "citation",
        "permission",
        "no_answer",
        "dataset",
        "runner",
    } <= stages


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(item for nested in value.values() for item in _strings(nested))
    if isinstance(value, list | tuple):
        return tuple(item for nested in value for item in _strings(nested))
    return ()
