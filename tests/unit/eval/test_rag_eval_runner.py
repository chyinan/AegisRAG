from __future__ import annotations

import asyncio
import importlib
import socket
from pathlib import Path

import pytest

from tests.eval.rag.dto import ExpectedAnswerPolicy, RagEvalCaseResult
from tests.eval.rag.loader import load_rag_eval_dataset
from tests.eval.rag.runner import RagEvalFakeLLMProvider, run_rag_eval

DATASET = Path("tests/eval/datasets/rag_smoke.json")


def test_rag_eval_runner_executes_full_local_chain_without_external_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_socket(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("rag eval runner must not open network sockets")

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    dataset = load_rag_eval_dataset(DATASET)

    report = asyncio.run(run_rag_eval(dataset.cases, dataset.corpus))

    assert report.summary.case_count == 20
    assert report.summary.passed_count == 20
    assert report.summary.failed_count == 0
    assert report.summary.retrieval_hit_rate == 1.0
    assert report.summary.citation_coverage == 1.0
    assert report.summary.no_answer_correctness == 1.0
    assert report.summary.acl_isolation_passed is True
    assert report.summary.prompt_injection_passed is True
    assert all(result.generation.provider in {"fake", None} for result in report.cases)


def test_rag_eval_runner_does_not_add_missing_query_permission() -> None:
    dataset = load_rag_eval_dataset(DATASET)
    denied_case = dataset.cases[0].model_copy(update={"permissions": ("document:read",)})

    report = asyncio.run(run_rag_eval((denied_case,), dataset.corpus))

    result = report.cases[0]
    assert result.passed is False
    assert result.failure_stage == "permission"


def test_rag_eval_runner_checks_expected_answer_policy() -> None:
    dataset = load_rag_eval_dataset(DATASET)
    target_case = dataset.cases[0].model_copy(
        update={
            "expected_answer": ExpectedAnswerPolicy(
                must_include_terms=(),
                must_not_include_terms=("Local eval",),
            )
        }
    )

    report = asyncio.run(run_rag_eval((target_case,), dataset.corpus))

    result = report.cases[0]
    assert result.passed is False
    assert result.failure_stage == "generation"


def test_rag_eval_runner_marks_missing_expected_hit_as_retrieval_failure() -> None:
    dataset = load_rag_eval_dataset(DATASET)
    target_case = dataset.cases[0].model_copy(
        update={
            "expected_documents": ("doc-missing-safe",),
            "expected_chunks": (),
            "expected_citations": (),
        }
    )

    report = asyncio.run(run_rag_eval((target_case,), dataset.corpus))

    result = report.cases[0]
    assert result.passed is False
    assert result.failure_stage == "retrieval"


def test_rag_eval_runner_security_flags_do_not_pass_without_attack_cases() -> None:
    dataset = load_rag_eval_dataset(DATASET)
    normal_case = next(case for case in dataset.cases if case.attack_type == "none")

    report = asyncio.run(run_rag_eval((normal_case,), dataset.corpus))

    assert report.summary.acl_isolation_passed is False
    assert report.summary.prompt_injection_passed is False


def test_rag_eval_runner_marks_forged_or_missing_citation_as_citation_failure() -> None:
    dataset = load_rag_eval_dataset(DATASET)
    target_case = dataset.cases[0]

    report = asyncio.run(
        run_rag_eval(
            (target_case,),
            dataset.corpus,
            provider=RagEvalFakeLLMProvider(citation_miss_case_ids=(target_case.case_id,)),
        )
    )

    result = report.cases[0]
    assert result.passed is False
    assert result.failure_stage == "citation"
    assert result.forged_reference_count >= 1
    assert result.matched_citations == ()
    assert report.summary.citation_coverage == 0.0


def test_rag_eval_runner_marks_generation_failure_safely() -> None:
    dataset = load_rag_eval_dataset(DATASET)
    target_case = dataset.cases[0]

    report = asyncio.run(
        run_rag_eval(
            (target_case,),
            dataset.corpus,
            provider=RagEvalFakeLLMProvider(failure_case_ids=(target_case.case_id,)),
        )
    )

    result = report.cases[0]
    assert result.passed is False
    assert result.failure_stage == "generation"
    assert result.generation.error_code == "LLM_PROVIDER_FAILED"
    dumped = report.model_dump_json()
    assert target_case.query not in dumped
    assert "Synthetic HR policy" not in dumped


def test_rag_eval_runner_marks_no_answer_and_retrieval_miss_stages() -> None:
    dataset = load_rag_eval_dataset(DATASET)
    no_answer_case = next(case for case in dataset.cases if case.expected_no_answer)
    answerable_case = dataset.cases[0]

    no_answer_report = asyncio.run(run_rag_eval((no_answer_case,), dataset.corpus))
    retrieval_miss_report = asyncio.run(run_rag_eval((answerable_case,), ()))

    assert no_answer_report.cases[0].passed is True
    assert no_answer_report.summary.no_answer_correctness == 1.0
    assert retrieval_miss_report.cases[0].passed is False
    assert retrieval_miss_report.cases[0].failure_stage == "retrieval"


def test_rag_eval_case_result_forbids_extra_fields() -> None:
    with pytest.raises(ValueError):
        RagEvalCaseResult.model_validate(
            {
                "case_id": "case-1",
                "request_id": "req-1",
                "trace_id": "trace-1",
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "top_k": 5,
                "latency_ms": 1.0,
                "passed": True,
                "query": "must not be stored",
            }
        )


def test_rag_eval_runner_default_path_blocks_external_service_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_external(*args: object, **kwargs: object) -> object:
        raise AssertionError("rag eval runner must not access external services")

    monkeypatch.setattr(socket, "create_connection", fail_external)
    _patch_if_available(monkeypatch, "httpx", "Client", fail_external)
    _patch_if_available(monkeypatch, "httpx", "AsyncClient", fail_external)
    _patch_if_available(monkeypatch, "asyncpg", "connect", fail_external)
    _patch_if_available(monkeypatch, "redis", "Redis", fail_external)
    _patch_if_available(monkeypatch, "minio", "Minio", fail_external)
    _patch_if_available(monkeypatch, "docker", "from_env", fail_external)
    _patch_if_available(monkeypatch, "docker", "APIClient", fail_external)

    dataset = load_rag_eval_dataset(DATASET)

    report = asyncio.run(run_rag_eval(dataset.cases, dataset.corpus))

    assert report.summary.passed_count == 20


def _patch_if_available(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    attribute: str,
    replacement: object,
) -> None:
    if importlib.util.find_spec(module_name) is None:
        return
    module = importlib.import_module(module_name)
    if hasattr(module, attribute):
        monkeypatch.setattr(module, attribute, replacement)
