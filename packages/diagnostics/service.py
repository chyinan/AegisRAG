from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

from packages.auth.policies import has_diagnostics_read_permission
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.audit_repositories import AuditLogRecord
from packages.data.storage.exceptions import StorageError
from packages.diagnostics.dto import (
    DiagnosticsLookupRequest,
    DiagnosticsReport,
    DiagnosticsResolveResponse,
    DiagnosticsStageSummary,
    DiagnosticsSummary,
    FailureStage,
    StageStatus,
)
from packages.diagnostics.exceptions import (
    DIAGNOSTICS_FORBIDDEN,
    DIAGNOSTICS_NOT_FOUND,
    DIAGNOSTICS_STORAGE_READ_FAILED,
    DiagnosticsError,
)
from packages.retrieval.dto import RetrievalLogRecord


class RetrievalLogReader(Protocol):
    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> list[RetrievalLogRecord]:
        ...

    async def list_by_trace_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        limit: int = 100,
    ) -> list[RetrievalLogRecord]:
        ...


class AuditLogReader(Protocol):
    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        ...

    async def list_by_trace_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        ...


class DiagnosticsService:
    def __init__(
        self,
        *,
        retrieval_logs: RetrievalLogReader,
        audit_logs: AuditLogReader,
        generated_at: Callable[[], str] | None = None,
    ) -> None:
        self._retrieval_logs = retrieval_logs
        self._audit_logs = audit_logs
        self._generated_at = generated_at or _utc_now_iso

    async def resolve(
        self,
        *,
        context: AuthenticatedRequestContext,
        lookup: DiagnosticsLookupRequest,
    ) -> DiagnosticsResolveResponse:
        if not has_diagnostics_read_permission(context.auth):
            raise DiagnosticsError(
                code=DIAGNOSTICS_FORBIDDEN,
                message="Diagnostics permission is required.",
                details={"permission": "audit:read"},
                status_code=403,
            )

        try:
            retrieval_records, audit_records = await self._read_records(
                tenant_id=context.auth.tenant_id,
                lookup=lookup,
            )
        except StorageError as exc:
            raise DiagnosticsError(
                code=DIAGNOSTICS_STORAGE_READ_FAILED,
                message="Diagnostics storage read failed.",
                details=_safe_storage_details(lookup=lookup),
                status_code=503,
            ) from exc

        if not retrieval_records and not audit_records:
            raise DiagnosticsError(
                code=DIAGNOSTICS_NOT_FOUND,
                message="Diagnostics records were not found.",
                details=_safe_storage_details(lookup=lookup),
                status_code=404,
            )

        summary = _build_summary(
            tenant_id=context.auth.tenant_id,
            lookup=lookup,
            retrieval_records=retrieval_records,
            audit_records=audit_records,
        )
        stages = _build_stages(retrieval_records=retrieval_records, audit_records=audit_records)
        next_steps = _next_steps(summary.failure_stage)
        report = None
        if lookup.include_report:
            report = DiagnosticsReport(
                lookup=lookup,
                summary=summary,
                stages=stages,
                next_steps=next_steps,
                generated_at=self._generated_at(),
            )
        return DiagnosticsResolveResponse(
            lookup=lookup,
            summary=summary,
            stages=stages,
            next_steps=next_steps,
            report=report,
        )

    async def _read_records(
        self,
        *,
        tenant_id: str,
        lookup: DiagnosticsLookupRequest,
    ) -> tuple[list[RetrievalLogRecord], list[AuditLogRecord]]:
        if lookup.request_id is not None:
            retrieval_records = await self._retrieval_logs.list_by_request_id(
                tenant_id=tenant_id,
                request_id=lookup.request_id,
            )
            audit_records = await self._audit_logs.list_by_request_id(
                tenant_id=tenant_id,
                request_id=lookup.request_id,
            )
            return retrieval_records, audit_records
        trace_id = lookup.trace_id
        if trace_id is None:
            return [], []
        retrieval_records = await self._retrieval_logs.list_by_trace_id(
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
        audit_records = await self._audit_logs.list_by_trace_id(
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
        return retrieval_records, audit_records


def _build_summary(
    *,
    tenant_id: str,
    lookup: DiagnosticsLookupRequest,
    retrieval_records: Sequence[RetrievalLogRecord],
    audit_records: Sequence[AuditLogRecord],
) -> DiagnosticsSummary:
    primary_retrieval = retrieval_records[-1] if retrieval_records else None
    primary_audit = audit_records[-1] if audit_records else None
    tenant_user_id = _first_text(
        [record.user_id for record in retrieval_records],
        [record.user_id for record in audit_records],
    )
    request_id = _first_text(
        [lookup.request_id],
        [record.request_id for record in retrieval_records],
        [record.request_id for record in audit_records],
    )
    trace_id = _first_text(
        [lookup.trace_id],
        [record.trace_id for record in retrieval_records],
        [record.trace_id for record in audit_records],
    )
    status = _overall_status(retrieval_records=retrieval_records, audit_records=audit_records)
    failure_stage = _failure_stage(retrieval_records=retrieval_records, audit_records=audit_records)
    audit_metadata = primary_audit.metadata if primary_audit is not None else {}
    return DiagnosticsSummary(
        tenant_id=tenant_id,
        user_id=tenant_user_id or "unknown",
        request_id=request_id or "unknown",
        trace_id=trace_id or "unknown",
        action=primary_audit.action if primary_audit is not None else None,
        status=status,
        top_k=primary_retrieval.top_k if primary_retrieval is not None else None,
        result_count=primary_retrieval.result_count if primary_retrieval is not None else None,
        highest_rerank_score=_highest_rerank_score(retrieval_records),
        citation_count=_nested_int(audit_metadata, ("citation", "citation_count")),
        context_item_count=_nested_int(audit_metadata, ("context", "item_count")),
        context_source_count=_nested_int(audit_metadata, ("context", "source_count")),
        generation_provider=_nested_text(audit_metadata, ("generation", "provider")),
        generation_model=_nested_text(audit_metadata, ("generation", "model")),
        generation_version=_nested_text(audit_metadata, ("generation", "version")),
        prompt_token_count=_nested_int(
            audit_metadata,
            ("generation", "token_usage", "prompt_tokens"),
        ),
        completion_token_count=_nested_int(
            audit_metadata,
            ("generation", "token_usage", "completion_tokens"),
        ),
        total_token_count=_nested_int(
            audit_metadata,
            ("generation", "token_usage", "total_tokens"),
        ),
        event_count=_safe_int(audit_metadata.get("event_count")),
        latency_ms=_max_latency(retrieval_records=retrieval_records, audit_records=audit_records),
        failure_stage=failure_stage,
        error_code=_first_text(
            [record.error_code for record in audit_records],
            [record.error_code for record in retrieval_records],
        ),
    )


def _build_stages(
    *,
    retrieval_records: Sequence[RetrievalLogRecord],
    audit_records: Sequence[AuditLogRecord],
) -> tuple[DiagnosticsStageSummary, ...]:
    stages: list[DiagnosticsStageSummary] = []
    if retrieval_records:
        latest = retrieval_records[-1]
        stages.append(
            DiagnosticsStageSummary(
                name=FailureStage.RETRIEVAL,
                status=latest.status,
                latency_ms=latest.latency_ms,
                error_code=latest.error_code,
                counts={
                    key: value
                    for key, value in {
                        "top_k": latest.top_k,
                        "result_count": latest.result_count,
                        "dense_top_k": _safe_int(latest.metadata.get("dense_top_k")),
                        "sparse_top_k": _safe_int(latest.metadata.get("sparse_top_k")),
                    }.items()
                    if value is not None
                },
            )
        )
    for audit_record in audit_records:
        stage = _stage_from_audit(audit_record)
        if stage is None:
            continue
        stages.append(
            DiagnosticsStageSummary(
                name=stage,
                status=_safe_stage_status(audit_record.status),
                latency_ms=audit_record.latency_ms,
                error_code=audit_record.error_code,
                counts=_counts_from_audit(audit_record.metadata),
            )
        )
    return tuple(stages)


def _highest_rerank_score(records: Sequence[RetrievalLogRecord]) -> float | None:
    scores = [record.rerank_score for record in records if record.rerank_score is not None]
    if scores:
        return max(scores)
    metadata_scores: list[float] = []
    for record in records:
        rerank = record.metadata.get("rerank")
        if isinstance(rerank, Mapping):
            for key in ("highest_score", "rerank_score", "score"):
                value = rerank.get(key)
                if isinstance(value, int | float) and not isinstance(value, bool):
                    metadata_scores.append(float(value))
    return max(metadata_scores) if metadata_scores else None


def _failure_stage(
    *,
    retrieval_records: Sequence[RetrievalLogRecord],
    audit_records: Sequence[AuditLogRecord],
) -> FailureStage | None:
    for audit_record in reversed(audit_records):
        stage = _stage_from_metadata(audit_record.metadata)
        if stage is not None:
            return stage
        if audit_record.status in {"failure", "denied"} or audit_record.error_code is not None:
            mapped = _stage_from_audit(audit_record)
            if mapped is not None:
                return mapped
    for retrieval_record in reversed(retrieval_records):
        if retrieval_record.status == "failure" or retrieval_record.error_code is not None:
            return FailureStage.RETRIEVAL
    return None


def _stage_from_metadata(metadata: Mapping[str, object]) -> FailureStage | None:
    error_details = metadata.get("error_details")
    if not isinstance(error_details, Mapping):
        return None
    value = error_details.get("stage")
    if not isinstance(value, str):
        return None
    return _coerce_stage(value)


def _stage_from_audit(record: AuditLogRecord) -> FailureStage | None:
    stage = _stage_from_metadata(record.metadata)
    if stage is not None:
        return stage
    action = record.action.lower()
    code = (record.error_code or "").lower()
    if "source.resolve" in action or "source_resolution" in code:
        return FailureStage.SOURCE_RESOLUTION
    if record.status == "denied" or "forbidden" in code or "auth" in code or "permission" in code:
        return FailureStage.PERMISSION
    if "chat" in action or "query" in action or "generation" in code or "llm" in code:
        return FailureStage.GENERATION
    if "citation" in code:
        return FailureStage.CITATION
    if "audit" in code:
        return FailureStage.AUDIT
    if "infra" in code or "storage" in code:
        return FailureStage.INFRASTRUCTURE
    return None


def _coerce_stage(value: str) -> FailureStage:
    try:
        return FailureStage(value)
    except ValueError:
        return FailureStage.UNKNOWN


def _overall_status(
    *,
    retrieval_records: Sequence[RetrievalLogRecord],
    audit_records: Sequence[AuditLogRecord],
) -> str:
    statuses = [record.status for record in retrieval_records] + [
        record.status for record in audit_records
    ]
    if "failure" in statuses:
        return "failure"
    if "denied" in statuses:
        return "denied"
    if statuses:
        return "success"
    return "unknown"


def _safe_stage_status(value: str) -> StageStatus:
    if value in {"success", "failure", "denied", "degraded", "not_available"}:
        return cast(StageStatus, value)
    return "unknown"


def _counts_from_audit(metadata: Mapping[str, object]) -> dict[str, int | float]:
    counts: dict[str, int | float] = {}
    for key, value in {
        "citation_count": _nested_int(metadata, ("citation", "citation_count")),
        "context_item_count": _nested_int(metadata, ("context", "item_count")),
        "context_source_count": _nested_int(metadata, ("context", "source_count")),
        "event_count": _safe_int(metadata.get("event_count")),
        "total_token_count": _nested_int(metadata, ("generation", "token_usage", "total_tokens")),
    }.items():
        if value is not None:
            counts[key] = value
    return counts


def _max_latency(
    *,
    retrieval_records: Sequence[RetrievalLogRecord],
    audit_records: Sequence[AuditLogRecord],
) -> float | None:
    latencies = [record.latency_ms for record in retrieval_records] + [
        record.latency_ms for record in audit_records
    ]
    return max(latencies) if latencies else None


def _nested_int(metadata: Mapping[str, object], path: tuple[str, ...]) -> int | None:
    value: object = metadata
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return _safe_int(value)


def _nested_text(metadata: Mapping[str, object], path: tuple[str, ...]) -> str | None:
    value: object = metadata
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _first_text(*groups: Sequence[str | None]) -> str | None:
    for group in groups:
        for value in group:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _next_steps(stage: FailureStage | None) -> tuple[str, ...]:
    if stage == FailureStage.SOURCE_RESOLUTION:
        return (
            ".venv\\Scripts\\python.exe -m pytest tests/integration/api/test_sources_routes.py -q",
            ".venv\\Scripts\\python.exe -m pytest tests/integration/api/test_sidecar_routes.py -q",
        )
    if stage == FailureStage.RETRIEVAL:
        return (
            ".venv\\Scripts\\python.exe -m pytest tests/integration/api/test_query_routes.py -q",
            ".venv\\Scripts\\python.exe -m pytest tests/eval -q",
        )
    if stage == FailureStage.GENERATION:
        return (
            ".venv\\Scripts\\python.exe -m pytest tests/unit/rag/test_query_service.py -q",
            ".venv\\Scripts\\python.exe -m pytest tests/integration/api/test_chat_routes.py -q",
        )
    return (
        ".venv\\Scripts\\python.exe -m pytest tests/integration/api/test_diagnostics_routes.py -q",
        ".venv\\Scripts\\python.exe -m pytest tests/unit/web/test_sidecar_static_contract.py -q",
    )


def _safe_storage_details(*, lookup: DiagnosticsLookupRequest) -> dict[str, object]:
    details: dict[str, object] = {}
    if lookup.request_id is not None:
        details["request_id"] = lookup.request_id
    if lookup.trace_id is not None:
        details["trace_id"] = lookup.trace_id
    return details


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
