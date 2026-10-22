from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from evaluation import evaluate as cli
from packages.eval.ragas_evaluator import EvalCase, EvalReport


def _query_response(**overrides: object) -> dict[str, object]:
    return {
        "request_id": "query-1",
        "answer": "The authorized answer.",
        "citations": [{"document_id": "doc-1", "version_id": "version-1", "chunk_id": "chunk-1"}],
        "metadata": {"context": {"item_count": 1, "citation_source_count": 1}},
        **overrides,
    }


async def test_query_uses_current_envelope_and_blocks_unavailable_context() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/query"
        assert json.loads(request.content) == {"query": "Question?", "top_k": 10}
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.extensions["timeout"]["read"] == 3.0
        return httpx.Response(200, json={"data": _query_response(), "error": None})

    with pytest.raises(cli.RagApiEvaluationError, match="Full packed contexts are unavailable"):
        await cli.query_rag(
            "https://aegis.test/api/",
            "Question?",
            "test-token",
            timeout=3.0,
            transport=httpx.MockTransport(respond),
        )

    # Do not silently score empty contexts, repeat retrieval, or fetch bounded
    # excerpts as a substitute for the context actually supplied to generation.
    assert len(requests) == 1


@pytest.mark.parametrize("answer", [None, "", "   ", 7, []])
async def test_query_rejects_missing_or_invalid_answer(answer: object) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": _query_response(answer=answer)})
    )
    with pytest.raises(cli.RagApiEvaluationError, match="nonblank answer"):
        await cli.query_rag("https://aegis.test", "Question?", transport=transport)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"answer": "legacy response"},
        {"data": []},
        {"data": None},
        {"data": _query_response(), "error": {"message": "sensitive-error"}},
    ],
)
async def test_query_rejects_invalid_or_failed_envelope(payload: object) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(cli.RagApiEvaluationError) as error:
        await cli.query_rag("https://aegis.test", "Question?", transport=transport)
    assert "sensitive-error" not in str(error.value)


async def test_query_rejects_invalid_json_without_response_content() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="invalid sensitive-body")
    )
    with pytest.raises(cli.RagApiEvaluationError, match="invalid JSON") as error:
        await cli.query_rag("https://aegis.test", "Question?", transport=transport)
    assert "sensitive-body" not in str(error.value)


@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_query_propagates_http_failure_without_auth_or_response_text(status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="sensitive-body"))
    with pytest.raises(cli.RagApiEvaluationError, match=f"HTTP {status}") as error:
        await cli.query_rag(
            "https://aegis.test", "Question?", "secret-api-token", transport=transport
        )
    assert "sensitive-body" not in str(error.value)
    assert "secret-api-token" not in str(error.value)


async def test_query_sanitizes_timeout() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive-transport-error", request=request)

    with pytest.raises(cli.RagApiEvaluationError, match="transport error") as error:
        await cli.query_rag(
            "https://aegis.test", "Question?", transport=httpx.MockTransport(respond)
        )
    assert "sensitive-transport-error" not in str(error.value)


