from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.rag.loader import RagEvalDatasetError, load_rag_eval_dataset

DATASET = Path("tests/eval/datasets/rag_smoke.json")


def test_loads_real_rag_smoke_dataset_with_required_coverage() -> None:
    dataset = load_rag_eval_dataset(DATASET)

    assert dataset.dataset_version == "rag-smoke-v1"
    assert len(dataset.cases) == 20
    assert len({case.case_id for case in dataset.cases}) == 20
    assert {case.category for case in dataset.cases} == {
        "faq",
        "policy",
        "product_manual",
        "technical_doc",
    }
    assert all(case.tenant_id and case.user_id for case in dataset.cases)
    assert sum(1 for case in dataset.cases if case.attack_type == "acl_isolation") >= 2
    assert sum(1 for case in dataset.cases if case.attack_type == "prompt_injection") >= 2
    assert sum(1 for case in dataset.cases if case.expected_no_answer) >= 2
    assert sum(1 for case in dataset.cases if case.expected_citations) >= 3
    assert len(dataset.corpus) >= 20


def test_rejects_duplicate_case_id_with_safe_error(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path, cases=[_case("dup-case"), _case("dup-case")])

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "duplicate_case_id" in message
    assert "dup-case" in message
    assert str(tmp_path) not in message


def test_rejects_invalid_case_without_leaking_query_answer_or_secret(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[
            {
                **_case("bad-case"),
                "query": "full sensitive query with sk-test-token",
                "metadata_filter": {"$where": "tenant_id = '*'; secret"},
                "expected_answer": {"must_include_terms": ["confidential answer text"]},
            }
        ],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "invalid_case" in message
    assert "metadata_filter" in message
    assert "full sensitive query" not in message
    assert "confidential answer text" not in message
    assert "sk-test-token" not in message
    assert str(tmp_path) not in message


@pytest.mark.parametrize(
    ("case_update", "corpus_update", "expected_field"),
    [
        ({"top_k": "5"}, None, "top_k"),
        ({"roles": "employee"}, None, "roles"),
        (
            {
                "expected_citations": [
                    {
                        "document_id": "doc-policy",
                        "version_id": "version-1",
                        "chunk_id": "chunk-policy-001",
                        "page_start": "1",
                        "page_end": 1,
                        "required": True,
                    }
                ]
            },
            None,
            "expected_citations.page_start",
        ),
        (None, {"token_count": "9"}, "token_count"),
        (None, {"score": "0.95"}, "score"),
    ],
)
def test_rejects_wrong_json_types(
    tmp_path: Path,
    case_update: dict[str, object] | None,
    corpus_update: dict[str, object] | None,
    expected_field: str,
) -> None:
    case = _case("bad-type")
    corpus = _corpus_record("bad-type")
    if case_update:
        case.update(case_update)
    if corpus_update:
        corpus.update(corpus_update)
    dataset = _write_dataset(tmp_path, cases=[case], corpus=[corpus])

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    assert expected_field in str(exc_info.value)


def test_rejects_extra_fields_without_leaking_secret_like_field_names(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[{**_case("extra-field"), "api_key": "sk-hidden-value"}],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "invalid_case" in message
    assert "unsafe_field" in message
    assert "api_key" not in message
    assert "sk-hidden-value" not in message


def test_rejects_secret_like_fixture_id_without_echoing_it(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path, cases=[_case("sk-prod-token")])

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "invalid_case" in message
    assert "sk-prod-token" not in message


def test_rejects_invalid_expected_citation_and_no_answer_policy(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[
            {
                **_case("bad-citation"),
                "answerable": False,
                "expected_no_answer": True,
                "expected_citations": [
                    {
                        "document_id": "doc-policy",
                        "version_id": "version-1",
                        "chunk_id": "chunk-policy-001",
                        "required": True,
                    }
                ],
            }
        ],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "invalid_case" in message
    assert "model" in message or "expected_citations" in message


def test_rejects_illegal_attack_type(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[{**_case("bad-attack"), "attack_type": "jailbreak"}],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    assert "attack_type" in str(exc_info.value)


def test_rejects_unknown_relevant_case_id_without_leaking_content(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[_case("known-case")],
        corpus=[
            {
                **_corpus_record("known-case"),
                "content": "Synthetic chunk that should not be echoed.",
                "relevant_case_ids": ["missing-case"],
            }
        ],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    message = str(exc_info.value)
    assert "unknown_relevant_case_id" in message
    assert "missing-case" not in message
    assert "Synthetic chunk" not in message


def test_rejects_expected_ids_missing_from_authorized_corpus(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[
            {
                **_case("missing-expected"),
                "expected_documents": ["doc-missing"],
                "expected_chunks": ["chunk-missing"],
                "expected_citations": [
                    {
                        "document_id": "doc-missing",
                        "version_id": "version-1",
                        "chunk_id": "chunk-missing",
                        "page_start": 1,
                        "page_end": 1,
                        "required": True,
                    }
                ],
            }
        ],
        corpus=[_corpus_record("missing-expected")],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    assert "expected_document_missing_from_corpus" in str(exc_info.value)


def test_rejects_cross_tenant_relevant_corpus(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[_case("cross-tenant")],
        corpus=[{**_corpus_record("cross-tenant"), "tenant_id": "tenant-beta"}],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    assert "cross_tenant_relevant_corpus" in str(exc_info.value)


def test_rejects_no_answer_case_with_relevant_corpus(tmp_path: Path) -> None:
    case = {
        **_case("no-answer-with-corpus"),
        "expected_documents": [],
        "expected_chunks": [],
        "expected_citations": [],
        "answerable": False,
        "expected_no_answer": True,
    }
    dataset = _write_dataset(
        tmp_path,
        cases=[case],
        corpus=[
            {
                **_corpus_record("no-answer-with-corpus"),
                "relevant_case_ids": [case["case_id"]],
            }
        ],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    assert "no_answer_case_has_relevant_corpus" in str(exc_info.value)


def test_rejects_acl_isolation_without_restricted_competitor(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[{**_case("acl-without-competitor"), "attack_type": "acl_isolation"}],
        corpus=[_corpus_record("acl-without-competitor")],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    assert "invalid_acl_isolation_fixture" in str(exc_info.value)


def test_rejects_malformed_acl_payload(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[_case("bad-acl")],
        corpus=[
            {
                **_corpus_record("bad-acl"),
                "acl": {"visibility": "private", "allowed_roles": "admin"},
            }
        ],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    assert "acl.allowed_roles" in str(exc_info.value)


def test_rejects_unsafe_corpus_source_uri(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path,
        cases=[_case("known-case")],
        corpus=[
            {
                **_corpus_record("known-case"),
                "source_uri": "C:\\Users\\person\\secret.txt",
            }
        ],
    )

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset, min_case_count=1)

    assert "source_uri" in str(exc_info.value)


def test_rejects_non_synthetic_source_and_embedded_local_path(tmp_path: Path) -> None:
    source_dataset = _write_dataset(
        tmp_path,
        cases=[_case("bad-source")],
        corpus=[{**_corpus_record("bad-source"), "source": "s3"}],
    )
    with pytest.raises(RagEvalDatasetError) as source_exc:
        load_rag_eval_dataset(source_dataset, min_case_count=1)
    assert "source" in str(source_exc.value)

    path_dataset = _write_dataset(
        tmp_path,
        cases=[_case("bad-path")],
        corpus=[
            {
                **_corpus_record("bad-path"),
                "source_uri": "synthetic://rag-eval/C:/Users/person/secret.txt",
            }
        ],
    )
    with pytest.raises(RagEvalDatasetError) as path_exc:
        load_rag_eval_dataset(path_dataset, min_case_count=1)
    assert "source_uri" in str(path_exc.value)


def test_rejects_case_count_below_minimum(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path, cases=[_case("only-case")])

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset)

    assert "case_count_below_minimum" in str(exc_info.value)


def test_rejects_invalid_json_with_safe_error(tmp_path: Path) -> None:
    dataset = tmp_path / "broken.json"
    dataset.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RagEvalDatasetError) as exc_info:
        load_rag_eval_dataset(dataset)

    message = str(exc_info.value)
    assert "invalid_json" in message
    assert str(tmp_path) not in message


def _write_dataset(
    tmp_path: Path,
    *,
    cases: list[dict[str, object]],
    corpus: list[dict[str, object]] | None = None,
) -> Path:
    dataset = tmp_path / "rag_smoke.json"
    dataset.write_text(
        json.dumps(
            {
                "dataset_version": "rag-smoke-test",
                "cases": cases,
                "corpus": corpus if corpus is not None else [_corpus_record(cases[0]["case_id"])],
            }
        ),
        encoding="utf-8",
    )
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
        "expected_citations": [
            {
                "document_id": "doc-policy",
                "version_id": "version-1",
                "chunk_id": "chunk-policy-001",
                "page_start": 1,
                "page_end": 1,
                "required": True,
            }
        ],
        "answerable": True,
        "expected_no_answer": False,
        "expected_answer": {
            "must_include_terms": ["policy"],
            "must_not_include_terms": ["forged"],
        },
        "attack_type": "none",
        "top_k": 5,
    }


def _corpus_record(case_id: object) -> dict[str, object]:
    return {
        "document_id": "doc-policy",
        "version_id": "version-1",
        "chunk_id": "chunk-policy-001",
        "tenant_id": "tenant-alpha",
        "content": "Synthetic policy fixture content for local RAG evaluation.",
        "token_count": 9,
        "source": "synthetic",
        "source_uri": "synthetic://rag-eval/policy/doc-policy",
        "source_type": "markdown",
        "page_start": 1,
        "page_end": 1,
        "title_path": ["Policy"],
        "score": 0.95,
        "retrieval_method": "hybrid",
        "acl": {"visibility": "tenant"},
        "metadata": {"category": "policy"},
        "relevant_case_ids": [case_id],
    }
