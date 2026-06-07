from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval.retrieval.loader import (
    load_retrieval_eval_cases,
    load_retrieval_eval_corpus,
)
from tests.eval.retrieval.runner import FixtureCandidateRetriever, run_retrieval_eval

DATASET = Path("tests/eval/datasets/retrieval_smoke.json")


@pytest.mark.asyncio
async def test_retrieval_smoke_dataset_runs_to_green_report(tmp_path: Path) -> None:
    cases = load_retrieval_eval_cases(DATASET)
    corpus = load_retrieval_eval_corpus(DATASET)

    report = await run_retrieval_eval(
        cases,
        retriever=FixtureCandidateRetriever(corpus),
        report_dir=tmp_path,
    )

    assert report.summary.case_count == 20
    assert report.summary.failed_count == 0
    assert report.summary.retrieval_hit_rate == 1.0
    assert report.summary.acl_isolation_passed
    assert report.summary.no_answer_passed
    assert report.summary.prompt_injection_passed
