"""Actual locked Ragas + LangChain + OpenAI SDK, with synthetic offline transport."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

pytest.importorskip("ragas")
pytest.importorskip("langchain_openai")

from packages.eval.dto import EvalEvidenceFailureStage  # noqa: E402
from packages.eval.ragas_evaluator import DEFAULT_METRICS, EvalCase, RagasEvaluator  # noqa: E402

QUESTION = "What is the synthetic capital?"
ANSWER = "The synthetic capital is Example."
CONTEXT = "Example is the synthetic capital."


@pytest.fixture(autouse=True)
def offline_private_ragas(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from ragas._analytics import do_not_track

    monkeypatch.setenv("RAGAS_DO_NOT_TRACK", "true")
    do_not_track.cache_clear()
    attempts: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        attempts.append("network")
        raise AssertionError("Real network must never be used in Ragas tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    yield
    do_not_track.cache_clear()
    assert attempts == []


class Judge:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail = False
        self.malformed = False
        self.nonfinite = False
        self.delay_path: str | None = None
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.delay_path and request.url.path.endswith(self.delay_path):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
        if self.fail:
            return httpx.Response(500, json={"error": {"message": "private-provider-error"}})
        payload = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            inputs = payload["input"]
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": "fake-embedding",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [1.0, 0.0]}
                        for i, _ in enumerate(inputs)
                    ],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        assert request.url.path.endswith("/chat/completions")
        prompt = payload["messages"][0]["content"]
        verdict = 0 if "irrelevant-marker" in prompt else 1
        if prompt.startswith("Given question, answer and context"):
            output = {"reason": "synthetic", "verdict": verdict}
        elif prompt.startswith("Given a context, and an answer"):
            output = {
                "classifications": [
                    {"statement": ANSWER, "reason": "synthetic", "attributed": verdict}
                ]
            }
        elif prompt.startswith("Given a question and an answer"):
            output = {"statements": [] if self.nonfinite else [ANSWER]}
        elif prompt.startswith("Your task is to judge the faithfulness"):
            output = {
                "statements": [{"statement": ANSWER, "reason": "synthetic", "verdict": verdict}]
            }
        elif prompt.startswith("Generate a question for the given answer"):
            output = {"question": QUESTION, "noncommittal": 1 - verdict}
        else:
            # Malformed-output repair uses the real Ragas parser/prompt too.
            assert self.malformed, prompt[:80]
            output = {"text": "private-malformed-response"}
        content = "private-malformed-response" if self.malformed else json.dumps(output)
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "fake-chat",
                "choices": [
                    {
                        "index": i,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                    for i in range(payload.get("n", 1))
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


def evaluator(judge: Judge, **kwargs: Any) -> RagasEvaluator:
    return RagasEvaluator(
        llm_model="fake-chat",
        llm_base_url="https://judge.invalid/v1",
        llm_api_key="fake-key",
        embedding_model="fake-embedding",
        embedding_base_url="https://embedding.invalid/v1",
        embedding_api_key="fake-embedding-key",
        http_transport=httpx.MockTransport(judge),
        **kwargs,
    )


async def run_case(question: str) -> tuple[str, list[str]]:
    return ANSWER, [CONTEXT]


def cases() -> list[EvalCase]:
    return [EvalCase("one", QUESTION, ANSWER, (CONTEXT,))]


async def test_real_ragas_four_metrics_and_reports() -> None:
    judge = Judge()
    ev = evaluator(judge)
    report = await ev.evaluate(cases=cases(), run_fn=run_case, dataset_name="synthetic")
    assert report.passed_count == 1
    assert report.failed_count == 0
    assert dict(report.aggregate_scores) == pytest.approx(dict.fromkeys(DEFAULT_METRICS, 1.0))
    assert [s.name for s in report.results[0].scores] == list(DEFAULT_METRICS)
    assert report.results[0].contexts == (CONTEXT,)
    assert report.results[0].answer == ANSWER
    assert len([r for r in judge.requests if r.url.path.endswith("/embeddings")]) == 2
    evidence = ev.to_evidence_report(report)
    assert evidence["cases"][0]["scores"] == pytest.approx(dict.fromkeys(DEFAULT_METRICS, 1.0))
    assert evidence["summary"]["retrieval_hit_rate"] is None
    assert evidence["summary"]["citation_coverage"] is None
    assert evidence["summary"]["no_answer_correctness"] is None
    assert evidence["cases"][0]["metric_errors"] == {}
    assert "one" in ev.to_markdown(report)
    json.dumps(evidence, allow_nan=False)


async def test_aggregate_uses_each_cases_actual_scores() -> None:
    judge = Judge()
    calls = 0

    async def alternating(question: str) -> tuple[str, list[str]]:
        nonlocal calls
        calls += 1
        return (ANSWER, [CONTEXT]) if calls == 1 else ("irrelevant-marker", ["irrelevant-marker"])

    report = await evaluator(judge).evaluate(
        cases=cases() + [EvalCase("two", QUESTION, ANSWER)], run_fn=alternating
    )
    assert (report.case_count, report.passed_count, report.failed_count) == (2, 1, 1)
    assert dict(report.aggregate_scores) == pytest.approx(dict.fromkeys(DEFAULT_METRICS, 0.5))
    assert all(score.score == 0 for score in report.results[1].scores)


@pytest.mark.parametrize("metric", DEFAULT_METRICS)
async def test_each_metric_and_only_relevancy_needs_embeddings(
    metric: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if metric != "answer_relevancy":
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        def forbidden(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("This metric must not construct an embedding client")

        monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", forbidden)
    judge = Judge()
    report = await evaluator(judge, metrics=(metric,)).evaluate(cases=cases(), run_fn=run_case)
    assert report.passed_count == 1
    assert list(report.aggregate_scores) == [metric]


@pytest.mark.parametrize(
    "metrics", [(), ("unknown",), ("faithfulness", "faithfulness"), "faithfulness"]
)
def test_invalid_metrics_rejected(metrics: Any) -> None:
    with pytest.raises(ValueError):
        evaluator(Judge(), metrics=metrics)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_threshold_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        evaluator(Judge(), pass_threshold=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1])
def test_invalid_timeout_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        evaluator(Judge(), timeout_seconds=value)


async def test_reference_is_required_before_generation() -> None:
    judge = Judge()

    async def forbidden(question: str) -> Any:
        raise AssertionError("Dataset invalid before any generation")

    report = await evaluator(judge).evaluate(cases=[EvalCase("one", QUESTION)], run_fn=forbidden)
    assert report.results[0].failure_stage == EvalEvidenceFailureStage.DATASET
    assert not judge.requests


async def test_no_reference_needed_by_reference_free_metrics() -> None:
    report = await evaluator(Judge(), metrics=("faithfulness", "answer_relevancy")).evaluate(
        cases=[EvalCase("one", QUESTION)],
        run_fn=run_case,
    )
    assert report.passed_count == 1


async def test_generation_failure_is_redacted_and_other_cases_continue(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing(question: str) -> Any:
        raise RuntimeError("private-key-and-user-prompt")

    report = await evaluator(Judge()).evaluate(cases=cases(), run_fn=failing)
    assert report.results[0].failure_stage == EvalEvidenceFailureStage.GENERATION
    assert "private-key" not in repr(report) + caplog.text
    assert report.average_latency_ms == report.results[0].latency_ms


@pytest.mark.parametrize("mode", ["fail", "malformed", "nonfinite"])
async def test_judge_failures_are_finite_fail_closed_and_redacted(
    mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    judge = Judge()
    setattr(judge, mode, True)
    ev = evaluator(judge, metrics=("faithfulness",), pass_threshold=0)
    report = await ev.evaluate(cases=cases(), run_fn=run_case)
    assert report.passed_count == 0
    assert report.results[0].failure_stage == EvalEvidenceFailureStage.RUNNER
    assert report.results[0].scores[0].score == 0.0
    assert report.results[0].scores[0].reason
    assert "private-provider-error" not in repr(report) + caplog.text
    assert "private-malformed-response" not in repr(report) + caplog.text
    json.dumps(ev.to_evidence_report(report), allow_nan=False)


@pytest.mark.parametrize("path", ["/chat/completions", "/embeddings"])
async def test_cancel_reaches_actual_sdk_request_without_blocking_loop(path: str) -> None:
    judge = Judge()
    judge.delay_path = path
    task = asyncio.create_task(
        evaluator(judge, metrics=("answer_relevancy",)).evaluate(cases=cases(), run_fn=run_case)
    )
    await asyncio.wait_for(judge.entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(judge.cancelled.wait(), 1)
    count = len(judge.requests)
    await asyncio.sleep(0.01)
    assert len(judge.requests) == count


@pytest.mark.parametrize("path", ["/chat/completions", "/embeddings"])
async def test_metric_timeout_cancels_request_and_marks_failure(path: str) -> None:
    judge = Judge()
    judge.delay_path = path
    report = await evaluator(judge, metrics=("answer_relevancy",), timeout_seconds=0.1).evaluate(
        cases=cases(), run_fn=run_case
    )
    assert judge.cancelled.is_set()
    assert report.results[0].scores[0].reason == "metric_timeout"
    assert report.passed_count == 0


async def test_generation_cancellation_propagates() -> None:
    async def cancelled(question: str) -> Any:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await evaluator(Judge()).evaluate(cases=cases(), run_fn=cancelled)


async def test_empty_dataset_does_not_make_requests() -> None:
    judge = Judge()
    report = await evaluator(judge).evaluate(cases=[], run_fn=run_case)
    assert report.case_count == 0
    assert report.average_latency_ms == 0
    assert report.aggregate_scores == {}
    assert not judge.requests


async def test_tracking_disabled_required_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragas._analytics import do_not_track

    from packages.eval.ragas_runtime import RagasConfigurationError

    monkeypatch.delenv("RAGAS_DO_NOT_TRACK")
    do_not_track.cache_clear()
    with pytest.raises(RagasConfigurationError, match="RAGAS_DO_NOT_TRACK"):
        await evaluator(Judge()).evaluate(cases=cases(), run_fn=run_case)


async def test_sdk_debug_logging_fails_closed() -> None:
    from packages.eval.ragas_runtime import RagasConfigurationError

    logger = logging.getLogger("openai._base_client")
    original = logger.level
    try:
        logger.setLevel(logging.DEBUG)
        with pytest.raises(RagasConfigurationError, match="debug logging"):
            await evaluator(Judge()).evaluate(cases=cases(), run_fn=run_case)
    finally:
        logger.setLevel(original)


@pytest.mark.parametrize(
    ("question", "responses"),
    [
        (QUESTION, [(QUESTION, 0)] * 3),
        ("orthogonal", [(QUESTION, 0)] * 3),
        ("opposite", [(QUESTION, 0)] * 3),
        (QUESTION, [(QUESTION, 1)] * 3),
        (QUESTION, [(QUESTION, 1), (QUESTION, 0), (QUESTION, 1)]),
        (QUESTION, [("", 0)] * 3),
        (QUESTION, [(QUESTION, 0), ("orthogonal", 0), ("opposite", 0)]),
    ],
)
async def test_async_prefetch_preserves_unmodified_upstream_score(
    question: str,
    responses: list[tuple[str, int]],
) -> None:
    import math

    from langchain_core.embeddings import Embeddings
    from langchain_core.outputs import Generation, LLMResult
    from ragas import SingleTurnSample
    from ragas.embeddings.base import LangchainEmbeddingsWrapper
    from ragas.llms.base import BaseRagasLLM
    from ragas.metrics._answer_relevance import AnswerRelevancy
    from ragas.run_config import RunConfig

    from packages.eval.ragas_runtime import create_metrics

    class DeterministicLLM(BaseRagasLLM):
        def is_finished(self, response: Any) -> bool:
            return True

        def generate_text(self, *args: Any, **kwargs: Any) -> Any:
            return LLMResult(
                generations=[
                    [
                        Generation(text=json.dumps({"question": q, "noncommittal": flag}))
                        for q, flag in responses
                    ]
                ]
            )

        async def agenerate_text(self, *args: Any, **kwargs: Any) -> Any:
            return self.generate_text()

    class Vectors(Embeddings):
        def __init__(self, allow_sync: bool) -> None:
            self.allow_sync = allow_sync
            self.async_calls: list[str] = []

        @staticmethod
        def vector(text: str) -> list[float]:
            return {"orthogonal": [0.0, 1.0], "opposite": [-1.0, 0.0]}.get(text, [1.0, 0.0])

        def embed_query(self, text: str) -> list[float]:
            assert self.allow_sync, "No synchronous provider call is permitted"
            return self.vector(text)

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            assert self.allow_sync, "No synchronous provider call is permitted"
            return [self.vector(text) for text in texts]

        async def aembed_query(self, text: str) -> list[float]:
            self.async_calls.append("query")
            return self.vector(text)

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            self.async_calls.append("documents")
            return [self.vector(text) for text in texts]

    config = RunConfig(max_retries=1)
    llm = DeterministicLLM(run_config=config)
    native = AnswerRelevancy(llm=llm, embeddings=LangchainEmbeddingsWrapper(Vectors(True)))
    native.init(config)
    vectors = Vectors(False)
    adapted = create_metrics(("answer_relevancy",), llm, vectors, config, question)[0]
    sample = SingleTurnSample(user_input=question, response=ANSWER, retrieved_contexts=[])
    native_score = await native.single_turn_ascore(sample)
    adapted_score = await adapted.single_turn_ascore(sample)
    assert (math.isnan(native_score) and math.isnan(adapted_score)) or native_score == adapted_score
    assert vectors.async_calls == (
        [] if all(q == "" for q, _ in responses) else ["query", "documents"]
    )


async def test_ambient_collectors_and_callback_config_cannot_capture_evaluation() -> None:
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.runnables.config import set_config_context
    from langchain_core.tracers.context import collect_runs

    seen: list[Any] = []

    class Capture(BaseCallbackHandler):
        def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
            seen.append(messages)

    with collect_runs() as collector:
        with set_config_context({"callbacks": [Capture()]}) as inherited:
            report = await asyncio.create_task(
                evaluator(Judge()).evaluate(cases=cases(), run_fn=run_case),
                context=inherited,
            )
        assert collector.traced_runs == []
    assert seen == []
    assert report.passed_count == 1


async def test_ambient_tracer_and_env_do_not_export_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from langchain_core.tracers.context import tracing_v2_callback_var, tracing_v2_enabled
    from langchain_core.tracers.langchain import LangChainTracer

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    saved: list[Any] = []
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: saved.append(run))
    with tracing_v2_enabled(client=MagicMock()) as tracer:
        report = await evaluator(Judge()).evaluate(cases=cases(), run_fn=run_case)
        assert tracing_v2_callback_var.get() is tracer
    assert saved == []
    assert report.passed_count == 1


async def test_empty_context_is_not_replaced_with_fake_chunk() -> None:
    judge = Judge()

    async def empty(question: str) -> tuple[str, list[str]]:
        return ANSWER, []

    report = await evaluator(judge, metrics=("context_precision",)).evaluate(
        cases=cases(), run_fn=empty
    )
    # Upstream precision is zero with no chunks; never ask the judge about "".
    assert report.results[0].contexts == ()
    assert not judge.requests
    assert report.results[0].scores[0].score == 0.0
    assert report.results[0].scores[0].reason == ""


async def test_metrics_do_not_initialize_multimodal_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from ragas.prompt.multi_modal_prompt import ImageTextPromptValue

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Multimodal processing is outside text evaluation")

    monkeypatch.setattr(ImageTextPromptValue, "_try_process_local_file", forbidden)
    monkeypatch.setattr(ImageTextPromptValue, "_try_process_allowed_url", forbidden)

    async def literals(question: str) -> tuple[str, list[str]]:
        return ANSWER, ["https://untrusted.invalid/not-fetched", "file:///not-read"]

    report = await evaluator(Judge()).evaluate(cases=cases(), run_fn=literals)
    assert report.passed_count == 1


@pytest.mark.parametrize(
    "name", ["LANGCHAIN_WANDB_TRACING", "LANGCHAIN_COMET_TRACING", "SYNTHETIC_TRACE_HOOK"]
)
async def test_environment_callback_hooks_fail_closed(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextvars import ContextVar

    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.tracers.context import _configure_hooks

    from packages.eval.ragas_runtime import RagasConfigurationError

    seen: list[Any] = []

    class Capture(BaseCallbackHandler):
        def on_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
            seen.append(inputs)

    hook = (ContextVar("synthetic_env_capture", default=None), True, Capture, name)
    _configure_hooks.append(hook)
    monkeypatch.setenv(name, "true")
    try:
        with pytest.raises(RagasConfigurationError, match="Environment-enabled"):
            await evaluator(Judge()).evaluate(cases=cases(), run_fn=run_case)
    finally:
        _configure_hooks.remove(hook)
    assert not seen


async def test_contextvar_default_hook_fails_closed() -> None:
    from contextvars import ContextVar

    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.tracers.context import _configure_hooks

    from packages.eval.ragas_runtime import RagasConfigurationError

    hook = (
        ContextVar("synthetic_default_capture", default=BaseCallbackHandler()),  # noqa: B039
        True,
        None,
        None,
    )
    _configure_hooks.append(hook)
    try:
        with pytest.raises(RagasConfigurationError, match="Default framework"):
            await evaluator(Judge()).evaluate(cases=cases(), run_fn=run_case)
    finally:
        _configure_hooks.remove(hook)


async def test_generation_and_judge_timeouts_are_independent() -> None:
    async def slow_generation(question: str) -> tuple[str, list[str]]:
        await asyncio.sleep(0.05)
        return ANSWER, []

    # ContextPrecision(empty context) performs no judge call, keeping this test
    # deterministic while proving that generation has its own larger budget.
    report = await evaluator(
        Judge(), metrics=("context_precision",), timeout_seconds=0.01, generation_timeout_seconds=1
    ).evaluate(cases=cases(), run_fn=slow_generation)
    assert report.results[0].failure_stage is None
    assert report.results[0].scores[0].score == 0


async def test_generation_timeout_cancels_call() -> None:
    closed = asyncio.Event()

    async def slow_generation(question: str) -> Any:
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    report = await evaluator(Judge(), generation_timeout_seconds=0.01).evaluate(
        cases=cases(), run_fn=slow_generation
    )
    assert closed.is_set()
    assert report.results[0].failure_stage == EvalEvidenceFailureStage.GENERATION


@pytest.mark.parametrize("metric", ["faithfulness", "answer_relevancy"])
async def test_bare_answer_cannot_imply_contexts_for_context_metrics(metric: str) -> None:
    async def answer_only(question: str) -> str:
        return ANSWER

    judge = Judge()
    report = await evaluator(judge, metrics=(metric,)).evaluate(cases=cases(), run_fn=answer_only)
    if metric == "faithfulness":
        assert report.results[0].failure_stage == EvalEvidenceFailureStage.GENERATION
        assert not judge.requests
    else:
        assert report.passed_count == 1
