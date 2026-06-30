from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from packages.retrieval.dto import MAX_RETRIEVAL_TOP_K
from tests.eval.rag.gate import (
    RagEvalGateError,
    decide_rag_eval_gate,
    load_rag_eval_gate_config,
    write_rag_eval_gate_report,
)
from tests.eval.rag.loader import RagEvalDatasetError, load_rag_eval_dataset
from tests.eval.rag.runner import run_rag_eval

DEFAULT_DATASET = Path("tests/eval/datasets/rag_smoke.json")
DEFAULT_EXTENDED_DATASET = Path("tests/eval/datasets/rag_extended.json")
DEFAULT_CONFIG = Path("tests/eval/config/rag_smoke_gate.json")
DEFAULT_EXTENDED_CONFIG = Path("tests/eval/config/rag_extended_gate.json")
DEFAULT_REPORT_DIR = Path("tests/eval/reports")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run RAG eval CI smoke gate.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--extended", action="store_true", help="Use the extended 220-case eval dataset")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args(argv)

    if args.extended:
        if args.dataset == DEFAULT_DATASET:
            args.dataset = DEFAULT_EXTENDED_DATASET
        if args.config == DEFAULT_CONFIG:
            args.config = DEFAULT_EXTENDED_CONFIG

    try:
        config = load_rag_eval_gate_config(args.config)
        dataset = load_rag_eval_dataset(args.dataset)
        _validate_top_k_arg(args.top_k)
    except (RagEvalDatasetError, RagEvalGateError) as exc:
        print(f"rag eval gate validation error: {exc}")
        return 2

    try:
        runner_report = asyncio.run(
            run_rag_eval(
                dataset.cases,
                dataset.corpus,
                top_k=args.top_k,
            )
        )
        failure_cases = tuple(
            (case.case_id, case.failure_stage or "runner")
            for case in runner_report.cases
            if not case.passed
        )
        decision = decide_rag_eval_gate(
            summary=runner_report.summary,
            failure_cases=failure_cases,
            config=config,
        )
        report_path = write_rag_eval_gate_report(
            runner_summary=runner_report.summary,
            decision=decision,
            config=config,
            dataset_path=args.dataset,
            report_dir=args.report_dir,
            report_path=args.report_path,
        )
    except Exception:
        print("rag eval gate runner error: runner")
        return 3

    print(
        json.dumps(
            {
                "decision": "pass" if decision.passed else "fail",
                "case_count": runner_report.summary.case_count,
                "failed_count": runner_report.summary.failed_count,
                "metrics": {
                    "retrieval_hit_rate": runner_report.summary.retrieval_hit_rate,
                    "citation_coverage": runner_report.summary.citation_coverage,
                    "no_answer_correctness": runner_report.summary.no_answer_correctness,
                    "acl_isolation_passed": runner_report.summary.acl_isolation_passed,
                    "prompt_injection_passed": runner_report.summary.prompt_injection_passed,
                },
                "failed_metric_names": list(decision.failed_metric_names),
                "failed_metrics": [
                    metric.model_dump(mode="json")
                    for metric in decision.metrics
                    if not metric.passed
                ],
                "failed_case_ids": list(decision.failed_case_ids),
                "failure_stages": list(decision.failure_stages),
                "report_file": report_path.name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if decision.passed else 1


def _validate_top_k_arg(top_k: int | None) -> None:
    if top_k is None:
        return
    if top_k <= 0 or top_k > MAX_RETRIEVAL_TOP_K:
        raise RagEvalGateError(
            code="invalid_top_k_override",
            details={"top_k": top_k, "max_top_k": MAX_RETRIEVAL_TOP_K},
        )


if __name__ == "__main__":
    raise SystemExit(main())
