"""Run frozen domain contracts; live execution requires an explicit trusted factory."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from packages.eval.domain_dataset import (
    DomainDataset,
    DomainEvaluationError,
    DomainProfile,
    load_domain_dataset,
    load_domain_profile,
)
from packages.eval.domain_fixture import build_offline_binding
from packages.eval.domain_runner import (
    DomainApplicationBinding,
    DomainReport,
    compare_domain_baseline,
    evaluation_identity,
    load_domain_report,
    run_domain_evaluation,
)

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "evaluation/datasets/it_ops_v1"


@asynccontextmanager
async def _offline_application(dataset: DomainDataset) -> AsyncIterator[DomainApplicationBinding]:
    yield build_offline_binding(dataset)


async def _run(
    *,
    dataset: DomainDataset,
    profile: DomainProfile,
    profile_sha256: str,
    factory: str | None,
) -> DomainReport:
    application: Any
    if profile.mode == "offline_contract":
        if factory is not None:
            raise DomainEvaluationError("domain_factory_requires_live_profile")
        application = _offline_application(dataset)
    else:
        if factory is None or factory.count(":") != 1:
            raise DomainEvaluationError("domain_live_factory_required")
        module_name, callable_name = factory.split(":")
        # This is an explicit local-code execution option, never a remote import,
        # automatic credential discovery, or a public/API-controlled parameter.
        function = getattr(importlib.import_module(module_name), callable_name)
        application = function(dataset=dataset, profile=profile)
    async with application as binding:
        if not isinstance(binding, DomainApplicationBinding):
            raise DomainEvaluationError("domain_factory_binding_invalid")
        return await run_domain_evaluation(
            dataset=dataset,
            profile=profile,
            profile_sha256=profile_sha256,
            binding=binding,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Frozen synthetic IT ops regression gate. Default mode is offline_contract; "
            "no real retrieval/LLM quality claim is made."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--profile", type=Path, help="Explicit operator-owned profile for live runs"
    )
    parser.add_argument(
        "--factory", help="Trusted module:async_context_manager_factory (live only)"
    )
    parser.add_argument(
        "--baseline", type=Path, help="Reviewed compatible safe report; never rewritten"
    )
    parser.add_argument("--output", type=Path, help="New safe report file (exclusive creation)")
    args = parser.parse_args(argv)
    try:
        dataset = load_domain_dataset(args.dataset)
        profile_path = args.profile or (args.dataset / "offline_profile.json")
        profile, profile_sha256 = load_domain_profile(profile_path)
        baseline_path = args.baseline
        if baseline_path is None and profile.mode == "offline_contract":
            baseline_path = args.dataset / "baseline.json"
        baseline = load_domain_report(baseline_path) if baseline_path else None
        if baseline is not None and baseline.identity != evaluation_identity(
            dataset,
            profile,
            profile_sha256,
        ):
            raise DomainEvaluationError("domain_baseline_identity_mismatch")
        if args.output is not None:
            protected = {
                args.dataset.resolve() / filename
                for filename in (
                    "manifest.json",
                    "cases.json",
                    "corpus.json",
                    "offline_profile.json",
                    "baseline.json",
                )
            }
            protected.add(profile_path.resolve())
            if baseline_path:
                protected.add(baseline_path.resolve())
            if args.output.resolve() in protected or args.output.exists():
                raise DomainEvaluationError("domain_output_must_be_new_file")
        report = asyncio.run(
            _run(
                dataset=dataset,
                profile=profile,
                profile_sha256=profile_sha256,
                factory=args.factory,
            )
        )
        gate = (
            compare_domain_baseline(report=report, baseline=baseline, profile=profile)
            if baseline
            else None
        )
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(report.model_dump_json(indent=2) + "\n")
        decision = gate.decision if gate else "unbaselined"
        _emit(
            {
                "mode": profile.mode,
                "decision": decision,
                "absolute_passed": report.absolute_passed,
                "case_count": report.case_count,
                "passed_count": report.passed_count,
                "identity": report.identity.model_dump(mode="json"),
                "aggregate_scores": report.aggregate_scores,
                "regression": gate.model_dump(mode="json") if gate else None,
                "report_file": args.output.name if args.output else None,
            }
        )
        if gate is None:
            return 4
        return 0 if gate.decision == "pass" else (2 if gate.decision == "incomparable" else 1)
    except DomainEvaluationError as exc:
        _emit({"error_code": str(exc)})
        return 2
    except Exception:
        # Imported factory/provider errors can contain private configuration/text.
        _emit({"error_code": "domain_runner_failed"})
        return 3


def _emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
