from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from math import isfinite
from typing import Protocol
from uuid import uuid4

from packages.agent.dto import ToolCallQuery, ToolCallRecord
from packages.audit.dto import (
    SAFE_AUDIT_LOG_FIELDS,
    AuditExplorerListResponse,
    AuditExportPayload,
    AuditExportRequest,
    AuditLogAssociationSummary,
    AuditLogQueryRequest,
    AuditLogSummary,
)
from packages.audit.exceptions import (
    AUDIT_EXPLORER_EXPORT_FAILED,
    AUDIT_EXPLORER_FORBIDDEN,
    AUDIT_EXPLORER_STORAGE_READ_FAILED,
    AuditExplorerError,
)
from packages.auth.policies import has_audit_explorer_read_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.audit_repositories import AuditLogRecord, AuditLogStorageQuery
from packages.data.storage.exceptions import StorageError

_NEXT_STEPS = (
    ".venv\\Scripts\\python.exe -m pytest tests/unit/audit_explorer -q",
    ".venv\\Scripts\\python.exe -m pytest tests/integration/api/test_audit_explorer_routes.py -q",
    "node tests/unit/web/sidecar_behavior_runner.js",
)
_SAFE_COUNT_KEYS = frozenset(
    {
        "metadata_count",
        "resource_metadata_count",
        "role_count",
        "permission_count",
        "citation_count",
        "context_item_count",
        "context_source_count",
        "result_count",
        "event_count",
        "top_k",
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "steps_used",
        "tool_calls_used",
        "validated_citation_count",
        "unsupported_citation_count",
        "failed_tool_reference_count",
    }
)
_SAFE_LABEL_KEYS = frozenset(
    {
        "termination_reason",
        "failure_stage",
        "auth_method",
        "decision",
        "validation_status",
    }
)
_FORBIDDEN_KEY_PARTS = (
    "answer",
    "api_key",
    "apikey",
    "authorization",
    "chunk",
    "content",
    "embedding",
    "exception",
    "file_path",
    "local_path",
    "object_key",
    "payload",
    "prompt",
    "provider_raw",
    "query",
    "secret",
    "source_uri",
    "sql",
    "token",
    "tsquery",
    "vector",
)


class AuditLogReader(Protocol):
    async def list_records(
        self,
        *,
        tenant_id: str,
        query: AuditLogStorageQuery,
    ) -> list[AuditLogRecord]:
        ...


class ToolCallReader(Protocol):
    async def list_tool_calls(self, query: ToolCallQuery) -> list[ToolCallRecord]:
        ...


