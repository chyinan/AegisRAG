from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

from tests.eval.rag.dto import RagEvalCaseResult
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
