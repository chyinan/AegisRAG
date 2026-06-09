from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SAFE_AUDIT_LOG_FIELDS = (
    "id",
    "tenant_id",
    "user_id",
    "request_id",
    "trace_id",
    "action",
    "resource_type",
    "resource_id",
    "status",
    "latency_ms",
    "error_code",
    "created_at",
    "safe_summary",
    "association",
    "safe_counts",
)

SAFE_AUDIT_ASSOCIATION_FIELDS = (
    "agent_run_id",
    "tool_call_id",
    "tool_name",
    "permission",
    "status",
    "error_code",
    "latency_ms",
    "arguments_summary",
    "result_summary",
    "steps_used",
    "tool_calls_used",
    "validation_counts",
)

SAFE_AUDIT_EXPORT_FIELDS = (
    "export_id",
    "generated_at",
    "filter_summary",
    "fields",
    "item_count",
    "request_ids",
    "trace_ids",
    "items",
)


class AuditLogQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    status: str | None = None
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)
    include_associations: bool = True

    @field_validator(
        "user_id",
        "request_id",
        "trace_id",
        "action",
        "resource_type",
        "resource_id",
        "status",
    )
    @classmethod
    def _optional_identifier_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @model_validator(mode="after")
    def _created_at_window_must_be_ordered(self) -> AuditLogQueryRequest:
        if (
            self.created_at_from is not None
            and self.created_at_to is not None
            and self.created_at_from > self.created_at_to
        ):
            raise ValueError("created_at_from must be before or equal to created_at_to")
        return self


class AuditExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    status: str | None = None
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None
    limit: int = Field(default=200, ge=1, le=500)
    include_associations: bool = True
    format: Literal["json"] = "json"

    @field_validator(
        "user_id",
        "request_id",
        "trace_id",
        "action",
        "resource_type",
        "resource_id",
        "status",
    )
    @classmethod
    def _optional_identifier_must_not_be_blank(cls, value: str | None) -> str | None:
        return AuditLogQueryRequest._optional_identifier_must_not_be_blank(value)

    @model_validator(mode="after")
    def _created_at_window_must_be_ordered(self) -> AuditExportRequest:
        if (
            self.created_at_from is not None
            and self.created_at_to is not None
            and self.created_at_from > self.created_at_to
        ):
            raise ValueError("created_at_from must be before or equal to created_at_to")
        return self

    def to_query(self) -> AuditLogQueryRequest:
        return AuditLogQueryRequest(
            user_id=self.user_id,
            request_id=self.request_id,
            trace_id=self.trace_id,
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            status=self.status,
            created_at_from=self.created_at_from,
            created_at_to=self.created_at_to,
            limit=min(self.limit, 200),
            include_associations=self.include_associations,
        )


class AuditLogAssociationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_run_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    permission: str | None = None
    status: str | None = None
    error_code: str | None = None
    latency_ms: float | None = None
    arguments_summary: dict[str, object] = Field(default_factory=dict)
    result_summary: dict[str, object] = Field(default_factory=dict)
    steps_used: int | None = None
    tool_calls_used: int | None = None
    validation_counts: dict[str, int] = Field(default_factory=dict)


class AuditLogSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    tenant_id: str
    user_id: str
    request_id: str
    trace_id: str
    action: str
    resource_type: str
    resource_id: str
    status: str
    latency_ms: float
    error_code: str | None
    created_at: datetime
    safe_summary: dict[str, int | float | str] = Field(default_factory=dict)
    association: AuditLogAssociationSummary | None = None
    safe_counts: dict[str, int | float | str] = Field(default_factory=dict)


class AuditExplorerListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[AuditLogSummary, ...]
    next_steps: tuple[str, ...] = ()


class AuditExportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    export_id: str
    generated_at: str
    filter_summary: dict[str, object]
    fields: tuple[str, ...] = SAFE_AUDIT_LOG_FIELDS
    item_count: int = Field(ge=0)
    request_ids: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()
    items: tuple[AuditLogSummary, ...] = ()