class AuditExplorerService:
    def __init__(
        self,
        *,
        audit_logs: AuditLogReader,
        tool_calls: ToolCallReader | None = None,
        audit: AuditPort | None = None,
        generated_at: Callable[[], str] | None = None,
        export_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._audit_logs = audit_logs
        self._tool_calls = tool_calls
        self._audit = audit
        self._generated_at = generated_at or _utc_now_iso
        self._export_id_factory = export_id_factory or (lambda: f"audit-export-{uuid4()}")

    async def list_logs(
        self,
        *,
        context: AuthenticatedRequestContext,
        query: AuditLogQueryRequest,
    ) -> AuditExplorerListResponse:
        _assert_permission(context)
        try:
            records = await self._audit_logs.list_records(
                tenant_id=context.auth.tenant_id,
                query=_storage_query_from_query(query),
            )
        except StorageError as exc:
            raise AuditExplorerError(
                code=AUDIT_EXPLORER_STORAGE_READ_FAILED,
                message="Audit explorer storage read failed.",
                details=_safe_error_details(context=context, stage="storage"),
                status_code=503,
            ) from exc

        items = tuple(
            [
                await self._summary_from_record(
                    context=context,
                    record=record,
                    include_association=query.include_associations,
                )
                for record in records
            ]
        )
        return AuditExplorerListResponse(items=items, next_steps=_NEXT_STEPS)

    async def export_logs(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: AuditExportRequest,
    ) -> AuditExportPayload:
        started = time.perf_counter()
        _assert_permission(context)
        try:
            records = await self._audit_logs.list_records(
                tenant_id=context.auth.tenant_id,
                query=_storage_query_from_export(request),
            )
        except StorageError as exc:
            raise AuditExplorerError(
                code=AUDIT_EXPLORER_STORAGE_READ_FAILED,
                message="Audit explorer storage read failed.",
                details=_safe_error_details(context=context, stage="storage"),
                status_code=503,
            ) from exc

        items = tuple(
            [
                await self._summary_from_record(
                    context=context,
                    record=record,
                    include_association=request.include_associations,
                )
                for record in records
            ]
        )
        payload = AuditExportPayload(
            export_id=self._export_id_factory(),
            generated_at=self._generated_at(),
            filter_summary=_filter_summary(request),
            fields=SAFE_AUDIT_LOG_FIELDS,
            item_count=len(items),
            request_ids=_unique_texts(item.request_id for item in items),
            trace_ids=_unique_texts(item.trace_id for item in items),
            items=items,
        )
        await self._record_export_audit(
            context=context,
            payload=payload,
            request=request,
            started=started,
        )
        return payload

    async def _summary_from_record(
        self,
        *,
        context: AuthenticatedRequestContext,
        record: AuditLogRecord,
        include_association: bool,
    ) -> AuditLogSummary:
        safe_summary = _safe_summary(record)
        return AuditLogSummary(
            id=record.id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            request_id=record.request_id,
            trace_id=record.trace_id,
            action=record.action,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            status=record.status,
            latency_ms=record.latency_ms,
            error_code=_safe_label(record.error_code),
            created_at=record.created_at,
            safe_summary=safe_summary,
            safe_counts=dict(safe_summary),
            association=(
                await self._association(context=context, record=record)
                if include_association
                else None
            ),
        )

    async def _association(
        self,
        *,
        context: AuthenticatedRequestContext,
        record: AuditLogRecord,
    ) -> AuditLogAssociationSummary | None:
        merged = {**record.metadata, **record.resource_metadata}
        agent_run_id = _safe_label(merged.get("agent_run_id"))
        tool_name = _safe_label(merged.get("tool_name"))
        has_agent_action = record.action.startswith("agent.") or record.resource_type == "agent_run"
        if not has_agent_action and agent_run_id is None and tool_name is None:
            return None

        tool_call = await self._find_tool_call(
            context=context,
            record=record,
            agent_run_id=agent_run_id,
            tool_name=tool_name,
        )
        if tool_call is not None:
            return AuditLogAssociationSummary(
                agent_run_id=tool_call.agent_run_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.tool_name,
                permission=_safe_label(tool_call.permission),
                status=tool_call.status,
                error_code=_safe_label(tool_call.error_code),
                latency_ms=tool_call.latency_ms,
                arguments_summary=_safe_mapping(tool_call.arguments_summary),
                result_summary=_safe_mapping(tool_call.result_summary),
                steps_used=_safe_int(merged.get("steps_used")),
                tool_calls_used=_safe_int(merged.get("tool_calls_used")),
                validation_counts=_validation_counts(merged),
            )
        return AuditLogAssociationSummary(
            agent_run_id=agent_run_id,
            tool_call_id=_safe_label(merged.get("tool_call_id")),
            tool_name=tool_name,
            permission=_safe_label(merged.get("permission")),
            status=_safe_label(merged.get("status")) or record.status,
            error_code=_safe_label(record.error_code or merged.get("error_code")),
            latency_ms=_safe_float(merged.get("latency_ms")) or record.latency_ms,
            steps_used=_safe_int(merged.get("steps_used")),
            tool_calls_used=_safe_int(merged.get("tool_calls_used")),
            validation_counts=_validation_counts(merged),
        )

    async def _find_tool_call(
        self,
        *,
        context: AuthenticatedRequestContext,
        record: AuditLogRecord,
        agent_run_id: str | None,
        tool_name: str | None,
    ) -> ToolCallRecord | None:
        if self._tool_calls is None:
            return None
        records = await self._tool_calls.list_tool_calls(
            ToolCallQuery(
                tenant_id=context.auth.tenant_id,
                request_id=record.request_id,
                trace_id=record.trace_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                limit=10,
            )
        )
        return records[0] if records else None

    async def _record_export_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        payload: AuditExportPayload,
        request: AuditExportRequest,
        started: float,
    ) -> None:
        if self._audit is None:
            return
        metadata: dict[str, object] = {
            "filter_summary": payload.filter_summary,
            "item_count": payload.item_count,
            "export_fields": tuple(payload.fields),
            "format": request.format,
            "status": "success",
        }
        try:
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action="audit_explorer.export",
                    resource=AuditResource(
                        type="audit_export",
                        id=payload.export_id,
                        metadata=metadata,
                    ),
                    status=AuditStatus.SUCCESS,
                    latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
                    metadata=metadata,
                    created_at=datetime.now(tz=UTC),
                )
            )
        except StorageError as exc:
            raise AuditExplorerError(
                code=AUDIT_EXPLORER_EXPORT_FAILED,
                message="Audit explorer export audit failed.",
                details=_safe_error_details(context=context, stage="audit"),
                status_code=503,
            ) from exc


