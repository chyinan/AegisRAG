from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from math import isfinite
from typing import Literal, TypeGuard

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from packages.auth.policies import FrozenDict
from packages.retrieval.exceptions import RETRIEVAL_INVALID_REQUEST, RetrievalError

ScalarMetadataValue = str | int | float | bool | None
MetadataValue = ScalarMetadataValue
MAX_RETRIEVAL_TOP_K = 100


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    query: str
    top_k: int = 10
    metadata_filter: Mapping[str, object] = Field(default_factory=FrozenDict)
    score_threshold: float | None = None
    request_id: str
    trace_id: str

    def __init__(self, **data: object) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise RetrievalError(
                code=RETRIEVAL_INVALID_REQUEST,
                message="Retrieval request is invalid.",
                details=_safe_validation_details(data=data, error_count=exc.error_count()),
                status_code=400,
            ) from exc

    @field_validator("query", "request_id", "trace_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("top_k", mode="before")
    @classmethod
    def _top_k_must_be_int(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("top_k must be an integer")
        return value

    @field_validator("top_k")
    @classmethod
    def _top_k_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("top_k must be greater than 0")
        if value > MAX_RETRIEVAL_TOP_K:
            raise ValueError(f"top_k must be less than or equal to {MAX_RETRIEVAL_TOP_K}")
        return value

    @field_validator("score_threshold")
    @classmethod
    def _score_threshold_in_range(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError("score_threshold must be between 0 and 1")
        return value

    @field_validator("metadata_filter", mode="before")
    @classmethod
    def _metadata_filter_must_be_structured(cls, value: object) -> FrozenDict:
        if value is None:
            return FrozenDict()
        if isinstance(value, FrozenDict):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("metadata_filter must be a mapping")

        normalized: dict[str, MetadataValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("metadata_filter keys must be strings")
            normalized_key = key.strip()
            if not normalized_key:
                raise ValueError("metadata_filter keys must not be blank")
            if normalized_key.startswith("$") or any(char.isspace() for char in normalized_key):
                raise ValueError("metadata_filter keys must be structured field names")
            normalized[normalized_key] = _normalize_metadata_value(item)
        return FrozenDict(normalized)


class RetrievalFilterSet(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()
    department: str | None = None
    permissions: tuple[str, ...] = ()
    metadata_filter: Mapping[str, object] = Field(default_factory=FrozenDict)
    acl_filter: Mapping[str, object] = Field(default_factory=FrozenDict)
    include_deleted: bool = False

    @field_validator("tenant_id", "user_id")
    @classmethod
    def _identifier_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized


class RetrievalCandidate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    source: str | None = None
    source_type: str
    source_uri: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    tenant_id: str
    acl: Mapping[str, object] = Field(default_factory=FrozenDict)
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator(
        "document_id",
        "version_id",
        "chunk_id",
        "source_type",
        "retrieval_method",
        "tenant_id",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value if item.strip())
        if not normalized:
            raise ValueError("title_path must contain at least one non-blank item")
        return normalized

    @field_validator("score")
    @classmethod
    def _score_must_be_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("score must be finite")
        return value

    @field_validator("acl", "metadata", mode="before")
    @classmethod
    def _structured_mapping(cls, value: object) -> FrozenDict:
        if value is None:
            return FrozenDict()
        if isinstance(value, FrozenDict):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return FrozenDict(value)

    @model_validator(mode="after")
    def _validate_page_range(self) -> RetrievalCandidate:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_start < 1 or self.page_end < 1:
            raise ValueError("page numbers must be 1-based")
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class RetrievalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    top_k: int
    query_summary: dict[str, int]
    candidates: tuple[RetrievalCandidate, ...] = ()
    latency_ms: float | None = None
    error_code: str | None = None


class RetrievalLogCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    created_by: str
    status: Literal["success", "failure"]
    latency_ms: float
    top_k: int
    result_count: int
    rerank_score: float | None = None
    error_code: str | None = None
    query_summary: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None

    @field_validator("request_id", "trace_id", "tenant_id", "user_id", "created_by", "status")
    @classmethod
    def _log_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("latency_ms")
    @classmethod
    def _latency_non_negative(cls, value: float) -> float:
        if not isfinite(value) or value < 0:
            raise ValueError("latency_ms must be a finite non-negative number")
        return value

    @field_validator("top_k")
    @classmethod
    def _log_top_k_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("top_k must be greater than 0")
        return value

    @field_validator("result_count")
    @classmethod
    def _result_count_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("result_count must not be negative")
        return value

    @field_validator("rerank_score")
    @classmethod
    def _rerank_score_valid(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not isfinite(value):
            raise ValueError("rerank_score must be finite")
        return value


class RetrievalLogRecord(RetrievalLogCreate):
    id: str
    created_at: datetime
    updated_at: datetime


def _normalize_metadata_value(value: object) -> MetadataValue:
    if _is_scalar_metadata_value(value):
        return value
    if isinstance(value, str) or isinstance(value, Mapping):
        raise ValueError("metadata_filter values must be scalar")
    if isinstance(value, Iterable):
        raise ValueError("metadata_filter values must be scalar")
    raise ValueError("metadata_filter values must be scalar")


def _is_scalar_metadata_value(value: object) -> TypeGuard[ScalarMetadataValue]:
    if value is None or isinstance(value, str | int | bool):
        return True
    return isinstance(value, float) and isfinite(value)


def _safe_validation_details(*, data: Mapping[str, object], error_count: int) -> dict[str, object]:
    details: dict[str, object] = {
        "error_code": RETRIEVAL_INVALID_REQUEST,
        "error_count": error_count,
    }
    request_id = data.get("request_id")
    if isinstance(request_id, str) and request_id.strip():
        details["request_id"] = request_id.strip()
    trace_id = data.get("trace_id")
    if isinstance(trace_id, str) and trace_id.strip():
        details["trace_id"] = trace_id.strip()
    top_k = data.get("top_k")
    if isinstance(top_k, int) and not isinstance(top_k, bool):
        details["top_k"] = top_k
    return details
