from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from tests.eval.retrieval.loader import (
    RetrievalEvalDatasetError,
    load_retrieval_eval_cases,
    load_retrieval_eval_corpus,
)
from tests.eval.retrieval.runner import FixtureCandidateRetriever, run_retrieval_eval

DEFAULT_DATASET = Path("tests/eval/datasets/retrieval_smoke.json")
DEFAULT_REPORT_DIR = Path("tests/eval/reports")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local retrieval eval smoke fixtures.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        cases = load_retrieval_eval_cases(args.dataset)
        corpus = load_retrieval_eval_corpus(
            args.dataset,
            case_ids={case.case_id for case in cases},
        )
        report = asyncio.run(
            run_retrieval_eval(
                cases,
                retriever=FixtureCandidateRetriever(corpus),
                report_dir=args.report_dir,
                top_k=args.top_k,
            )
        )
    except RetrievalEvalDatasetError as exc:
        print(f"retrieval eval dataset error: {exc}")
        return 2
    except Exception as exc:
        print(f"retrieval eval runner error: {type(exc).__name__}")
        return 3

    print(json.dumps(report.summary.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 0 if report.summary.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
