"""Ordinary domain regression contracts; no real network/provider/database calls."""

from __future__ import annotations

import json
import socket
import sys
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from packages.eval.domain_cli import main
from packages.eval.domain_dataset import (
    METRICS,
    DomainDataset,
    DomainEvaluationError,
    load_domain_dataset,
    sha256_bytes,
)
from packages.eval.domain_fixture import (
    ExtractiveFixtureProvider,
    FixtureLookupRetrieval,
    build_offline_binding,
)
from packages.eval.domain_ragas import evaluate_domain_captures_with_ragas
from packages.eval.domain_runner import (
    DomainReport,
    compare_domain_baseline,
    evaluation_identity,
    load_domain_report,
    run_domain_evaluation,
)
from packages.rag.capture import CapturedQuery

DATASET = Path(__file__).resolve().parents[3] / "evaluation/datasets/it_ops_v1"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        attempts.append("network")
        raise AssertionError("Domain contracts must never use real network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    yield
    assert attempts == []


def test_frozen_synthetic_inputs_and_measured_baseline() -> None:
    dataset = load_domain_dataset(DATASET)
    assert dataset.manifest.dataset_version == "v1"
    assert len(dataset.corpus) == 12
    assert len(dataset.cases) == 16
    assert sum(case.expected_no_answer for case in dataset.cases) == 2
    assert dataset.manifest.files["corpus.json"] == (
        "2b9009d630708acf9cdab98f7142ba0746e9a21eabe03685516dd82beddd2bba"
    )
    assert dataset.manifest.files["cases.json"] == (
        "6fa2247886d63a785bec27b8e2f438b6204ed60380eb1ee3aa2efd8adfb43878"
    )
    baseline = load_domain_report(DATASET / "baseline.json")
    assert baseline.identity.mode == "offline_contract"
    assert baseline.passed_count == baseline.case_count == 16
    assert baseline.aggregate_scores == dict.fromkeys(METRICS, 1.0)


async def _run(dataset: DomainDataset, **kwargs: Any) -> DomainReport:
    return await run_domain_evaluation(
        dataset=dataset,
        profile=dataset.offline_profile,
        profile_sha256=dataset.manifest.files["offline_profile.json"],
        binding=kwargs.pop("binding", build_offline_binding(dataset)),
        **kwargs,
    )


async def test_same_generation_domain_chain_and_safe_report() -> None:
    dataset = load_domain_dataset(DATASET)
    binding = build_offline_binding(dataset)
    captures: dict[str, CapturedQuery] = {}
    report = await _run(dataset, binding=binding, capture_consumer=captures.__setitem__)
    assert report.passed_count == 16
    retrieval = cast(FixtureLookupRetrieval, binding.service._retrieval_service)
    provider = cast(ExtractiveFixtureProvider, binding.service._generation_service._provider)
    assert retrieval.request_count == 16
    assert provider.generation_count == 14
    assert len(captures["itops-13"].contexts) == 2
    assert captures["itops-15"].contexts == ()
    for case in dataset.cases:
        captured = captures[case.case_id]
        if not captured.response.no_answer:
            assert captured.response.answer == "\n".join(item.content for item in captured.contexts)
    safe_json = report.model_dump_json()
    for case in dataset.cases:
        assert case.question not in safe_json
        assert case.reference_answer not in safe_json
    for chunk in dataset.corpus:
        assert chunk.content not in safe_json
    assert "retrieved_contexts" not in safe_json
    assert (
        compare_domain_baseline(
            report=report,
            baseline=load_domain_report(DATASET / "baseline.json"),
            profile=dataset.offline_profile,
        ).decision
        == "pass"
    )


async def test_fake_generation_does_not_read_expected_answers_or_terms() -> None:
    dataset = load_domain_dataset(DATASET)
    original = await _run(dataset)
    changed_case = dataset.cases[0].model_copy(
        update={
            "reference_answer": "An independently changed reference is never a provider response.",
            "required_terms": (("a missing ordinary phrase",),),
        }
    )
    changed = dataset.model_copy(update={"cases": (changed_case, *dataset.cases[1:])})
    observed = await _run(changed)
    assert observed.cases[0].answer_sha256 == original.cases[0].answer_sha256
    assert observed.cases[0].answer_required_terms == 0.0
    assert observed.passed_count == 15
    gate = compare_domain_baseline(
        report=observed,
        baseline=original,
        profile=dataset.offline_profile,
    )
    assert gate.decision == "fail"
    assert gate.failed_case_ids == ("itops-01",)
    assert (
        next(metric for metric in gate.metrics if metric.metric == "answer_required_terms").delta
        < 0
    )


async def test_query_failure_remains_in_denominators_and_hides_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = load_domain_dataset(DATASET)
    binding = build_offline_binding(dataset)
    original_query = binding.service.query_with_capture
    calls = 0

    async def ordinary_failure(**kwargs: Any) -> CapturedQuery:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private-provider-body-and-configuration")
        return await original_query(**kwargs)

    monkeypatch.setattr(binding.service, "query_with_capture", ordinary_failure)
    report = await _run(dataset, binding=binding)
    assert report.case_count == 16 and report.passed_count == 15
    assert report.aggregate_scores["no_answer_correctness"] == 15 / 16
    assert report.aggregate_scores["answer_required_terms"] == 13 / 14
    assert report.aggregate_scores["citation_coverage"] == 13 / 14
    assert report.cases[0].error_code == "query_failed"
    assert "private-provider" not in report.model_dump_json()


@pytest.mark.parametrize(
    "field,value",
    [
        ("dataset_version", "v2"),
        ("corpus_sha256", "a" * 64),
        ("configuration_sha256", "b" * 64),
        ("profile_version", "v2"),
        ("mode", "live_application"),
    ],
)
def test_unlike_domain_runs_are_incomparable(field: str, value: str) -> None:
    dataset = load_domain_dataset(DATASET)
    baseline = load_domain_report(DATASET / "baseline.json")
    report = baseline.model_copy(
        update={
            "identity": baseline.identity.model_copy(update={field: value}),
        }
    )
    assert (
        compare_domain_baseline(
            report=report,
            baseline=baseline,
            profile=dataset.offline_profile,
        ).decision
        == "incomparable"
    )


def test_gate_policy_cannot_be_weakened_after_run() -> None:
    dataset = load_domain_dataset(DATASET)
    baseline = load_domain_report(DATASET / "baseline.json")
    weaker = dataset.offline_profile.model_copy(
        update={"minimum_scores": dict.fromkeys(METRICS, 0.0)}
    )
    assert compare_domain_baseline(report=baseline, baseline=baseline, profile=weaker).decision == (
        "incomparable"
    )


def test_per_case_regression_cannot_be_hidden_by_an_aggregate_improvement() -> None:
    dataset = load_domain_dataset(DATASET)
    baseline = load_domain_report(DATASET / "baseline.json")
    profile = dataset.offline_profile.model_copy(
        update={"minimum_scores": dict.fromkeys(METRICS, 0.0)}
    )
    identity = evaluation_identity(dataset, profile, "c" * 64)
    prior_cases = list(baseline.cases)
    prior_cases[1] = prior_cases[1].model_copy(update={"answer_required_terms": 0.0})
    current_cases = list(baseline.cases)
    current_cases[0] = current_cases[0].model_copy(update={"answer_required_terms": 0.0})
    scores = dict(baseline.aggregate_scores, answer_required_terms=13 / 14)
    previous = baseline.model_copy(
        update={
            "identity": identity,
            "cases": tuple(prior_cases),
            "aggregate_scores": scores,
        }
    )
    current = baseline.model_copy(
        update={
            "identity": identity,
            "cases": tuple(current_cases),
            "aggregate_scores": scores,
        }
    )
    gate = compare_domain_baseline(report=current, baseline=previous, profile=profile)
    assert gate.decision == "fail" and gate.failed_case_ids == ("itops-01",)
    assert all(metric.delta == 0.0 for metric in gate.metrics)


def test_dataset_bytes_are_locked_without_rewriting_expectations(tmp_path: Path) -> None:
    for filename in ("manifest.json", "corpus.json", "cases.json", "offline_profile.json"):
        (tmp_path / filename).write_bytes((DATASET / filename).read_bytes())
    (tmp_path / "cases.json").write_text((tmp_path / "cases.json").read_text() + "\n")
    with pytest.raises(DomainEvaluationError, match="^domain_input_hash_mismatch$"):
        load_domain_dataset(tmp_path)


def test_cli_default_runs_checked_baseline_and_only_writes_safe_new_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "safe-report.json"
    assert main(["--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "offline_contract" and summary["decision"] == "pass"
    assert load_domain_report(output).case_count == 16
    assert main(["--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "domain_output_must_be_new_file"


def test_cli_never_overwrites_frozen_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    baseline_path = DATASET / "baseline.json"
    before = sha256_bytes(baseline_path.read_bytes())
    assert main(["--output", str(baseline_path)]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "domain_output_must_be_new_file"
    assert before == sha256_bytes(baseline_path.read_bytes())


def test_cli_trusted_factory_uses_query_capture_without_claiming_baselined_quality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = load_domain_dataset(DATASET)
    profile = dataset.offline_profile.model_copy(update={"mode": "live_application"})
    profile_path = tmp_path / "operator-profile.json"
    profile_path.write_text(profile.model_dump_json())
    entered: list[str] = []
    closed: list[str] = []

    @asynccontextmanager
    async def trusted_application(**kwargs: Any) -> Any:
        assert kwargs["profile"] == profile
        entered.append("entered")
        try:
            # Synthetic transport verifies factory mechanics only; no real live run.
            yield replace(build_offline_binding(kwargs["dataset"]), mode="live_application")
        finally:
            closed.append("closed")

    module = ModuleType("test_trusted_domain_factory")
    module.compose = trusted_application  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    output = tmp_path / "candidate.json"
    assert (
        main(
            [
                "--profile",
                str(profile_path),
                "--factory",
                module.__name__ + ":compose",
                "--output",
                str(output),
            ]
        )
        == 4
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["decision"] == "unbaselined"
    assert entered == ["entered"] and closed == ["closed"]
    assert load_domain_report(output).identity.mode == "live_application"


async def test_ragas_adapter_reuses_existing_captures_and_returns_no_raw_text() -> None:
    from packages.eval.ragas_evaluator import CaseResult, EvalReport, MetricScore, RagasEvaluator

    dataset = load_domain_dataset(DATASET)
    captures: dict[str, CapturedQuery] = {}
    await _run(dataset, capture_consumer=captures.__setitem__)
    calls: list[str] = []

    class StubJudgeAdapter:
        async def evaluate(self, **kwargs: Any) -> EvalReport:
            results: list[CaseResult] = []
            for case in kwargs["cases"]:
                answer, contexts = await kwargs["run_fn"](case.question)
                calls.append(case.case_id)
                assert (answer, contexts) == captures[case.case_id].ragas_result()
                results.append(
                    CaseResult(
                        case, (MetricScore("faithfulness", 1.0),), answer, contexts, 0.0, True
                    )
                )
            return EvalReport(
                "synthetic",
                "synthetic",
                kwargs["dataset_name"],
                kwargs["dataset_version"],
                16,
                16,
                0,
                tuple(results),
                {"faithfulness": 1.0},
                0.0,
                (),
            )

    safe = await evaluate_domain_captures_with_ragas(
        dataset=dataset,
        captures=captures,
        evaluator=cast(RagasEvaluator, StubJudgeAdapter()),
    )
    assert calls == [case.case_id for case in dataset.cases]
    assert safe.aggregate_scores == {"faithfulness": 1.0}
    serialized = safe.model_dump_json()
    assert dataset.corpus[0].content not in serialized
    assert dataset.cases[0].question not in serialized


def test_retired_script_import_has_no_provider_or_credential_side_effects(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from evaluation import eval_ragas_v2

    assert eval_ragas_v2.main() == 2
    assert "retired" in capsys.readouterr().err
    assert not hasattr(eval_ragas_v2, "DS_KEY")


async def test_frozen_expectations_detect_ordinary_response_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = load_domain_dataset(DATASET)
    binding = build_offline_binding(dataset)
    original_query = binding.service.query_with_capture
    calls = 0

    async def incomplete_response(**kwargs: Any) -> CapturedQuery:
        nonlocal calls
        captured = await original_query(**kwargs)
        calls += 1
        if calls == 1:
            return captured.model_copy(
                update={
                    "response": captured.response.model_copy(
                        update={
                            "answer": "The provided context is insufficient to answer.",
                            "no_answer": True,
                            "citations": (),
                        }
                    )
                }
            )
        return captured

    monkeypatch.setattr(binding.service, "query_with_capture", incomplete_response)
    report = await _run(dataset, binding=binding)
    case = report.cases[0]
    assert case.no_answer_correctness == case.citation_coverage == case.answer_required_terms == 0.0
    assert (
        compare_domain_baseline(
            report=report,
            baseline=load_domain_report(DATASET / "baseline.json"),
            profile=dataset.offline_profile,
        ).decision
        == "fail"
    )


def test_cli_incompatible_baseline_stops_before_factory_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = load_domain_dataset(DATASET)
    profile = dataset.offline_profile.model_copy(update={"mode": "live_application"})
    profile_path = tmp_path / "live-profile.json"
    profile_path.write_text(profile.model_dump_json())
    calls: list[str] = []

    def would_import(module_name: str) -> Any:
        calls.append(module_name)
        raise AssertionError("An incomparable baseline must not execute a factory")

    monkeypatch.setattr("packages.eval.domain_cli.importlib.import_module", would_import)
    assert (
        main(
            [
                "--profile",
                str(profile_path),
                "--baseline",
                str(DATASET / "baseline.json"),
                "--factory",
                "unused_live_factory:compose",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["error_code"] == "domain_baseline_identity_mismatch"
    assert calls == []
