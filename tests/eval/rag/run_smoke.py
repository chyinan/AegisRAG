from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from tests.eval.rag.loader import RagEvalDatasetError, load_rag_eval_dataset
from tests.eval.rag.runner import run_rag_eval

DEFAULT_DATASET = Path("tests/eval/datasets/rag_smoke.json")
DEFAULT_REPORT_DIR = Path("tests/eval/reports")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local RAG quality eval fixtures.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        dataset = load_rag_eval_dataset(args.dataset)
        report = asyncio.run(
            run_rag_eval(
                dataset.cases,
                dataset.corpus,
                report_dir=args.report_dir,
                report_path=args.report_path,
                top_k=args.top_k,
            )
        )
    except RagEvalDatasetError as exc:
        print(f"rag eval dataset error: {exc}")
        return 2
    except Exception:
        print("rag eval runner error: runner")
        return 3

    print(json.dumps(report.summary.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