def _assert_permission(context: AuthenticatedRequestContext) -> None:
    if has_audit_explorer_read_permission(context.auth):
        return
    raise AuditExplorerError(
        code=AUDIT_EXPLORER_FORBIDDEN,
        message="Audit explorer permission is required.",
        details=_safe_error_details(context=context, stage="permission"),
        status_code=403,
    )


def _storage_query_from_query(query: AuditLogQueryRequest) -> AuditLogStorageQuery:
    return AuditLogStorageQuery(
        user_id=query.user_id,
        request_id=query.request_id,
        trace_id=query.trace_id,
        action=query.action,
        resource_type=query.resource_type,
        resource_id=query.resource_id,
        status=query.status,
        created_at_from=query.created_at_from,
        created_at_to=query.created_at_to,
        limit=query.limit,
    )


def _storage_query_from_export(request: AuditExportRequest) -> AuditLogStorageQuery:
    return AuditLogStorageQuery(
        user_id=request.user_id,
        request_id=request.request_id,
        trace_id=request.trace_id,
        action=request.action,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        status=request.status,
        created_at_from=request.created_at_from,
        created_at_to=request.created_at_to,
        limit=min(request.limit, 500),
    )


def _filter_summary(request: AuditExportRequest) -> dict[str, object]:
    summary: dict[str, object] = {}
    for key in (
        "user_id",
        "request_id",
        "trace_id",
        "action",
        "resource_type",
        "resource_id",
        "status",
    ):
        value = getattr(request, key)
        if value is not None:
            summary[key] = value
    if request.created_at_from is not None:
        summary["created_at_from"] = request.created_at_from.isoformat()
    if request.created_at_to is not None:
        summary["created_at_to"] = request.created_at_to.isoformat()
    summary["limit"] = request.limit
    summary["include_associations"] = request.include_associations
    return summary


def _safe_summary(record: AuditLogRecord) -> dict[str, int | float | str]:
    metadata = record.metadata
    resource_metadata = record.resource_metadata
    values: dict[str, object] = {
        "metadata_count": len(metadata),
        "resource_metadata_count": len(resource_metadata),
        "role_count": _sequence_count(metadata.get("roles") or resource_metadata.get("roles")),
        "permission_count": _sequence_count(
            metadata.get("permissions") or resource_metadata.get("permissions")
        ),
        "citation_count": _metadata_int(metadata, "citation_count", ("citation", "citation_count")),
        "context_item_count": _metadata_int(
            metadata,
            "context_item_count",
            ("context", "item_count"),
        ),
        "context_source_count": _metadata_int(
            metadata,
            "context_source_count",
            ("context", "source_count"),
        ),
        "result_count": _metadata_int(metadata, "result_count", ("result", "count")),
        "event_count": _metadata_int(metadata, "event_count", ("events", "count")),
        "top_k": _metadata_int(metadata, "top_k", ("retrieval", "top_k")),
        "input_token_count": _token_int(metadata, "input_tokens", "prompt_tokens"),
        "output_token_count": _token_int(metadata, "output_tokens", "completion_tokens"),
        "total_token_count": _token_int(metadata, "total_tokens"),
        "steps_used": _metadata_int(metadata, "steps_used", ("agent", "steps_used")),
        "tool_calls_used": _metadata_int(metadata, "tool_calls_used", ("agent", "tool_calls_used")),
        "termination_reason": _metadata_text(
            metadata,
            "termination_reason",
            ("agent", "termination_reason"),
        ),
        "failure_stage": _metadata_text(metadata, "failure_stage", ("error_details", "stage")),
        "auth_method": _metadata_text(metadata, "auth_method", ("auth", "method")),
        "validation_status": _metadata_text(
            metadata,
            "validation_status",
            ("validation", "status"),
        ),
    }
    return {
        key: safe
        for key, value in values.items()
        if (safe := _safe_summary_value(key, value)) is not None
    }


