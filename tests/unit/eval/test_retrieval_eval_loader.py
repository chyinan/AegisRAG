from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.retrieval.loader import (
    RetrievalEvalDatasetError,
    load_retrieval_eval_cases,
    load_retrieval_eval_corpus,
)

DATASET = Path("tests/eval/datasets/retrieval_smoke.json")


def test_loads_real_retrieval_smoke_dataset_with_required_coverage() -> None:
    cases = load_retrieval_eval_cases(DATASET)

    assert len(cases) == 20
    assert len({case.case_id for case in cases}) == 20
    assert {case.category for case in cases} == {
        "faq",
        "policy",
        "product_manual",
        "technical_doc",
    }
    assert sum(1 for case in cases if case.attack_type == "acl_isolation") >= 2
    assert sum(1 for case in cases if case.attack_type == "prompt_injection") >= 2
    assert sum(1 for case in cases if not case.answerable) >= 2


def test_rejects_duplicate_case_id_with_safe_error(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[_case("dup-case"), _case("dup-case")],
    )

    with pytest.raises(RetrievalEvalDatasetError) as exc_info:
        load_retrieval_eval_cases(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "duplicate_case_id" in message
    assert "dup-case" in message
    assert str(tmp_path) not in message


def test_rejects_invalid_case_without_leaking_query_or_secret(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[
            {
                **_case("bad-case"),
                "query": "full sensitive query with sk-test-token",
                "metadata_filter": {"$where": "tenant_id = '*'"}
            }
        ],
    )

    with pytest.raises(RetrievalEvalDatasetError) as exc_info:
        load_retrieval_eval_cases(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "invalid_case" in message
    assert "metadata_filter" in message
    assert "full sensitive query" not in message
    assert "sk-test-token" not in message
    assert str(tmp_path) not in message


def test_rejects_unsafe_case_id_without_echoing_it(tmp_path: Path) -> None:
    unsafe_case_id = "case-with-sk-test-token"
    dataset = _write_dataset(
        tmp_path,
        cases=[
            {
                **_case("safe-case"),
                "case_id": unsafe_case_id + "/path",
            }
        ],
    )

    with pytest.raises(RetrievalEvalDatasetError) as exc_info:
        load_retrieval_eval_cases(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "invalid_case" in message
    assert unsafe_case_id not in message


def test_rejects_non_string_list_items(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[
            {
                **_case("bad-list-item"),
                "roles": ["employee", 123],
            }
        ],
    )

    with pytest.raises(RetrievalEvalDatasetError) as exc_info:
        load_retrieval_eval_cases(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "invalid_case" in message
    assert "roles" in message


def test_rejects_corpus_relevant_case_ids_that_do_not_exist(tmp_path: Path) -> None:
    dataset = tmp_path / "retrieval_smoke.json"
    dataset.write_text(
        json.dumps(
            {
                "cases": [_case("known-case")],
                "corpus": [
                    {
                        "document_id": "doc-policy",
                        "version_id": "version-1",
                        "chunk_id": "chunk-policy-001",
                        "tenant_id": "tenant-alpha",
                        "title_path": ["Policy"],
                        "metadata": {"category": "policy"},
                        "relevant_case_ids": ["missing-case"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RetrievalEvalDatasetError) as exc_info:
        load_retrieval_eval_corpus(dataset)

    message = str(exc_info.value)
    assert "unknown_relevant_case_id" in message
    assert "missing-case" not in message


def test_rejects_empty_expected_ids_and_invalid_top_k(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[
            {
                **_case("bad-ids"),
                "expected_chunks": ["chunk-ok", "  "],
                "top_k": 0,
            }
        ],
    )

    with pytest.raises(RetrievalEvalDatasetError) as exc_info:
        load_retrieval_eval_cases(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "invalid_case" in message
    assert "expected_chunks" in message or "top_k" in message


def test_rejects_case_count_below_minimum(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path, cases=[_case("only-case")])

    with pytest.raises(RetrievalEvalDatasetError) as exc_info:
        load_retrieval_eval_cases(dataset)

    assert "case_count_below_minimum" in str(exc_info.value)


def test_rejects_invalid_json_with_safe_error(tmp_path: Path) -> None:
    dataset = tmp_path / "broken.json"
    dataset.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RetrievalEvalDatasetError) as exc_info:
        load_retrieval_eval_cases(dataset)

    message = str(exc_info.value)
    assert "invalid_json" in message
    assert str(tmp_path) not in message


def _write_dataset(tmp_path: Path, *, cases: list[dict[str, object]]) -> Path:
    dataset = tmp_path / "retrieval_smoke.json"
    dataset.write_text(json.dumps({"cases": cases, "corpus": []}), encoding="utf-8")
    return dataset


def _case(case_id: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "category": "policy",
        "query": "synthetic policy question",
        "tenant_id": "tenant-alpha",
        "user_id": "user-alpha",
        "roles": ["employee"],
        "department": "people",
        "permissions": ["document:read"],
        "metadata_filter": {"category": "policy"},
        "expected_documents": ["doc-policy"],
        "expected_chunks": ["chunk-policy-001"],
        "answerable": True,
        "attack_type": "none",
        "top_k": 5,
    }
