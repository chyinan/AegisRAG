"""
RAGAS-powered RAG evaluation engine.

Computes the four industry-standard RAG metrics:
  • Context Precision  — are retrieved chunks relevant to the question?
  • Context Recall     — did we retrieve all relevant information?
  • Faithfulness       — is the answer grounded in retrieved context?
  • Answer Relevancy   — does the answer address the question?

Maps directly to the existing EvalEvidenceReport format for seamless integration
with the governance API.
"""
from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.eval.dto import (
    EvalCaseEvidence,
    EvalEvidenceFailureStage,
    EvalEvidenceGenerationSummary,
    EvalEvidenceReportType,
)

try:
    from ragas import evaluate
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )
    from ragas.metrics._answer_relevancy import AnswerRelevancy
    from ragas.metrics._context_precision import ContextPrecision
    from ragas.metrics._context_recall import ContextRecall
    from ragas.metrics._faithfulness import Faithfulness

    RAGAS_AVAILABLE = True
except ImportError:
    RAGAS_AVAILABLE = False


DEFAULT_METRICS = ("context_precision", "context_recall", "faithfulness", "answer_relevancy")

METRIC_CLASSES: dict[str, type] = {}
if RAGAS_AVAILABLE:
    METRIC_CLASSES = {
        "context_precision": ContextPrecision,
        "context_recall": ContextRecall,
        "faithfulness": Faithfulness,
        "answer_relevancy": AnswerRelevancy,
    }


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
    ) -> None:
        if not RAGAS_AVAILABLE:
            raise ImportError(
                "ragas is required. Install: pip install ragas"
            )
        self._llm_model = llm_model
        self._llm_base_url = llm_base_url
        self._llm_api_key = llm_api_key
        self._embedding_model = embedding_model
        self._embedding_base_url = embedding_base_url
        self._embedding_api_key = embedding_api_key
        self._pass_threshold = pass_threshold
        self._metric_names = tuple(metrics)

    def evaluate(
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
        import asyncio

        run_id = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        results: list[CaseResult] = []
        total_latency = 0.0

        for case in cases:
            started = time.perf_counter()
            try:
                raw = asyncio.get_event_loop().run_until_complete(run_fn(case.question))
                if isinstance(raw, tuple) and len(raw) == 2:
                    answer, contexts = raw
                else:
                    answer = str(raw)
                    contexts = []
            except Exception as exc:
                results.append(
                    CaseResult(
                        case=case,
                        scores=(),
                        answer=f"ERROR: {exc}",
                        contexts=(),
                        latency_ms=(time.perf_counter() - started) * 1000,
                        passed=False,
                        failure_stage=EvalEvidenceFailureStage.GENERATION,
                    )
                )
                continue

            latency_ms = (time.perf_counter() - started) * 1000
            total_latency += latency_ms
            scores = self._compute_metrics(
                question=case.question,
                answer=answer,
                contexts=list(contexts),
                reference_answer=case.reference_answer,
                reference_contexts=list(case.reference_contexts),
            )
            avg_score = sum(s.score for s in scores) / max(len(scores), 1)
            results.append(
                CaseResult(
                    case=case,
                    scores=scores,
                    answer=answer,
                    contexts=tuple(contexts),
                    latency_ms=latency_ms,
                    passed=avg_score >= self._pass_threshold,
                )
            )

        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count

        aggregate: dict[str, float] = {}
        for metric_name in self._metric_names:
            metric_scores = [
                s.score for r in results for s in r.scores if s.name == metric_name
            ]
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
            average_latency_ms=total_latency / max(len(cases), 1),
            failure_stages=tuple(failure_stages),
        )

    def _compute_metrics(
        self,
        *,
        question: str,
        answer: str,
        contexts: list[str],
        reference_answer: str | None,
        reference_contexts: list[str],
    ) -> tuple[MetricScore, ...]:
        """Compute RAGAS metrics for a single case."""
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        llm_kwargs: dict[str, Any] = {"model": self._llm_model, "temperature": 0}
        if self._llm_base_url:
            llm_kwargs["base_url"] = self._llm_base_url
        if self._llm_api_key:
            llm_kwargs["api_key"] = self._llm_api_key

        emb_kwargs: dict[str, Any] = {"model": self._embedding_model}
        if self._embedding_base_url:
            emb_kwargs["base_url"] = self._embedding_base_url
        if self._embedding_api_key:
            emb_kwargs["api_key"] = self._embedding_api_key

        llm = ChatOpenAI(**llm_kwargs)
        embeddings = OpenAIEmbeddings(**emb_kwargs)

        # Build dataset row
        row: dict[str, Any] = {
            "question": [question],
            "answer": [answer],
            "contexts": [contexts] if contexts else [[""]],
        }
        if reference_answer:
            row["ground_truth"] = [reference_answer]
        if reference_contexts:
            row["reference_contexts"] = [reference_contexts]

        from ragas import EvaluationDataset
        ds = EvaluationDataset.from_dict(row)

        metrics = [
            METRIC_CLASSES[name](llm=llm, embeddings=embeddings)
            for name in self._metric_names
            if name in METRIC_CLASSES
        ]
        if not metrics:
            return ()

        result = evaluate(ds, metrics=metrics, raise_exceptions=False)
        df = result.to_pandas()

        scores: list[MetricScore] = []
        metric_name_map = {
            "context_precision": "context_precision",
            "context_recall": "context_recall",
            "faithfulness": "faithfulness",
            "answer_relevancy": "answer_relevancy",
        }
        for col in df.columns:
            if col in metric_name_map:
                val = float(df[col].iloc[0]) if not df[col].isna().iloc[0] else 0.0
                scores.append(MetricScore(name=metric_name_map[col], score=val))
            elif "context_precision" in col:
                val = float(df[col].iloc[0]) if not df[col].isna().iloc[0] else 0.0
                scores.append(MetricScore(name="context_precision", score=val))
            elif "context_recall" in col:
                val = float(df[col].iloc[0]) if not df[col].isna().iloc[0] else 0.0
                scores.append(MetricScore(name="context_recall", score=val))
            elif "faithfulness" in col:
                val = float(df[col].iloc[0]) if not df[col].isna().iloc[0] else 0.0
                scores.append(MetricScore(name="faithfulness", score=val))
            elif "answer_relevancy" in col:
                val = float(df[col].iloc[0]) if not df[col].isna().iloc[0] else 0.0
                scores.append(MetricScore(name="answer_relevancy", score=val))

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
                "retrieval_hit_rate": report.aggregate_scores.get("context_precision"),
                "citation_coverage": report.aggregate_scores.get("faithfulness"),
                "no_answer_correctness": report.aggregate_scores.get("answer_relevancy"),
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
            f"**Cases:** {report.case_count} total, {report.passed_count} passed, {report.failed_count} failed",
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
