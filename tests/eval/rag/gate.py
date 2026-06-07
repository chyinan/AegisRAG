from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from tests.eval.rag.dto import (
    SAFE_FIXTURE_ID_PATTERN,
    FailureStage,
    RagEvalReportSummary,
)


class RagEvalGateError(ValueError):
    def __init__(self, *, code: str, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(self.__str__())

    def __str__(self) -> str:
        safe_details = json.dumps(self.details, ensure_ascii=False, sort_keys=True)
        return f"{self.code}: {safe_details}"


class RagEvalGateThresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_retrieval_hit_rate: float
    min_citation_coverage: float
    min_no_answer_correctness: float
    require_acl_isolation_passed: bool
    require_prompt_injection_passed: bool
    max_failed_count: int

    @field_validator(
        "min_retrieval_hit_rate",
        "min_citation_coverage",
        "min_no_answer_correctness",
        mode="before",
    )
    @classmethod
    def _strict_rate(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("rate thresholds must be numbers")
        return value

    @field_validator(
        "min_retrieval_hit_rate",
        "min_citation_coverage",
        "min_no_answer_correctness",
    )
    @classmethod
    def _rate_range(cls, value: float) -> float:
        if value < 0.0 or value > 1.0:
            raise ValueError("rate thresholds must be between 0 and 1")
        return value

    @field_validator(
        "require_acl_isolation_passed",
        "require_prompt_injection_passed",
        mode="before",
    )
    @classmethod
    def _strict_bool(cls, value: object) -> object:
        if not isinstance(value, bool):
            raise ValueError("gate boolean thresholds must be booleans")
        return value

    @field_validator("max_failed_count", mode="before")
    @classmethod
    def _strict_count(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("max_failed_count must be an integer")
        return value

    @field_validator("max_failed_count")
    @classmethod
    def _count_range(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_failed_count must be non-negative")
        return value


class RagEvalGateConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_name: str
    config_id: str
    thresholds: RagEvalGateThresholds

    @field_validator("gate_name", "config_id")
    @classmethod
    def _safe_id(cls, value: str) -> str:
        normalized = value.strip()
        if not SAFE_FIXTURE_ID_PATTERN.fullmatch(normalized):
            raise ValueError("gate identifiers must be safe fixture ids")
        return normalized


class RagEvalGateMetricDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    threshold_name: str
    passed: bool
    expected: float | int | bool
    actual: float | int | bool


class RagEvalGateDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    metrics: tuple[RagEvalGateMetricDecision, ...]
    failed_metric_names: tuple[str, ...] = ()
    failed_case_ids: tuple[str, ...] = ()
    failure_stages: tuple[FailureStage, ...] = ()


class RagEvalGateDatasetSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    name: str


class RagEvalGateConfigSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_name: str
    config_id: str
    thresholds: RagEvalGateThresholds


class RagEvalGateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    generated_at: str
    report_type: Literal["rag_ci_smoke_gate"] = "rag_ci_smoke_gate"
    commit_sha: str
    branch: str
    dataset: RagEvalGateDatasetSummary
    config: RagEvalGateConfigSummary
    runner_summary: RagEvalReportSummary
    decision: RagEvalGateDecision
    failed_case_ids: tuple[str, ...] = ()
    failure_stages: tuple[FailureStage, ...] = ()


def load_rag_eval_gate_config(path: Path) -> RagEvalGateConfig:
    payload = _load_payload(path)
    if not payload:
        raise RagEvalGateError(code="invalid_gate_config", details={"file": path.name})
    try:
        return RagEvalGateConfig.model_validate(payload)
    except ValidationError as exc:
        raise RagEvalGateError(
            code="invalid_gate_config",
            details=_safe_validation_details(exc=exc, file=path.name),
        ) from exc


def decide_rag_eval_gate(
    *,
    summary: RagEvalReportSummary,
    failure_cases: tuple[tuple[str, FailureStage], ...],
    config: RagEvalGateConfig,
) -> RagEvalGateDecision:
    thresholds = config.thresholds
    metrics = (
        _metric(
            "retrieval_hit_rate",
            "min_retrieval_hit_rate",
            summary.retrieval_hit_rate >= thresholds.min_retrieval_hit_rate,
            thresholds.min_retrieval_hit_rate,
            summary.retrieval_hit_rate,
        ),
        _metric(
            "citation_coverage",
            "min_citation_coverage",
            summary.citation_coverage >= thresholds.min_citation_coverage,
            thresholds.min_citation_coverage,
            summary.citation_coverage,
        ),
        _metric(
            "no_answer_correctness",
            "min_no_answer_correctness",
            summary.no_answer_correctness >= thresholds.min_no_answer_correctness,
            thresholds.min_no_answer_correctness,
            summary.no_answer_correctness,
        ),
        _metric(
            "acl_isolation_passed",
            "require_acl_isolation_passed",
            (
                summary.acl_isolation_passed is True
                if thresholds.require_acl_isolation_passed
                else True
            ),
            thresholds.require_acl_isolation_passed,
            summary.acl_isolation_passed,
        ),
        _metric(
            "prompt_injection_passed",
            "require_prompt_injection_passed",
            (
                summary.prompt_injection_passed is True
                if thresholds.require_prompt_injection_passed
                else True
            ),
            thresholds.require_prompt_injection_passed,
            summary.prompt_injection_passed,
        ),
        _metric(
            "failed_count",
            "max_failed_count",
            summary.failed_count <= thresholds.max_failed_count,
            thresholds.max_failed_count,
            summary.failed_count,
        ),
    )
    failed_metric_names = tuple(metric.metric for metric in metrics if not metric.passed)
    return RagEvalGateDecision(
        passed=not failed_metric_names and not failure_cases,
        metrics=metrics,
        failed_metric_names=failed_metric_names,
        failed_case_ids=tuple(case_id for case_id, _stage in failure_cases),
        failure_stages=tuple(dict.fromkeys(stage for _case_id, stage in failure_cases)),
    )


def write_rag_eval_gate_report(
    *,
    runner_summary: RagEvalReportSummary,
    decision: RagEvalGateDecision,
    config: RagEvalGateConfig,
    dataset_path: Path,
    report_dir: Path | None = None,
    report_path: Path | None = None,
    commit_sha: str | None = None,
    branch: str | None = None,
) -> Path:
    report = RagEvalGateReport(
        generated_at=datetime.now(UTC).isoformat(),
        commit_sha=commit_sha or _git_value("sha"),
        branch=branch or _git_value("branch"),
        dataset=RagEvalGateDatasetSummary(
            path=_safe_path_summary(dataset_path),
            name=dataset_path.name,
        ),
        config=RagEvalGateConfigSummary(
            gate_name=config.gate_name,
            config_id=config.config_id,
            thresholds=config.thresholds,
        ),
        runner_summary=runner_summary,
        decision=decision,
        failed_case_ids=decision.failed_case_ids,
        failure_stages=decision.failure_stages,
    )
    if report_path is None:
        target_dir = report_dir or Path("tests/eval/reports")
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        report_path = target_dir / f"rag-ci-smoke-{stamp}-{uuid4().hex[:8]}.json"
    else:
        report_path.parent.mkdir(parents=True, exist_ok=True)

    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report_path


def _metric(
    metric: str,
    threshold_name: str,
    passed: bool,
    expected: float | int | bool,
    actual: float | int | bool,
) -> RagEvalGateMetricDecision:
    return RagEvalGateMetricDecision(
        metric=metric,
        threshold_name=threshold_name,
        passed=passed,
        expected=expected,
        actual=actual,
    )


def _load_payload(path: Path) -> dict[str, object]:
    if not path.exists():
        raise RagEvalGateError(code="file_not_found", details={"file": path.name})
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RagEvalGateError(code="read_failed", details={"file": path.name}) from exc
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RagEvalGateError(
            code="invalid_json",
            details={"file": path.name, "line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(payload, dict):
        raise RagEvalGateError(code="invalid_gate_config", details={"file": path.name})
    return payload


def _safe_validation_details(*, exc: ValidationError, file: str) -> dict[str, object]:
    first_error = exc.errors(include_url=False, include_context=False)[0]
    loc = first_error.get("loc", ())
    loc_items = tuple(str(item) for item in loc) if isinstance(loc, tuple) else (str(loc),)
    field = ".".join(item for item in loc_items if not item.isdigit()) or "model"
    return {"field": field, "error_count": exc.error_count(), "file": file}


def _safe_path_summary(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(Path.cwd().resolve())
        return relative.as_posix()
    except OSError:
        return path.name
    except ValueError:
        if path.is_absolute():
            return path.name
        return path.as_posix()


def _git_value(kind: Literal["sha", "branch"]) -> str:
    env_value = _git_env_value(kind)
    if env_value is not None:
        return env_value
    args = ("rev-parse", "--short", "HEAD") if kind == "sha" else ("branch", "--show-current")
    try:
        result = subprocess.run(
            ("git", *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else "unknown"


def _git_env_value(kind: Literal["sha", "branch"]) -> str | None:
    key = "GITHUB_SHA" if kind == "sha" else "GITHUB_REF_NAME"
    value = os.environ.get(key)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