def _safe_mapping(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in value.items():
        safe_key = str(key).strip()
        if not safe_key or _forbidden_key(safe_key):
            continue
        safe_value = _safe_nested_value(item)
        if safe_value is not None:
            safe[safe_key] = safe_value
    return safe


def _safe_nested_value(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if isfinite(value) and value >= 0 else None
    if isinstance(value, str):
        return _safe_label(value)
    if isinstance(value, Mapping):
        return _safe_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(
            item for raw in value if (item := _safe_nested_value(raw)) is not None
        )
    return None


def _validation_counts(metadata: Mapping[str, object]) -> dict[str, int]:
    counts = {}
    for key in (
        "validated_citation_count",
        "unsupported_citation_count",
        "failed_tool_reference_count",
    ):
        value = _safe_int(metadata.get(key))
        if value is not None:
            counts[key] = value
    validation = metadata.get("validation")
    if isinstance(validation, Mapping):
        for key in (
            "validated_citation_count",
            "unsupported_citation_count",
            "failed_tool_reference_count",
        ):
            value = _safe_int(validation.get(key))
            if value is not None:
                counts[key] = value
    return counts


def _safe_summary_value(key: str, value: object) -> int | float | str | None:
    if key in _SAFE_COUNT_KEYS:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float):
            return value if isfinite(value) and value >= 0 else None
        return None
    if key in _SAFE_LABEL_KEYS:
        return _safe_label(value)
    return None


def _metadata_int(
    metadata: Mapping[str, object],
    flat_key: str,
    nested_path: tuple[str, ...],
) -> int | None:
    value = _safe_int(metadata.get(flat_key))
    if value is not None:
        return value
    nested: object = metadata
    for key in nested_path:
        if not isinstance(nested, Mapping):
            return None
        nested = nested.get(key)
    return _safe_int(nested)


def _metadata_text(
    metadata: Mapping[str, object],
    flat_key: str,
    nested_path: tuple[str, ...],
) -> str | None:
    value = _safe_label(metadata.get(flat_key))
    if value is not None:
        return value
    nested: object = metadata
    for key in nested_path:
        if not isinstance(nested, Mapping):
            return None
        nested = nested.get(key)
    return _safe_label(nested)


def _token_int(metadata: Mapping[str, object], *keys: str) -> int | None:
    token_usage = metadata.get("token_usage")
    if not isinstance(token_usage, Mapping):
        generation = metadata.get("generation")
        token_usage = generation.get("token_usage") if isinstance(generation, Mapping) else None
    if not isinstance(token_usage, Mapping):
        return None
    for key in keys:
        value = _safe_int(token_usage.get(key))
        if value is not None:
            return value
    return None


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and isfinite(float(value)) and float(value) >= 0:
        return float(value)
    return None


def _sequence_count(value: object) -> int | None:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return len(value)
    return None


def _safe_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        return None
    if _forbidden_value(normalized):
        return None
    if not all(char.isalnum() or char in {"_", "-", ".", ":", "/"} for char in normalized):
        return None
    return normalized


def _forbidden_key(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return any(
        part in normalized or part.replace("_", "") in compact
        for part in _FORBIDDEN_KEY_PARTS
    )


def _forbidden_value(value: str) -> bool:
    lowered = value.lower()
    return (
        "bearer " in lowered
        or "token=" in lowered
        or "secret=" in lowered
        or "api_key" in lowered
        or "access_token" in lowered
        or "file://" in lowered
        or "s3://" in lowered
        or "minio://" in lowered
        or "\\" in value
        or value.startswith("/")
        or (len(value) > 2 and value[1:3] in {":\\", ":/"})
    )


def _unique_texts(values: Iterable[object]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        safe = _safe_label(value)
        if safe is not None and safe not in seen:
            seen.append(safe)
    return tuple(seen)


def _safe_error_details(
    *,
    context: AuthenticatedRequestContext,
    stage: str,
) -> dict[str, object]:
    return {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "stage": stage,
    }


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
