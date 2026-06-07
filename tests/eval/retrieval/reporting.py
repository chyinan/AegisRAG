from __future__ import annotations

import json
from pathlib import Path

from tests.eval.retrieval.dto import (
    RetrievalEvalCaseResult,
    RetrievalEvalReport,
    RetrievalEvalReportSummary,
)


def build_report(
    *,
    results: tuple[RetrievalEvalCaseResult, ...],
    summary: RetrievalEvalReportSummary,
) -> RetrievalEvalReport:
    return RetrievalEvalReport(summary=summary, cases=results)


def write_json_report(
    report: RetrievalEvalReport,
    *,
    report_dir: Path | None = None,
    report_path: Path | None = None,
) -> Path:
    if report_path is None:
        target_dir = report_dir or Path("tests/eval/reports")
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = report.generated_at.strftime("%Y%m%dT%H%M%SZ")
        report_path = target_dir / f"retrieval-smoke-{stamp}.json"
    else:
        report_path.parent.mkdir(parents=True, exist_ok=True)

    payload = report.model_dump(mode="json")
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report_path
