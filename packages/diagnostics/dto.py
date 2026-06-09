from __future__ import annotations

from enum import StrEnum
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from packages.diagnostics.exceptions import DIAGNOSTICS_INVALID_LOOKUP, DiagnosticsError


class FailureStage(StrEnum):
    RETRIEVAL = "retrieval"
    SPARSE_RETRIEVAL = "sparse_retrieval"
    RRF_MERGE = "rrf_merge"
    RERANK = "rerank"
    CONTEXT_PACKING = "context_packing"
    GENERATION = "generation"
    CITATION = "citation"
    PERMISSION = "permission"
    SOURCE_RESOLUTION = "source_resolution"
    AUDIT = "audit"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


StageStatus = Literal["success", "failure", "denied", "degraded", "not_available", "unknown"]


class DiagnosticsLookupRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str | None = None
    trace_id: str | None = None
    include_report: bool = False

    def __init__(self, **data: object) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise DiagnosticsError(
                code=DIAGNOSTICS_INVALID_LOOKUP,
                message="Diagnostics lookup is invalid.",
                details={"error_count": exc.error_count()},
                status_code=400,
            ) from exc

    @field_validator("request_id", "trace_id", mode="before")
    @classmethod
    def _normalize_optional_id(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("identifier must be a string")
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _request_or_trace_required(self) -> DiagnosticsLookupRequest:
        if self.request_id is None and self.trace_id is None:
            raise ValueError("request_id or trace_id is required")
        return self


class DiagnosticsStageSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: FailureStage
    status: StageStatus
    latency_ms: float | None = None
    error_code: str | None = None
    counts: dict[str, int | float | str] = Field(default_factory=dict)

    @field_validator("latency_ms")
    @classmethod
    def _latency_valid(cls, value: float | None) -> float | None:
        if value is not None and (not isfinite(value) or value < 0):
            raise ValueError("latency_ms must be finite and non-negative")
        return value


class DiagnosticsSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    user_id: str
    request_id: str
    trace_id: str
    action: str | None = None
    status: str
    top_k: int | None = None
    result_count: int | None = None
    highest_rerank_score: float | None = None
    citation_count: int | None = None
    context_item_count: int | None = None
    context_source_count: int | None = None
    generation_provider: str | None = None
    generation_model: str | None = None
    generation_version: str | None = None
    prompt_token_count: int | None = None
    completion_token_count: int | None = None
    total_token_count: int | None = None
    event_count: int | None = None
    latency_ms: float | None = None
    failure_stage: FailureStage | None = None
    error_code: str | None = None


class DiagnosticsReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    lookup: DiagnosticsLookupRequest
    summary: DiagnosticsSummary
    stages: tuple[DiagnosticsStageSummary, ...] = ()
    next_steps: tuple[str, ...] = ()
    generated_at: str


class DiagnosticsResolveResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    lookup: DiagnosticsLookupRequest
    summary: DiagnosticsSummary
    stages: tuple[DiagnosticsStageSummary, ...] = ()
    next_steps: tuple[str, ...] = ()
    report: DiagnosticsReport | None = None