def _dataset(tmp_path: Path) -> Path:
    path = tmp_path / "dataset.json"
    path.write_text(
        json.dumps(
            {
                "dataset_name": "cli-test",
                "dataset_version": "v2",
                "cases": [
                    {
                        "case_id": "case-1",
                        "question": "Question?",
                        "reference_answer": "Reference.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _responses_file(tmp_path: Path, payload: object | None = None) -> Path:
    path = tmp_path / "responses.json"
    path.write_text(
        json.dumps(
            {"case-1": {"answer": "Answer.", "retrieved_contexts": ["Exact captured context."]}}
            if payload is None
            else payload
        ),
        encoding="utf-8",
    )
    return path


def _report(*, failed: bool = False) -> EvalReport:
    return EvalReport(
        run_id="test-run",
        generated_at="2026-10-14T00:00:00+00:00",
        dataset_name="cli-test",
        dataset_version="v2",
        case_count=1,
        passed_count=0 if failed else 1,
        failed_count=1 if failed else 0,
        results=(),
        aggregate_scores={"faithfulness": 0.0 if failed else 1.0},
        average_latency_ms=1.0,
        failure_stages=(),
    )


async def test_main_awaits_evaluation_writes_reports_and_forwards_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _report()
    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = AsyncMock(return_value=report)
    fake_evaluator.to_evidence_report.return_value = {"run_id": "test-run"}
    fake_evaluator.to_markdown.return_value = "# Evaluation\n"
    constructor = MagicMock(return_value=fake_evaluator)
    monkeypatch.setattr(cli, "RagasEvaluator", constructor)
    query = AsyncMock(return_value=("Answer.", ["Context."]))
    monkeypatch.setattr(cli, "query_rag", query)
    output = tmp_path / "reports"

    await cli.main(
        [
            "--dataset",
            str(_dataset(tmp_path)),
            "--output",
            str(output),
            "--api-url",
            "https://aegis.test",
            "--api-key",
            "rag-token",
            "--api-timeout",
            "7",
            "--llm-model",
            "local-judge",
            "--llm-base-url",
            "https://judge.test/v1",
            "--llm-api-key",
            "judge-token",
            "--embedding-model",
            "local-embedding",
            "--embedding-base-url",
            "https://embedding.test/v1",
            "--embedding-api-key",
            "embedding-token",
            "--judge-timeout",
            "9",
            "--metrics",
            " faithfulness , answer_relevancy ",
            "--responses",
            str(_responses_file(tmp_path)),
        ]
    )

    fake_evaluator.evaluate.assert_awaited_once()
    kwargs = fake_evaluator.evaluate.await_args.kwargs
    assert kwargs["dataset_name"] == "cli-test"
    assert kwargs["dataset_version"] == "v2"
    assert kwargs["cases"][0].question == "Question?"
    assert await kwargs["run_fn"]("Question?") == ("Answer.", ["Exact captured context."])
    query.assert_not_called()
    assert constructor.call_args.kwargs == {
        "llm_model": "local-judge",
        "llm_base_url": "https://judge.test/v1",
        "llm_api_key": "judge-token",
        "embedding_model": "local-embedding",
        "embedding_base_url": "https://embedding.test/v1",
        "embedding_api_key": "embedding-token",
        "timeout_seconds": 9.0,
        "generation_timeout_seconds": 7.0,
        "pass_threshold": 0.7,
        "metrics": ("faithfulness", "answer_relevancy"),
    }
    assert json.loads((output / "eval_test-run.json").read_text()) == {"run_id": "test-run"}
    assert (output / "eval_test-run.md").read_text() == "# Evaluation\n"
    assert "Passed: 1/1" in capsys.readouterr().out


async def test_main_reads_embedding_and_timeout_environment_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = AsyncMock(return_value=_report())
    fake_evaluator.to_evidence_report.return_value = {}
    fake_evaluator.to_markdown.return_value = "report"
    constructor = MagicMock(return_value=fake_evaluator)
    monkeypatch.setattr(cli, "RagasEvaluator", constructor)
    monkeypatch.setenv("RAGAS_EMBEDDING_BASE_URL", "https://embedding.test/v1")
    monkeypatch.setenv("RAGAS_EMBEDDING_API_KEY", "environment-token")
    monkeypatch.setenv("RAGAS_TIMEOUT_SECONDS", "11")
    await cli.main(
        [
            "--dataset",
            str(_dataset(tmp_path)),
            "--output",
            str(tmp_path / "out"),
            "--metrics",
            "answer_relevancy",
        ]
    )
    assert constructor.call_args.kwargs["embedding_base_url"] == "https://embedding.test/v1"
    assert constructor.call_args.kwargs["embedding_api_key"] == "environment-token"
    assert constructor.call_args.kwargs["timeout_seconds"] == 11.0
    assert constructor.call_args.kwargs["generation_timeout_seconds"] == 60.0


async def test_main_failed_evaluation_saves_reports_before_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = AsyncMock(return_value=_report(failed=True))
    fake_evaluator.to_evidence_report.return_value = {"passed_count": 0}
    fake_evaluator.to_markdown.return_value = "failed"
    monkeypatch.setattr(cli, "RagasEvaluator", MagicMock(return_value=fake_evaluator))
    output = tmp_path / "reports"
    with pytest.raises(SystemExit) as error:
        await cli.main(
            [
                "--dataset",
                str(_dataset(tmp_path)),
                "--output",
                str(output),
                "--metrics",
                "answer_relevancy",
            ]
        )
    assert error.value.code == 1
    assert (output / "eval_test-run.json").is_file()
    assert (output / "eval_test-run.md").is_file()


@pytest.mark.parametrize("option", ["--api-timeout", "--judge-timeout"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
async def test_main_rejects_invalid_timeouts_before_io(option: str, value: str) -> None:
    with pytest.raises(SystemExit) as error:
        await cli.main(["--dataset", "missing.json", option, value])
    assert error.value.code == 2


async def test_query_answer_only_mode_uses_data_answer_without_context_lookup() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": _query_response()})

    result = await cli.query_rag(
        "https://aegis.test",
        "Question?",
        allow_missing_contexts=True,
        transport=httpx.MockTransport(respond),
    )
    assert result == ("The authorized answer.", [])
    assert len(requests) == 1


async def test_main_blocks_context_metrics_without_capture_before_any_io(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    constructor = MagicMock()
    dataset = MagicMock()
    monkeypatch.setattr(cli, "RagasEvaluator", constructor)
    monkeypatch.setattr(cli, "load_dataset", dataset)
    with pytest.raises(SystemExit) as error:
        await cli.main(["--dataset", "missing.json"])
    assert error.value.code == 2
    assert "--responses" in capsys.readouterr().err
    constructor.assert_not_called()
    dataset.assert_not_called()


async def test_main_live_answer_relevancy_forwards_auth_timeout_and_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = AsyncMock(return_value=_report())
    fake_evaluator.to_evidence_report.return_value = {}
    fake_evaluator.to_markdown.return_value = "report"
    monkeypatch.setattr(cli, "RagasEvaluator", MagicMock(return_value=fake_evaluator))
    query = AsyncMock(return_value=("Answer.", []))
    monkeypatch.setattr(cli, "query_rag", query)
    monkeypatch.setenv("RAGAS_API_KEY", "environment-api-token")
    await cli.main(
        [
            "--dataset",
            str(_dataset(tmp_path)),
            "--output",
            str(tmp_path / "out"),
            "--metrics",
            "answer_relevancy",
            "--api-url",
            "https://aegis.test",
            "--api-timeout",
            "7",
        ]
    )
    await fake_evaluator.evaluate.await_args.kwargs["run_fn"]("Question?")
    query.assert_awaited_once_with(
        "https://aegis.test",
        "Question?",
        "environment-api-token",
        timeout=7.0,
        allow_missing_contexts=True,
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"wrong-id": {"answer": "Answer", "retrieved_contexts": ["Context"]}},
        {"case-1": "not an object"},
        {"case-1": {"answer": "", "retrieved_contexts": ["Context"]}},
        {"case-1": {"answer": 1, "retrieved_contexts": ["Context"]}},
        {"case-1": {"answer": "Answer", "reference_contexts": ["Not actual context"]}},
        {"case-1": {"answer": "Answer", "retrieved_contexts": "string"}},
        {"case-1": {"answer": "Answer", "retrieved_contexts": [None]}},
        {"case-1": {"answer": "Answer", "retrieved_contexts": ["   "]}},
    ],
)
def test_load_responses_rejects_missing_misaligned_or_invalid_captures(
    tmp_path: Path, payload: object
) -> None:
    with pytest.raises(ValueError):
        cli.load_responses(
            str(_responses_file(tmp_path, payload)),
            [EvalCase(case_id="case-1", question="Question?")],
        )


def test_load_responses_preserves_explicit_empty_retrieval(
    tmp_path: Path,
) -> None:
    response = {"case-1": {"answer": "Answer", "retrieved_contexts": []}}
    assert cli.load_responses(
        str(_responses_file(tmp_path, response)),
        [EvalCase(case_id="case-1", question="Question?")],
    ) == {"case-1": ("Answer", [])}


def test_load_responses_rejects_duplicate_dataset_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unique"):
        cli.load_responses(
            str(_responses_file(tmp_path)),
            [EvalCase(case_id="case-1", question="Question?")] * 2,
        )


async def test_main_captured_responses_align_by_case_id_even_with_duplicate_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = [
        EvalCase(case_id="one", question="Same?", reference_answer="Reference"),
        EvalCase(case_id="two", question="Same?", reference_answer="Reference"),
    ]
    monkeypatch.setattr(cli, "load_dataset", lambda path: (cases, "dataset", "v1"))
    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = AsyncMock(return_value=_report())
    fake_evaluator.to_evidence_report.return_value = {}
    fake_evaluator.to_markdown.return_value = "report"
    monkeypatch.setattr(cli, "RagasEvaluator", MagicMock(return_value=fake_evaluator))
    response_path = _responses_file(
        tmp_path,
        {
            "two": {"answer": "Second answer", "retrieved_contexts": ["Second context"]},
            "one": {"answer": "First answer", "retrieved_contexts": ["First context"]},
        },
    )
    await cli.main(
        [
            "--dataset",
            "dataset.json",
            "--output",
            str(tmp_path / "out"),
            "--responses",
            str(response_path),
        ]
    )
    runner = fake_evaluator.evaluate.await_args.kwargs["run_fn"]
    assert await runner("Same?") == ("First answer", ["First context"])
    assert await runner("Same?") == ("Second answer", ["Second context"])
    with pytest.raises(ValueError, match="order"):
        await runner("Same?")


@pytest.mark.parametrize(
    "raw",
    [
        (
            '{"case-1": {"answer": "one", "retrieved_contexts": ["context"]},'
            ' "case-1": {"answer": "two", "retrieved_contexts": ["context"]}}'
        ),
        '{"case-1": {"answer": "one", "answer": "two", "retrieved_contexts": ["context"]}}',
    ],
)
def test_load_responses_rejects_duplicate_json_keys(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(raw)
    with pytest.raises(ValueError, match="duplicate object keys"):
        cli.load_responses(
            str(path),
            [EvalCase(case_id="case-1", question="Question?")],
        )


async def test_main_rejects_invalid_capture_before_evaluator_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructor = MagicMock()
    monkeypatch.setattr(cli, "RagasEvaluator", constructor)
    with pytest.raises(SystemExit) as error:
        await cli.main(
            [
                "--dataset",
                str(_dataset(tmp_path)),
                "--responses",
                str(_responses_file(tmp_path, {})),
                "--output",
                str(tmp_path / "out"),
            ]
        )
    assert error.value.code == 2
    constructor.assert_not_called()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("reference", [None, "", "  ", 1])
async def test_main_preflights_required_references_before_capture_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: object
) -> None:
    cases = [
        EvalCase(case_id="one", question="Same?", reference_answer=reference),
        EvalCase(case_id="two", question="Same?", reference_answer="Second reference"),
    ]
    monkeypatch.setattr(cli, "load_dataset", lambda path: (cases, "dataset", "v1"))
    constructor = MagicMock()
    monkeypatch.setattr(cli, "RagasEvaluator", constructor)
    with pytest.raises(SystemExit) as error:
        await cli.main(
            [
                "--dataset",
                "dataset.json",
                "--responses",
                "not-read.json",
                "--metrics",
                "context_precision",
            ]
        )
    assert error.value.code == 2
    constructor.assert_not_called()


async def test_main_rejects_empty_dataset_before_evaluator_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "load_dataset", lambda path: ([], "empty", "v1"))
    constructor = MagicMock()
    monkeypatch.setattr(cli, "RagasEvaluator", constructor)
    with pytest.raises(SystemExit) as error:
        await cli.main(["--dataset", "dataset.json", "--metrics", "answer_relevancy"])
    assert error.value.code == 2
    constructor.assert_not_called()


@pytest.mark.parametrize("empty_context", [False, True])
async def test_main_captured_run_uses_real_ragas_metrics_and_writes_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    empty_context: bool,
) -> None:
    monkeypatch.setenv("RAGAS_DO_NOT_TRACK", "true")
    pytest.importorskip("ragas")
    pytest.importorskip("langchain_openai")
    from ragas._analytics import do_not_track

    from tests.unit.eval.test_ragas_evaluator import ANSWER, CONTEXT, QUESTION, Judge

    attempts: list[str] = []

    def forbid_network(*args: object, **kwargs: object) -> None:
        attempts.append("network")
        raise AssertionError("Real network must not be used in CLI evaluation tests")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    monkeypatch.setattr("requests.sessions.Session.request", forbid_network)
    do_not_track.cache_clear()
    judge = Judge()
    real_evaluator = cli.RagasEvaluator

    def with_offline_transport(**kwargs: Any) -> cli.RagasEvaluator:
        return real_evaluator(http_transport=httpx.MockTransport(judge), **kwargs)

    # Only inject an offline SDK transport. Evaluate, metric implementations,
    # response adapters, aggregation and report serialization remain real.
    monkeypatch.setattr(cli, "RagasEvaluator", with_offline_transport)
    dataset_path = tmp_path / "real-dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "dataset_name": "real-cli",
                "dataset_version": "v1",
                "cases": [{"case_id": "case-1", "question": QUESTION, "reference_answer": ANSWER}],
            }
        )
    )
    responses_path = _responses_file(
        tmp_path,
        {
            "case-1": {"answer": ANSWER, "retrieved_contexts": [] if empty_context else [CONTEXT]},
        },
    )
    output = tmp_path / "real-reports"
    args = [
        "--dataset",
        str(dataset_path),
        "--responses",
        str(responses_path),
        "--output",
        str(output),
        "--llm-model",
        "fake-chat",
        "--llm-base-url",
        "https://judge.invalid/v1",
        "--llm-api-key",
        "fake-key",
        "--embedding-model",
        "fake-embedding",
        "--embedding-base-url",
        "https://embedding.invalid/v1",
        "--embedding-api-key",
        "fake-embedding-key",
        "--api-timeout",
        "7",
        "--judge-timeout",
        "9",
    ]
    try:
        if empty_context:
            # An explicit empty retrieval is valid negative evaluation evidence.
            # Real upstream ContextPrecision scores it zero without judge I/O.
            with pytest.raises(SystemExit) as error:
                await cli.main([*args, "--metrics", "context_precision"])
            assert error.value.code == 1
        else:
            await cli.main(args)
    finally:
        do_not_track.cache_clear()

    assert not attempts
    report_files = list(output.glob("eval_*.json"))
    assert len(report_files) == 1
    evidence = json.loads(report_files[0].read_text())
    assert evidence["dataset"] == {"name": "real-cli", "version": "v1"}
    assert evidence["summary"]["passed_count"] == (0 if empty_context else 1)
    assert evidence["summary"]["failed_count"] == (1 if empty_context else 0)
    expected_scores = (
        {"context_precision": 0.0}
        if empty_context
        else {
            "context_precision": 1.0,
            "context_recall": 1.0,
            "faithfulness": 1.0,
            "answer_relevancy": 1.0,
        }
    )
    assert evidence["aggregate_scores"] == pytest.approx(expected_scores)
    assert evidence["cases"][0]["scores"] == pytest.approx(expected_scores)
    assert evidence["cases"][0]["passed"] is not empty_context
    assert evidence["cases"][0]["case_id"] == "case-1"
    assert evidence["cases"][0]["answer"] == ANSWER
    assert evidence["cases"][0]["metric_errors"] == {}
    assert len(list(output.glob("eval_*.md"))) == 1
    assert f"Passed: {0 if empty_context else 1}/1" in capsys.readouterr().out
    if empty_context:
        assert not judge.requests
    else:
        assert {request.url.host for request in judge.requests} == {
            "judge.invalid",
            "embedding.invalid",
        }
        assert sum(request.url.path.endswith("/embeddings") for request in judge.requests) == 2
