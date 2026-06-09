from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReviewItemType = Literal[
    "questionable_answer",
    "low_confidence_citation",
    "no_answer",
    "acl_boundary",
    "prompt_injection",
    "tool_output",
    "eval_failure",
]
ReviewSeverity = Literal["low", "medium", "high", "critical"]
ReviewStatus = Literal[
    "open",
    "accepted",
    "rejected",
    "needs_followup",
    "converted_to_eval_case",
]
ReviewSourceView = Literal[
    "source_evidence",
    "retrieval_diagnostics",
    "eval_evidence",
    "audit_explorer",
]

SAFE_REVIEW_IDENTIFIER_FIELDS = (
    "document_id",
    "version_id",
    "chunk_id",
    "page_start",
    "page_end",
    "citation_ref",
    "eval_report_filename",
    "eval_case_id",
    "audit_log_id",
    "agent_run_id",
    "tool_call_id",
)
SAFE_REVIEW_SUMMARY_FIELDS = (
    "failure_stage",
    "error_code",
    "reason_code",
    "metric_name",
    "expected_behavior",
    "observed_behavior",
    "risk_label",
    "safe_note",
    "citation_count",
    "unsupported_count",
    "forged_reference_count",
    "prompt_risk_count",
    "retrieval_result_count",
    "context_item_count",
    "tool_call_count",
    "latency_ms",
)
SAFE_REVIEW_STATUS_HISTORY_FIELDS = (
    "status",
    "changed_by",
    "changed_at",
    "reason_code",
)
SAFE_EVAL_CANDIDATE_FIELDS = (
    "candidate_id",
    "source_review_item_id",
    "case_type",
    "safe_identifiers",
    "failure_stage",
    "safe_metric_counts",
    "expected_behavior",
    "request_id",
    "trace_id",
    "requires_human_confirmation",
)
SAFE_REVIEW_ITEM_FIELDS = (
    "id",
    "item_type",
    "severity",
    "status",
    "request_id",
    "trace_id",
    "source_view",
    "safe_identifiers",
    "safe_summary",
    "status_history",
    "allowed_transitions",
    "eval_candidate",
    "created_by",
    "tenant_id",
    "created_at",
    "updated_at",
)
FORBIDDEN_REVIEW_FIELD_PARTS = (
    "access_token",
    "answer",
    "api_key",
    "apikey",
    "authorization",
    "chunk_text",
    "content",
    "embedding",
    "file_path",
    "local_path",
    "object_key",
    "prompt",
    "provider_raw_response",
    "query",
    "raw_exception",
    "secret",
    "source_uri",
    "sql",
    "token",
    "tool_observation",
    "tsquery",
    "vector",
)


class ReviewItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_type: ReviewItemType
    severity: ReviewSeverity = "medium"
    request_id: str
    trace_id: str
    source_view: ReviewSourceView
    safe_identifiers: dict[str, object] = Field(default_factory=dict)
    safe_summary: dict[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "trace_id")
    @classmethod
    def _required_safe_text(cls, value: str) -> str:
        return safe_text(value, max_length=128)

    @field_validator("safe_identifiers", mode="before")
    @classmethod
    def _safe_identifiers(cls, value: object) -> dict[str, object]:
        return safe_mapping(value, SAFE_REVIEW_IDENTIFIER_FIELDS)

    @field_validator("safe_summary", mode="before")
    @classmethod
    def _safe_summary(cls, value: object) -> dict[str, object]:
        return safe_mapping(value, SAFE_REVIEW_SUMMARY_FIELDS)


class ReviewItemQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_type: ReviewItemType | None = None
    severity: ReviewSeverity | None = None
    status: ReviewStatus | None = None
    request_id: str | None = None
    trace_id: str | None = None
    source_view: ReviewSourceView | None = None
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("request_id", "trace_id")
    @classmethod
    def _optional_safe_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return safe_text(value, max_length=128)

    @model_validator(mode="after")
    def _created_window_ordered(self) -> ReviewItemQueryRequest:
        if (
            self.created_at_from is not None
            and self.created_at_to is not None
            and self.created_at_from > self.created_at_to
        ):
            raise ValueError("created_at_from must be before or equal to created_at_to")
        return self


class ReviewItemStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ReviewStatus
    reason_code: str | None = None

    @field_validator("reason_code")
    @classmethod
    def _optional_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return safe_text(value, max_length=64)


class ReviewItemStatusHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ReviewStatus
    changed_by: str
    changed_at: datetime
    reason_code: str | None = None


class EvalCandidatePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    source_review_item_id: str
    case_type: str
    safe_identifiers: dict[str, object] = Field(default_factory=dict)
    failure_stage: str | None = None
    safe_metric_counts: dict[str, int | float] = Field(default_factory=dict)
    expected_behavior: str
    request_id: str
    trace_id: str
    requires_human_confirmation: bool = True


class ReviewItemSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    item_type: ReviewItemType
    severity: ReviewSeverity
    status: ReviewStatus
    request_id: str
    trace_id: str
    source_view: ReviewSourceView
    safe_identifiers: dict[str, object] = Field(default_factory=dict)
    safe_summary: dict[str, object] = Field(default_factory=dict)
    status_history: tuple[ReviewItemStatusHistoryEntry, ...] = ()
    allowed_transitions: tuple[ReviewStatus, ...] = ()
    eval_candidate: EvalCandidatePreview | None = None
    created_by: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime


class ReviewQueueListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ReviewItemSummary, ...]
    next_steps: tuple[str, ...] = ()


def safe_mapping(value: object, allowed_fields: tuple[str, ...]) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("value must be an object")
    result: dict[str, object] = {}
    allowed = set(allowed_fields)
    for key, raw in value.items():
        safe_key = safe_text(str(key), max_length=64)
        if safe_key not in allowed or forbidden_review_key(safe_key):
            continue
        safe_value = safe_value_from_input(raw)
        if safe_value is not None:
            result[safe_key] = safe_value
    return result


def safe_value_from_input(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if 0 <= value <= 1_000_000_000 else None
    if isinstance(value, float):
        return value if 0 <= value <= 1_000_000_000 else None
    if isinstance(value, str):
        return safe_text(value, max_length=256)
    if isinstance(value, list | tuple):
        items = [safe_value_from_input(item) for item in value[:20]]
        return tuple(item for item in items if item is not None)
    return None


def safe_text(value: str, *, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    if len(normalized) > max_length:
        raise ValueError("value is too long")
    if forbidden_review_value(normalized):
        raise ValueError("value is not safe for review queue storage")
    return normalized


def forbidden_review_key(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return any(
        part in normalized or part.replace("_", "") in compact
        for part in FORBIDDEN_REVIEW_FIELD_PARTS
    )


def forbidden_review_value(value: str) -> bool:
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
