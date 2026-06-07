from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from tests.eval.rag.loader import RagEvalDatasetError, load_rag_eval_dataset
from tests.eval.rag.reporting import summarize_rag_eval_dataset, write_json_report

DEFAULT_DATASET = Path("tests/eval/datasets/rag_smoke.json")
DEFAULT_REPORT_DIR = Path("tests/eval/reports")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate local RAG eval dataset fixtures.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args(argv)

    try:
        dataset = load_rag_eval_dataset(args.dataset)
        report = summarize_rag_eval_dataset(dataset)
        write_json_report(report, report_dir=args.report_dir)
    except RagEvalDatasetError as exc:
        print(f"rag eval dataset error: {exc}")
        return 2
    except Exception as exc:
        print(f"rag eval dataset smoke error: {type(exc).__name__}")
        return 3

    print(json.dumps(report.summary.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
