from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from tests.eval.rag.dto import (
    FailureStage,
    RagEvalCase,
    RagEvalCaseResult,
    RagEvalDataset,
    RagEvalReport,
    RagEvalReportSummary,
)


class RagEvalCaseSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    category: str
    tenant_id: str
    user_id: str
    top_k: int
    expected_documents: tuple[str, ...] = ()
    expected_chunks: tuple[str, ...] = ()
    expected_citations: tuple[str, ...] = ()
    answerable: bool
    expected_no_answer: bool
    acl_isolation: bool
    prompt_injection: bool


class RagEvalDatasetSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_count: int
    answerable_count: int
    no_answer_count: int
    acl_case_count: int
    prompt_injection_case_count: int
    citation_expected_count: int
    dataset_version: str
    failure_stages: tuple[FailureStage, ...]


class RagEvalDatasetReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    report_type: Literal["rag_dataset_smoke"] = "rag_dataset_smoke"
    summary: RagEvalDatasetSummary
    cases: tuple[RagEvalCaseSummary, ...]


def summarize_rag_eval_dataset(dataset: RagEvalDataset) -> RagEvalDatasetReport:
    cases = tuple(_summarize_case(case) for case in dataset.cases)
    required_citation_count = sum(
        1 for case in dataset.cases for citation in case.expected_citations if citation.required
    )
    summary = RagEvalDatasetSummary(
        case_count=len(dataset.cases),
        answerable_count=sum(1 for case in dataset.cases if case.answerable),
        no_answer_count=sum(1 for case in dataset.cases if case.expected_no_answer),
        acl_case_count=sum(1 for case in dataset.cases if case.attack_type == "acl_isolation"),
        prompt_injection_case_count=sum(
            1 for case in dataset.cases if case.attack_type == "prompt_injection"
        ),
        citation_expected_count=required_citation_count,
        dataset_version=dataset.dataset_version,
        failure_stages=(
            "retrieval",
            "rerank",
            "context_packing",
            "prompt_build",
            "generation",
            "citation",
            "permission",
            "no_answer",
            "dataset",
            "runner",
        ),
    )
    return RagEvalDatasetReport(summary=summary, cases=cases)


def write_json_report(
    report: RagEvalDatasetReport,
    *,
    report_dir: Path | None = None,
    report_path: Path | None = None,
) -> Path:
    if report_path is None:
        target_dir = report_dir or Path("tests/eval/reports")
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = report.generated_at.strftime("%Y%m%dT%H%M%S%fZ")
        report_path = target_dir / f"rag-dataset-smoke-{stamp}-{uuid4().hex[:8]}.json"
    else:
        report_path.parent.mkdir(parents=True, exist_ok=True)

    payload = report.model_dump(mode="json")
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report_path


def build_rag_eval_report(
    *,
    results: tuple[RagEvalCaseResult, ...],
    summary: RagEvalReportSummary,
) -> RagEvalReport:
    return RagEvalReport(
        generated_at=datetime.now(UTC).isoformat(),
        summary=summary,
        cases=results,
    )


def write_rag_eval_report(
    report: RagEvalReport,
    *,
    report_dir: Path | None = None,
    report_path: Path | None = None,
) -> Path:
    if report_path is None:
        target_dir = report_dir or Path("tests/eval/reports")
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        report_path = target_dir / f"rag-smoke-{stamp}-{uuid4().hex[:8]}.json"
    else:
        report_path.parent.mkdir(parents=True, exist_ok=True)

    payload = report.model_dump(mode="json")
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report_path


def _summarize_case(case: RagEvalCase) -> RagEvalCaseSummary:
    expected_citation_ids = tuple(
        f"{citation.document_id}:{citation.version_id}:{citation.chunk_id}"
        for citation in case.expected_citations
    )
    return RagEvalCaseSummary(
        case_id=case.case_id,
        category=case.category,
        tenant_id=case.tenant_id,
        user_id=case.user_id,
        top_k=case.top_k,
        expected_documents=case.expected_documents,
        expected_chunks=case.expected_chunks,
        expected_citations=expected_citation_ids,
        answerable=case.answerable,
        expected_no_answer=case.expected_no_answer,
        acl_isolation=case.attack_type == "acl_isolation",
        prompt_injection=case.attack_type == "prompt_injection",
    )
