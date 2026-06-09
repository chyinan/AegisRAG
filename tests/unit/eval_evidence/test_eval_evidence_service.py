from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.eval import (
    EVAL_EVIDENCE_FORBIDDEN,
    EVAL_EVIDENCE_INVALID_REPORT_FILENAME,
    EVAL_EVIDENCE_PARSE_FAILED,
    EvalEvidenceError,
    EvalEvidenceService,
)

FORBIDDEN_FRAGMENTS = (
    '"query"',
    '"answer"',
    '"content"',
    '"prompt"',
    '"source_uri"',
    '"object_key"',
    '"sql"',
    '"vectors"',
    '"embeddings"',
    '"provider_raw_response"',
    '"raw_exception"',
    '"secret"',
    '"access_token"',
    '"api_key"',
    "must not escape",
    "file:///secret",
    "raw/tenant/doc",
    "C:\\Users\\secret",
)


@pytest.mark.asyncio
async def test_lists_safe_summaries_from_supported_reports(tmp_path: Path) -> None:
    _write_report(tmp_path / "rag-smoke-20260609T100000Z-safe.json", _quality_report())
    _write_report(tmp_path / "rag-ci-smoke-20260609T110000Z-safe.json", _gate_report())
    service = EvalEvidenceService(report_dir=tmp_path)

    result = await service.list_reports(context=_context())

    assert [item.report_type for item in result.items] == [
        "rag_ci_smoke_gate",
        "rag_quality_runner",
    ]
    gate = result.items[0]
    assert gate.report_filename == "rag-ci-smoke-20260609T110000Z-safe.json"
    assert gate.dataset_name == "rag_smoke.json"
    assert gate.decision == "failed"
    assert gate.failed_metric_names == ("citation_coverage",)
    assert gate.case_count == 2
    assert gate.failed_count == 1
    serialized = result.model_dump_json()
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in serialized


@pytest.mark.asyncio
async def test_resolves_failed_case_evidence_without_raw_payload(tmp_path: Path) -> None:
    _write_report(tmp_path / "rag-smoke-20260609T100000Z-safe.json", _quality_report())
    service = EvalEvidenceService(report_dir=tmp_path)

    result = await service.resolve_report(
        context=_context(),
        report_filename="rag-smoke-20260609T100000Z-safe.json",
    )

    assert result.summary.report_type == "rag_quality_runner"
    assert len(result.failed_cases) == 1
    failed = result.failed_cases[0]
    assert failed.case_id == "case-failed"
    assert failed.failure_stage == "citation"
    assert failed.matched_documents == ("doc-1",)
    assert failed.matched_chunks == ("chunk-1",)
    assert failed.generation.provider == "fake"
    assert failed.generation.token_usage == {
        "input_tokens": 9,
        "output_tokens": 3,
        "total_tokens": 12,
    }
    assert any("tests/eval" in command for command in result.next_steps)
    serialized = result.model_dump_json()
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in serialized


