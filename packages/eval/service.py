from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from packages.auth.policies import has_eval_evidence_read_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.eval.dto import (
    EvalCaseEvidence,
    EvalEvidenceFailureStage,
    EvalEvidenceGateMetric,
    EvalEvidenceGenerationSummary,
    EvalEvidenceReportListResponse,
    EvalEvidenceReportSummary,
    EvalEvidenceReportType,
    EvalEvidenceResolveResponse,
)
from packages.eval.exceptions import (
    EVAL_EVIDENCE_FORBIDDEN,
    EVAL_EVIDENCE_INVALID_REPORT_FILENAME,
    EVAL_EVIDENCE_NOT_FOUND,
    EVAL_EVIDENCE_PARSE_FAILED,
    EVAL_EVIDENCE_STORAGE_READ_FAILED,
    EvalEvidenceError,
)

_SAFE_REPORT_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.json$")
_UNSAFE_TEXT = re.compile(
    r"(?:"
    r"\b(?:bearer|token|access[_-]?token|api[_-]?key|secret|password|credential)\b"
    r"|provider[_-]?raw"
    r"|raw[_-]?exception"
    r"|\bsk-[A-Za-z0-9_-]{8,}\b"
    r"|[A-Za-z]:[\\/]"
    r"|\\\\"
    r"|/"
    r"|\b(?:file|s3|minio|https?)://"
    r")",
    re.IGNORECASE,
)
_NEXT_STEPS = (
    ".venv\\Scripts\\python.exe -m pytest tests/eval -q",
    ".venv\\Scripts\\python.exe -m pytest tests/unit/eval_evidence "
    "tests/integration/api/test_eval_evidence_routes.py -q",
    ".venv\\Scripts\\python.exe -m pytest tests/unit/web/test_governance_static_contract.py "
    "tests/unit/web/test_sidecar_static_contract.py -q",
    "node tests/unit/web/sidecar_behavior_runner.js",
)


