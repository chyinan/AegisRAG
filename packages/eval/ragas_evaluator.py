"""
RAGAS-powered RAG evaluation engine.

Computes the four industry-standard RAG metrics:
  • Context Precision  — are retrieved chunks relevant to the question?
  • Context Recall     — did we retrieve all relevant information?
  • Faithfulness       — is the answer grounded in retrieved context?
  • Answer Relevancy   — does the answer address the question?

Keeps the existing evidence envelope; actual Ragas metrics remain in their
dedicated named scores rather than aliases for unrelated governance metrics.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from packages.common.framework_context import isolated_framework_context
from packages.eval.dto import (
    EvalEvidenceFailureStage,
    EvalEvidenceReportType,
)

# Keep the optional package lazy: importing the normal application must not
# initialize Ragas clients, analytics or its LangChain dependencies.
RAGAS_AVAILABLE = importlib.util.find_spec("ragas") is not None
DEFAULT_METRICS = ("context_precision", "context_recall", "faithfulness", "answer_relevancy")


@dataclass(frozen=True)
class EvalCase:
    """A single evaluation case with ground truth."""

    case_id: str
    question: str
    reference_answer: str | None = None
    reference_contexts: tuple[str, ...] = ()
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True)
class MetricScore:
    name: str
    score: float
    reason: str = ""


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    scores: tuple[MetricScore, ...]
    answer: str
    contexts: tuple[str, ...]
    latency_ms: float
    passed: bool
    failure_stage: EvalEvidenceFailureStage | None = None


@dataclass(frozen=True)
class EvalReport:
    run_id: str
    generated_at: str
    dataset_name: str
    dataset_version: str
    case_count: int
    passed_count: int
    failed_count: int
    results: tuple[CaseResult, ...]
    aggregate_scores: Mapping[str, float]
    average_latency_ms: float
    failure_stages: tuple[EvalEvidenceFailureStage, ...]


class RagasEvaluator:
    """RAGAS-based evaluator that plugs into the AegisRAG eval infrastructure."""

    def __init__(
        self,
        *,
        llm_model: str = "gpt-4o-mini",
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        embedding_model: str = "text-embedding-3-small",
        embedding_base_url: str | None = None,
        embedding_api_key: str | None = None,
        pass_threshold: float = 0.70,
        metrics: Sequence[str] = DEFAULT_METRICS,
        timeout_seconds: float = 60.0,
        generation_timeout_seconds: float = 60.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not RAGAS_AVAILABLE:
            raise ImportError("ragas is required. Install: pip install ragas")
        if (
            not metrics
            or isinstance(metrics, str)
            or any(name not in DEFAULT_METRICS for name in metrics)
        ):
            raise ValueError("Choose at least one supported text evaluation metric.")
        if len(set(metrics)) != len(metrics):
            raise ValueError("Evaluation metrics must be unique.")
        if not math.isfinite(pass_threshold) or not 0 <= pass_threshold <= 1:
            raise ValueError("pass_threshold must be finite and between 0 and 1.")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive.")
        if importlib.metadata.version("ragas") != "0.4.3":
            raise RuntimeError("Use the locked eval dependency group (Ragas 0.4.3).")
        if not math.isfinite(generation_timeout_seconds) or generation_timeout_seconds <= 0:
            raise ValueError("generation_timeout_seconds must be finite and positive.")
        self._generation_timeout_seconds = generation_timeout_seconds
        self._timeout_seconds = timeout_seconds
        self._http_transport = http_transport
        self._llm_model = llm_model
        self._llm_base_url = llm_base_url
        self._llm_api_key = llm_api_key
        self._embedding_model = embedding_model
        self._embedding_base_url = embedding_base_url
        self._embedding_api_key = embedding_api_key
        self._pass_threshold = pass_threshold
        self._metric_names = tuple(metrics)

    async def evaluate(
        self,
        *,
        cases: Sequence[EvalCase],
        run_fn: Any,  # async (question: str) -> (answer: str, contexts: list[str])
        dataset_name: str = "default",
        dataset_version: str = "v1",
    ) -> EvalReport:
        """Run evaluation across all cases.

        Args:
            cases: Evaluation cases with ground truth
            run_fn: Async callable that takes a question string and returns
                    (answer: str, contexts: list[str])
            dataset_name: Name for the dataset
            dataset_version: Version tag
        """

        from packages.eval.ragas_runtime import RagasConfigurationError, assert_private_execution

        assert_private_execution()
        run_id = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        results: list[CaseResult] = []
        for case in cases:
            started = time.perf_counter()
            # Precision/recall require a reference answer, not reference chunks.
            if (
                set(self._metric_names) & {"context_precision", "context_recall"}
                and not case.reference_answer
            ):
                results.append(
                    CaseResult(
                        case=case,
                        scores=(),
                        answer="",
                        contexts=(),
                        latency_ms=0.0,
                        passed=False,
                        failure_stage=EvalEvidenceFailureStage.DATASET,
                    )
                )
                continue
            try:
                async with asyncio.timeout(self._generation_timeout_seconds):
                    raw = await run_fn(case.question)
                if isinstance(raw, tuple) and len(raw) == 2:
                    answer, contexts = raw
                elif isinstance(raw, str) and self._metric_names == ("answer_relevancy",):
                    answer, contexts = raw, []
                else:
                    raise ValueError("Invalid generation result.")
                if (
                    not isinstance(answer, str)
                    or not isinstance(contexts, (list, tuple))
                    or any(not isinstance(context, str) for context in contexts)
                ):
                    raise ValueError("Generation must return text and a sequence of text contexts.")
            except Exception:
                # Provider exception strings may contain prompts, keys or bodies.
                results.append(
                    CaseResult(
                        case=case,
                        scores=(),
                        answer="ERROR: generation failed",
                        contexts=(),
                        latency_ms=(time.perf_counter() - started) * 1000,
                        passed=False,
                        failure_stage=EvalEvidenceFailureStage.GENERATION,
                    )
                )
                continue

            latency_ms = (time.perf_counter() - started) * 1000
            try:
                scores = await asyncio.create_task(
                    self._compute_metrics(
                        question=case.question,
                        answer=answer,
                        contexts=list(contexts),
                        reference_answer=case.reference_answer,
                        reference_contexts=list(case.reference_contexts),
                    ),
                    context=isolated_framework_context(),
                )
            except RagasConfigurationError:
                raise
            except Exception:
                scores = tuple(
                    MetricScore(name, 0.0, "metric_setup_failed") for name in self._metric_names
                )
            metric_failed = any(score.reason for score in scores)
            avg_score = sum(score.score for score in scores) / len(self._metric_names)
            results.append(
                CaseResult(
                    case=case,
                    scores=scores,
                    answer=answer,
                    contexts=tuple(contexts),
                    latency_ms=latency_ms,
                    passed=not metric_failed and avg_score >= self._pass_threshold,
                    failure_stage=EvalEvidenceFailureStage.RUNNER if metric_failed else None,
                )
            )

        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count

        aggregate: dict[str, float] = {}
        for metric_name in self._metric_names:
            metric_scores = [s.score for r in results for s in r.scores if s.name == metric_name]
            if metric_scores:
                aggregate[metric_name] = sum(metric_scores) / len(metric_scores)

        failure_stages: list[EvalEvidenceFailureStage] = []
        for r in results:
            if not r.passed and r.failure_stage and r.failure_stage not in failure_stages:
                failure_stages.append(r.failure_stage)

        return EvalReport(
            run_id=run_id,
            generated_at=datetime.now(tz=UTC).isoformat(),
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            case_count=len(cases),
            passed_count=passed_count,
            failed_count=failed_count,
            results=tuple(results),
            aggregate_scores=aggregate,
            average_latency_ms=sum(result.latency_ms for result in results) / max(len(cases), 1),
            failure_stages=tuple(failure_stages),
        )

    async def _compute_metrics(
        self,
        *,
        question: str,
        answer: str,
        contexts: list[str],
        reference_answer: str | None,
        reference_contexts: list[str],
    ) -> tuple[MetricScore, ...]:
        """Await actual Ragas text metrics without patching the caller's loop."""
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from langsmith import tracing_context
        from ragas import EvaluationDataset, SingleTurnSample
        from ragas.llms import LangchainLLMWrapper
        from ragas.run_config import RunConfig

        from packages.eval.ragas_runtime import assert_private_execution, create_metrics

        assert_private_execution()
        # Typed, row-oriented v2 data. Do not invent an empty context chunk.
        sample = SingleTurnSample(
            user_input=question,
            response=answer,
            retrieved_contexts=contexts,
            reference=reference_answer,
            reference_contexts=reference_contexts,
        )
        dataset = EvaluationDataset(samples=[sample])
        config = RunConfig(timeout=math.ceil(self._timeout_seconds), max_retries=1)
        options: dict[str, Any] = {
            "max_retries": 0,
            "timeout": self._timeout_seconds,
        }
        llm_options: dict[str, Any] = {
            **options,
            "model": self._llm_model,
            "temperature": 0,
            "cache": False,
            "callbacks": None,
            "verbose": False,
            "use_responses_api": False,
        }
        if self._llm_base_url:
            llm_options["base_url"] = self._llm_base_url
        if self._llm_api_key:
            llm_options["api_key"] = self._llm_api_key
        embedding_options: dict[str, Any] = {
            **options,
            "model": self._embedding_model,
            "check_embedding_ctx_length": False,
        }
        if self._embedding_base_url:
            embedding_options["base_url"] = self._embedding_base_url
        if self._embedding_api_key:
            embedding_options["api_key"] = self._embedding_api_key

        # Own all SDK transports for the operation; close on success, error and
        # cancellation. No worker thread or synchronous HTTP scoring is used.
        def reject_sync_request(request: httpx.Request) -> httpx.Response:
            raise RuntimeError("Synchronous evaluation HTTP is disabled.")

        with (
            tracing_context(enabled=False),
            httpx.Client(
                transport=httpx.MockTransport(reject_sync_request),
            ) as sync_client,
        ):
            async with httpx.AsyncClient(transport=self._http_transport) as async_client:
                try:
                    clients: dict[str, Any] = {
                        "http_client": sync_client,
                        "http_async_client": async_client,
                    }
                    llm = LangchainLLMWrapper(
                        ChatOpenAI(**llm_options, **clients), run_config=config
                    )
                    embeddings = (
                        OpenAIEmbeddings(**embedding_options, **clients)
                        if "answer_relevancy" in self._metric_names
                        else None
                    )
                    metrics = create_metrics(self._metric_names, llm, embeddings, config, question)
                except Exception:
                    return tuple(
                        MetricScore(name, 0.0, "metric_setup_failed") for name in self._metric_names
                    )

                scores = []
                for name, metric in zip(self._metric_names, metrics, strict=True):
                    assert_private_execution()
                    try:
                        value = float(
                            await metric.single_turn_ascore(
                                dataset.samples[0],
                                callbacks=[],
                                timeout=self._timeout_seconds,
                            )
                        )
                        if not math.isfinite(value):
                            scores.append(MetricScore(name, 0.0, "metric_non_finite"))
                        else:
                            scores.append(MetricScore(name, value))
                    except TimeoutError:
                        scores.append(MetricScore(name, 0.0, "metric_timeout"))
                    except Exception:
                        scores.append(MetricScore(name, 0.0, "metric_failed"))
                return tuple(scores)

    def to_evidence_report(
        self,
        report: EvalReport,
    ) -> dict[str, Any]:
        """Convert EvalReport to the existing EvalEvidence JSON format."""
        return {
            "report_type": EvalEvidenceReportType.RAG_QUALITY_RUNNER.value,
            "generated_at": report.generated_at,
            "run_id": report.run_id,
            "summary": {
                "case_count": report.case_count,
                "passed_count": report.passed_count,
                "failed_count": report.failed_count,
                "retrieval_hit_rate": None,
                "citation_coverage": None,
                "no_answer_correctness": None,
                "average_latency_ms": report.average_latency_ms,
                "context_recall": report.aggregate_scores.get("context_recall"),
                "pass_threshold": self._pass_threshold,
                "failure_stages": [fs.value for fs in report.failure_stages],
            },
            "dataset": {
                "name": report.dataset_name,
                "version": report.dataset_version,
            },
            "aggregate_scores": dict(report.aggregate_scores),
            "cases": [
                {
                    "case_id": r.case.case_id,
                    "question": r.case.question,
                    "answer": r.answer,
                    "passed": r.passed,
                    "latency_ms": r.latency_ms,
                    "failure_stage": r.failure_stage.value if r.failure_stage else None,
                    "scores": {s.name: s.score for s in r.scores},
                    "metric_errors": {s.name: s.reason for s in r.scores if s.reason},
                }
                for r in report.results
            ],
        }

    def to_markdown(self, report: EvalReport) -> str:
        """Generate a readable markdown report."""
        lines = [
            f"# RAG Evaluation Report — {report.dataset_name}",
            "",
            f"**Generated:** {report.generated_at}",
            f"**Dataset:** {report.dataset_name} ({report.dataset_version})",
            f"**Cases:** {report.case_count} total, {report.passed_count} passed, "
            f"{report.failed_count} failed",
            f"**Pass Threshold:** {self._pass_threshold:.0%}",
            f"**Avg Latency:** {report.average_latency_ms:.0f}ms",
            "",
            "## Aggregate Scores",
            "",
            "| Metric | Score |",
            "|--------|-------|",
        ]
        for name, score in sorted(report.aggregate_scores.items()):
            emoji = "✅" if score >= self._pass_threshold else "❌"
            lines.append(f"| {name} | {emoji} {score:.4f} |")

        lines += [
            "",
            "## Per-Question Results",
            "",
        ]
        for r in report.results:
            status = "✅ PASS" if r.passed else "❌ FAIL"
            lines.append(f"### {r.case.case_id} — {status}")
            lines.append(f"**Q:** {r.case.question}")
            lines.append(f"**A:** {r.answer[:300]}{'...' if len(r.answer) > 300 else ''}")
            lines.append(f"**Latency:** {r.latency_ms:.0f}ms")
            lines.append("")
            lines.append("| Metric | Score |")
            lines.append("|--------|-------|")
            for s in r.scores:
                emoji = "✅" if s.score >= self._pass_threshold else "⚠️"
                lines.append(f"| {s.name} | {emoji} {s.score:.4f} |")
            if not r.passed and r.failure_stage:
                lines.append(f"\n**Failure Stage:** {r.failure_stage.value}")
            lines.append("")

        return "\n".join(lines)