@pytest.mark.asyncio
async def test_rejects_path_traversal_without_existence_leak(tmp_path: Path) -> None:
    service = EvalEvidenceService(report_dir=tmp_path)

    with pytest.raises(EvalEvidenceError) as exc_info:
        await service.resolve_report(context=_context(), report_filename="../secret.json")

    assert exc_info.value.code == EVAL_EVIDENCE_INVALID_REPORT_FILENAME
    assert "secret" not in str(exc_info.value.details)
    assert "report_dir" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_permission_denial_is_uniform_and_does_not_read_storage(tmp_path: Path) -> None:
    _write_report(tmp_path / "rag-smoke-20260609T100000Z-safe.json", _quality_report())
    service = EvalEvidenceService(report_dir=tmp_path)

    with pytest.raises(EvalEvidenceError) as exc_info:
        await service.resolve_report(
            context=_context(permissions=("document:read",)),
            report_filename="rag-smoke-20260609T100000Z-safe.json",
        )

    assert exc_info.value.code == EVAL_EVIDENCE_FORBIDDEN
    assert "rag-smoke" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_malformed_report_raises_safe_parse_error(tmp_path: Path) -> None:
    (tmp_path / "rag-smoke-20260609T100000Z-safe.json").write_text("{bad-json", encoding="utf-8")
    service = EvalEvidenceService(report_dir=tmp_path)

    with pytest.raises(EvalEvidenceError) as exc_info:
        await service.resolve_report(
            context=_context(),
            report_filename="rag-smoke-20260609T100000Z-safe.json",
        )

    assert exc_info.value.code == EVAL_EVIDENCE_PARSE_FAILED
    assert "bad-json" not in str(exc_info.value.details)
    assert str(tmp_path) not in str(exc_info.value.details)


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _quality_report() -> dict[str, object]:
    return {
        "generated_at": "2026-06-09T10:00:00+00:00",
        "report_type": "rag_quality_runner",
        "summary": {
            "case_count": 2,
            "passed_count": 1,
            "failed_count": 1,
            "retrieval_hit_rate": 0.5,
            "citation_coverage": 0.5,
            "required_citation_count": 2,
            "matched_required_citation_count": 1,
            "no_answer_correctness": 1.0,
            "no_answer_case_count": 0,
            "acl_isolation_passed": True,
            "prompt_injection_passed": True,
            "average_latency_ms": 12.5,
        },
        "cases": [
            {
                "case_id": "case-passed",
                "request_id": "req-pass",
                "trace_id": "trace-pass",
                "tenant_id": "tenant-raw",
                "user_id": "user-raw",
                "top_k": 5,
                "latency_ms": 5.0,
                "passed": True,
                "failure_stage": None,
            },
            {
                "case_id": "case-failed",
                "request_id": "req-fail",
                "trace_id": "trace-fail",
                "tenant_id": "tenant-raw",
                "user_id": "user-raw",
                "top_k": 5,
                "latency_ms": 20.0,
                "passed": False,
                "failure_stage": "citation",
                "matched_documents": ["doc-1"],
                "matched_chunks": ["chunk-1"],
                "matched_citations": ["doc-1:v1:chunk-1"],
                "retrieval_result_count": 1,
                "context_item_count": 1,
                "citation_count": 0,
                "unsupported_count": 0,
                "forged_reference_count": 1,
                "prompt_risk_count": 0,
                "generation": {
                    "provider": "fake",
                    "model": "fake-llm",
                    "version": "fake-v1",
                    "finish_reason": "stop",
                    "token_usage": {
                        "input_tokens": 9,
                        "output_tokens": 3,
                        "total_tokens": 12,
                    },
                    "provider_raw_response": "must not escape",
                },
                "query": "must not escape",
                "answer": "must not escape",
                "prompt": "must not escape",
                "source_uri": "file:///secret",
                "object_key": "raw/tenant/doc",
                "raw_exception": "C:\\Users\\secret\\trace.txt",
            },
        ],
    }


def _gate_report() -> dict[str, object]:
    return {
        "generated_at": "2026-06-09T11:00:00+00:00",
        "report_type": "rag_ci_smoke_gate",
        "commit_sha": "abc123",
        "branch": "main",
        "dataset": {"name": "rag_smoke.json", "path": "tests/eval/datasets/rag_smoke.json"},
        "runner_summary": _quality_report()["summary"],
        "decision": {
            "passed": False,
            "failed_metric_names": ["citation_coverage"],
            "failed_case_ids": ["case-failed"],
            "failure_stages": ["citation"],
            "metrics": [
                {
                    "metric": "citation_coverage",
                    "threshold_name": "min_citation_coverage",
                    "passed": False,
                    "expected": 0.9,
                    "actual": 0.5,
                }
            ],
        },
        "failed_case_ids": ["case-failed"],
        "failure_stages": ["citation"],
    }


def _context(permissions: tuple[str, ...] = ("eval:read",)) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-eval",
        trace_id="trace-eval",
        auth=AuthContext(
            user_id="platform-user",
            tenant_id="tenant-1",
            roles=("platform_engineer",),
            permissions=permissions,
        ),
    )