class EvalEvidenceService:
    def __init__(
        self,
        *,
        report_dir: Path | str = Path("tests/eval/reports"),
        audit: AuditPort | None = None,
    ) -> None:
        self._report_dir = Path(report_dir)
        self._audit = audit

    async def list_reports(
        self,
        *,
        context: AuthenticatedRequestContext,
        limit: int = 20,
    ) -> EvalEvidenceReportListResponse:
        started = time.perf_counter()
        action = "eval_evidence.list_reports"
        try:
            _assert_permission(context)
        except EvalEvidenceError as exc:
            await self._record_audit(
                context=context,
                action=action,
                started=started,
                status=AuditStatus.DENIED,
                report_filename=None,
                report_type=None,
                item_count=0,
                error_code=exc.code,
            )
            raise
        if not self._report_dir.exists():
            await self._record_audit(
                context=context,
                action=action,
                started=started,
                status=AuditStatus.SUCCESS,
                report_filename=None,
                report_type=None,
                item_count=0,
                error_code=None,
            )
            return EvalEvidenceReportListResponse(items=(), next_steps=_NEXT_STEPS)
        safe_limit = min(max(limit, 1), 100)
        try:
            candidates = [
                path
                for path in self._report_dir.iterdir()
                if path.is_file() and path.suffix.lower() == ".json"
            ]
        except OSError as exc:
            error = EvalEvidenceError(
                code=EVAL_EVIDENCE_STORAGE_READ_FAILED,
                message="Eval evidence storage read failed.",
                details=_safe_error_details(context=context, stage="storage"),
                status_code=503,
            )
            await self._record_audit(
                context=context,
                action=action,
                started=started,
                status=AuditStatus.FAILURE,
                report_filename=None,
                report_type=None,
                item_count=0,
                error_code=error.code,
            )
            raise error from exc

        summaries: list[EvalEvidenceReportSummary] = []
        for path in candidates:
            if not _is_safe_report_filename(path.name):
                continue
            try:
                payload = self._load_payload(path)
                summaries.append(_summary_from_payload(filename=path.name, payload=payload))
            except EvalEvidenceError:
                continue
        summaries.sort(key=lambda item: _generated_at_sort_key(item.generated_at), reverse=True)
        await self._record_audit(
            context=context,
            action=action,
            started=started,
            status=AuditStatus.SUCCESS,
            report_filename=None,
            report_type=None,
            item_count=len(summaries[:safe_limit]),
            error_code=None,
        )
        return EvalEvidenceReportListResponse(
            items=tuple(summaries[:safe_limit]),
            next_steps=_NEXT_STEPS,
        )

    async def resolve_report(
        self,
        *,
        context: AuthenticatedRequestContext,
        report_filename: str,
    ) -> EvalEvidenceResolveResponse:
        started = time.perf_counter()
        action = "eval_evidence.resolve_report"
        filename: str | None = None
        try:
            _assert_permission(context)
            filename = _normalize_report_filename(report_filename)
            path = self._report_path(filename)
            payload = self._load_payload(path)
            result = _resolve_payload(filename=filename, payload=payload)
            await self._record_audit(
                context=context,
                action=action,
                started=started,
                status=AuditStatus.SUCCESS,
                report_filename=filename,
                report_type=result.summary.report_type.value,
                item_count=len(result.failed_cases),
                error_code=None,
            )
            return result
        except EvalEvidenceError as exc:
            await self._record_audit(
                context=context,
                action=action,
                started=started,
                status=(
                    AuditStatus.DENIED
                    if exc.code == EVAL_EVIDENCE_FORBIDDEN
                    else AuditStatus.FAILURE
                ),
                report_filename=filename,
                report_type=None,
                item_count=0,
                error_code=exc.code,
            )
            raise
        except ValidationError as exc:
            error = _parse_failed_error()
            await self._record_audit(
                context=context,
                action=action,
                started=started,
                status=AuditStatus.FAILURE,
                report_filename=filename,
                report_type=None,
                item_count=0,
                error_code=error.code,
            )
            raise error from exc

    def _report_path(self, filename: str) -> Path:
        base = self._report_dir.resolve()
        path = (base / filename).resolve()
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise EvalEvidenceError(
                code=EVAL_EVIDENCE_INVALID_REPORT_FILENAME,
                message="Eval evidence report filename is invalid.",
                details={
                    "failure_stage": "validation",
                    "error_code": EVAL_EVIDENCE_INVALID_REPORT_FILENAME,
                },
                status_code=400,
            ) from exc
        return path

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        action: str,
        started: float,
        status: AuditStatus,
        report_filename: str | None,
        report_type: str | None,
        item_count: int,
        error_code: str | None,
    ) -> None:
        if self._audit is None:
            return
        metadata: dict[str, object] = {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "action": action,
            "report_filename": report_filename,
            "report_type": report_type,
            "item_count": item_count,
            "error_code": error_code,
        }
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action=action,
                resource=AuditResource(
                    type="eval_report",
                    id=report_filename or "eval_reports",
                    metadata=metadata,
                ),
                status=status,
                latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
                error_code=error_code,
                metadata=metadata,
                created_at=datetime.now(tz=UTC),
            )
        )

    def _load_payload(self, path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            raise EvalEvidenceError(
                code=EVAL_EVIDENCE_NOT_FOUND,
                message="Eval evidence report was not found.",
                details={"failure_stage": "storage", "error_code": EVAL_EVIDENCE_NOT_FOUND},
                status_code=404,
            )
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise EvalEvidenceError(
                code=EVAL_EVIDENCE_STORAGE_READ_FAILED,
                message="Eval evidence storage read failed.",
                details={
                    "failure_stage": "storage",
                    "error_code": EVAL_EVIDENCE_STORAGE_READ_FAILED,
                    "report_filename": path.name,
                },
                status_code=503,
            ) from exc
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise EvalEvidenceError(
                code=EVAL_EVIDENCE_PARSE_FAILED,
                message="Eval evidence report parse failed.",
                details={
                    "failure_stage": "parse",
                    "error_code": EVAL_EVIDENCE_PARSE_FAILED,
                    "report_filename": path.name,
                },
                status_code=422,
            ) from exc
        if not isinstance(payload, dict):
            raise EvalEvidenceError(
                code=EVAL_EVIDENCE_PARSE_FAILED,
                message="Eval evidence report parse failed.",
                details={
                    "failure_stage": "parse",
                    "error_code": EVAL_EVIDENCE_PARSE_FAILED,
                    "report_filename": path.name,
                },
                status_code=422,
            )
        return payload


def _assert_permission(context: AuthenticatedRequestContext) -> None:
    if has_eval_evidence_read_permission(context.auth):
        return
    raise EvalEvidenceError(
        code=EVAL_EVIDENCE_FORBIDDEN,
        message="Eval evidence permission is required.",
        details=_safe_error_details(context=context, stage="permission"),
        status_code=403,
    )


def _safe_error_details(
    *,
    context: AuthenticatedRequestContext,
    stage: str,
) -> dict[str, object]:
    return {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "failure_stage": stage,
        "error_code": EVAL_EVIDENCE_FORBIDDEN if stage == "permission" else stage,
    }


def _normalize_report_filename(value: str) -> str:
    normalized = value.strip()
    if not _is_safe_report_filename(normalized):
        raise EvalEvidenceError(
            code=EVAL_EVIDENCE_INVALID_REPORT_FILENAME,
            message="Eval evidence report filename is invalid.",
            details={
                "failure_stage": "validation",
                "error_code": EVAL_EVIDENCE_INVALID_REPORT_FILENAME,
            },
            status_code=400,
        )
    return normalized


def _is_safe_report_filename(value: str) -> bool:
    return (
        bool(_SAFE_REPORT_FILENAME.fullmatch(value))
        and ".." not in value
        and "/" not in value
        and "\\" not in value
        and ":" not in value
    )


def _resolve_payload(*, filename: str, payload: Mapping[str, Any]) -> EvalEvidenceResolveResponse:
    summary = _summary_from_payload(filename=filename, payload=payload)
    report_type = summary.report_type
    if report_type == EvalEvidenceReportType.RAG_QUALITY_RUNNER:
        failed_cases = tuple(
            _case_evidence(case)
            for case in _sequence(payload.get("cases"))
            if isinstance(case, Mapping) and case.get("passed") is False
        )
        return EvalEvidenceResolveResponse(
            summary=summary,
            failed_cases=failed_cases,
            next_steps=_NEXT_STEPS,
        )
    if report_type == EvalEvidenceReportType.RAG_CI_SMOKE_GATE:
        decision = _mapping(payload.get("decision"))
        return EvalEvidenceResolveResponse(
            summary=summary,
            failed_cases=_gate_failed_cases(decision),
            gate_metrics=tuple(
                _gate_metric(metric) for metric in _sequence(decision.get("metrics"))
            ),
            next_steps=_NEXT_STEPS,
        )
    return EvalEvidenceResolveResponse(summary=summary, next_steps=_NEXT_STEPS)


def _summary_from_payload(
    *,
    filename: str,
    payload: Mapping[str, Any],
) -> EvalEvidenceReportSummary:
    report_type = _report_type(payload.get("report_type"))
    if report_type == EvalEvidenceReportType.RAG_DATASET_SMOKE:
        summary = _mapping(payload.get("summary"))
        return _validate_summary(
            EvalEvidenceReportSummary(
                report_filename=filename,
                generated_at=_safe_optional_text(payload.get("generated_at")),
                report_type=report_type,
                dataset_version=_safe_optional_text(summary.get("dataset_version")),
                case_count=_safe_int(summary.get("case_count")),
                passed_count=_safe_int(summary.get("case_count")),
                failed_count=0,
                acl_isolation=None,
                prompt_injection=None,
                decision="dataset",
                failure_stages=_failure_stages(summary.get("failure_stages")),
            )
        )
    if report_type == EvalEvidenceReportType.RAG_QUALITY_RUNNER:
        summary = _mapping(payload.get("summary"))
        return _quality_summary(
            filename=filename,
            generated_at=payload.get("generated_at"),
            summary=summary,
        )
    if report_type == EvalEvidenceReportType.RAG_CI_SMOKE_GATE:
        runner_summary = _mapping(payload.get("runner_summary"))
        decision = _mapping(payload.get("decision"))
        dataset = _mapping(payload.get("dataset"))
        base = _quality_summary(
            filename=filename,
            generated_at=payload.get("generated_at"),
            summary=runner_summary,
        )
        return base.model_copy(
            update={
                "report_type": report_type,
                "dataset_name": _safe_optional_text(dataset.get("name")),
                "decision": "passed" if decision.get("passed") is True else "failed",
                "failed_metric_names": _safe_text_tuple(decision.get("failed_metric_names")),
                "failure_stages": _failure_stages(
                    decision.get("failure_stages") or payload.get("failure_stages")
                ),
            }
        )
    return EvalEvidenceReportSummary(
        report_filename=filename,
        generated_at=_safe_optional_text(payload.get("generated_at")),
        report_type=EvalEvidenceReportType.UNKNOWN,
        decision="unsupported",
    )


def _quality_summary(
    *,
    filename: str,
    generated_at: object,
    summary: Mapping[str, Any],
) -> EvalEvidenceReportSummary:
    case_count = _required_int(summary, "case_count")
    passed_count = _required_int(summary, "passed_count")
    failed_count = _required_int(summary, "failed_count")
    return _validate_summary(
        EvalEvidenceReportSummary(
            report_filename=filename,
            generated_at=_safe_optional_text(generated_at),
            report_type=EvalEvidenceReportType.RAG_QUALITY_RUNNER,
            case_count=case_count,
            passed_count=passed_count,
            failed_count=failed_count,
            retrieval_hit_rate=_safe_float(summary.get("retrieval_hit_rate")),
            citation_coverage=_safe_float(summary.get("citation_coverage")),
            no_answer_correctness=_safe_float(summary.get("no_answer_correctness")),
            acl_isolation=_safe_bool(summary.get("acl_isolation_passed")),
            prompt_injection=_safe_bool(summary.get("prompt_injection_passed")),
            average_latency_ms=_safe_float(summary.get("average_latency_ms")),
            decision="passed" if failed_count == 0 else "failed",
        )
    )


def _validate_summary(summary: EvalEvidenceReportSummary) -> EvalEvidenceReportSummary:
    try:
        return EvalEvidenceReportSummary.model_validate(summary)
    except ValidationError as exc:
        raise EvalEvidenceError(
            code=EVAL_EVIDENCE_PARSE_FAILED,
            message="Eval evidence report parse failed.",
            details={"failure_stage": "parse", "error_code": EVAL_EVIDENCE_PARSE_FAILED},
            status_code=422,
        ) from exc


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _parse_failed_error()
    return max(value, 0)


def _parse_failed_error() -> EvalEvidenceError:
    return EvalEvidenceError(
        code=EVAL_EVIDENCE_PARSE_FAILED,
        message="Eval evidence report parse failed.",
        details={"failure_stage": "parse", "error_code": EVAL_EVIDENCE_PARSE_FAILED},
        status_code=422,
    )


def _case_evidence(payload: Mapping[str, Any]) -> EvalCaseEvidence:
    return EvalCaseEvidence(
        case_id=_safe_text(payload.get("case_id")),
        failure_stage=_failure_stage(payload.get("failure_stage")),
        matched_documents=_safe_text_tuple(payload.get("matched_documents")),
        matched_chunks=_safe_text_tuple(payload.get("matched_chunks")),
        matched_citations=_safe_text_tuple(payload.get("matched_citations")),
        retrieval_result_count=_safe_int(payload.get("retrieval_result_count")),
        context_item_count=_safe_int(payload.get("context_item_count")),
        citation_count=_safe_int(payload.get("citation_count")),
        unsupported_count=_safe_int(payload.get("unsupported_count")),
        forged_reference_count=_safe_int(payload.get("forged_reference_count")),
        prompt_risk_count=_safe_int(payload.get("prompt_risk_count")),
        request_id=_safe_optional_text(payload.get("request_id")),
        trace_id=_safe_optional_text(payload.get("trace_id")),
        top_k=_safe_optional_int(payload.get("top_k")),
        latency_ms=_safe_float(payload.get("latency_ms")),
        generation=_generation_summary(_mapping(payload.get("generation"))),
    )


def _gate_failed_cases(decision: Mapping[str, Any]) -> tuple[EvalCaseEvidence, ...]:
    case_ids = _safe_text_tuple(decision.get("failed_case_ids"))
    stages = _failure_stages(decision.get("failure_stages"))
    default_stage = stages[0] if stages else EvalEvidenceFailureStage.UNKNOWN
    return tuple(
        EvalCaseEvidence(case_id=case_id, failure_stage=default_stage)
        for case_id in case_ids
    )


def _gate_metric(payload: object) -> EvalEvidenceGateMetric:
    metric = _mapping(payload)
    return EvalEvidenceGateMetric(
        metric=_safe_text(metric.get("metric")),
        threshold_name=_safe_text(metric.get("threshold_name")),
        passed=metric.get("passed") is True,
        expected=_safe_scalar_metric(metric.get("expected")),
        actual=_safe_scalar_metric(metric.get("actual")),
    )


def _generation_summary(payload: Mapping[str, Any]) -> EvalEvidenceGenerationSummary:
    return EvalEvidenceGenerationSummary(
        provider=_safe_optional_text(payload.get("provider")),
        model=_safe_optional_text(payload.get("model")),
        version=_safe_optional_text(payload.get("version")),
        finish_reason=_safe_optional_text(payload.get("finish_reason")),
        error_code=_safe_optional_text(payload.get("error_code")),
        token_usage=_safe_token_usage(payload.get("token_usage")),
    )


def _report_type(value: object) -> EvalEvidenceReportType:
    if isinstance(value, str):
        try:
            return EvalEvidenceReportType(value.strip())
        except ValueError:
            return EvalEvidenceReportType.UNKNOWN
    return EvalEvidenceReportType.UNKNOWN


def _failure_stage(value: object) -> EvalEvidenceFailureStage | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return EvalEvidenceFailureStage(value.strip())
        except ValueError:
            return EvalEvidenceFailureStage.UNKNOWN
    return EvalEvidenceFailureStage.UNKNOWN


def _failure_stages(value: object) -> tuple[EvalEvidenceFailureStage, ...]:
    stages = []
    for item in _sequence(value):
        stage = _failure_stage(item)
        if stage is not None and stage not in stages:
            stages.append(stage)
    return tuple(stages)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, list | tuple) else ()


def _safe_text_tuple(value: object) -> tuple[str, ...]:
    return tuple(_safe_text(item) for item in _sequence(value) if _safe_text(item))


def _safe_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if _UNSAFE_TEXT.search(normalized):
        return ""
    if len(normalized) > 200:
        return normalized[:200]
    return normalized


def _safe_optional_text(value: object) -> str | None:
    text = _safe_text(value)
    return text or None


def _safe_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _safe_optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(value, 0)


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return None
    return result


def _safe_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_token_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = ("input_tokens", "output_tokens", "total_tokens")
    safe = {
        key: item
        for key in allowed
        if (item := value.get(key)) is not None
        and not isinstance(item, bool)
        and isinstance(item, int)
        and item >= 0
    }
    return safe or None


def _safe_scalar_metric(value: object) -> float | int | bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        if not math.isfinite(float(value)):
            return 0
        return value
    return 0


def _generated_at_sort_key(value: str | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
