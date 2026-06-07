from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tests.eval.rag.loader import load_rag_eval_dataset
from tests.eval.rag.run_dataset_smoke import main
from tests.eval.rag.run_smoke import main as run_rag_smoke

DATASET = Path("tests/eval/datasets/rag_smoke.json")


def test_rag_smoke_dataset_loads_and_summarizes_without_external_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_socket(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("rag smoke dataset must not open network sockets")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    exit_code = main(
        [
            "--dataset",
            str(DATASET),
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    reports = list(tmp_path.glob("rag-dataset-smoke-*.json"))
    assert len(reports) == 1
    report_text = reports[0].read_text(encoding="utf-8")
    dataset = load_rag_eval_dataset(DATASET)
    for case in dataset.cases:
        assert case.query not in report_text
    for record in dataset.corpus:
        assert record.content not in report_text


def test_rag_smoke_dataset_has_required_category_distribution() -> None:
    dataset = load_rag_eval_dataset(DATASET)
    categories = {category: 0 for category in ("policy", "product_manual", "faq", "technical_doc")}
    for case in dataset.cases:
        categories[case.category] += 1

    assert categories == {
        "policy": 5,
        "product_manual": 5,
        "faq": 5,
        "technical_doc": 5,
    }


def test_rag_quality_runner_executes_real_dataset_with_safe_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_socket(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("rag quality runner must not open network sockets")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    exit_code = run_rag_smoke(
        [
            "--dataset",
            str(DATASET),
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    reports = list(tmp_path.glob("rag-smoke-*.json"))
    assert len(reports) == 1
    report_text = reports[0].read_text(encoding="utf-8")
    dataset = load_rag_eval_dataset(DATASET)
    for case in dataset.cases:
        assert case.query not in report_text
    for record in dataset.corpus:
        assert record.content not in report_text
