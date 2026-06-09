from __future__ import annotations

from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvalEvidenceReportType(StrEnum):
    RAG_DATASET_SMOKE = "rag_dataset_smoke"
    RAG_QUALITY_RUNNER = "rag_quality_runner"
    RAG_CI_SMOKE_GATE = "rag_ci_smoke_gate"
    UNKNOWN = "unknown"


class EvalEvidenceFailureStage(StrEnum):
    RETRIEVAL = "retrieval"
    RERANK = "rerank"
    CONTEXT_PACKING = "context_packing"
    PROMPT_BUILD = "prompt_build"
    GENERATION = "generation"
    CITATION = "citation"
    PERMISSION = "permission"
    NO_ANSWER = "no_answer"
    DATASET = "dataset"
    RUNNER = "runner"
    UNKNOWN = "unknown"


class EvalEvidenceGateMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    threshold_name: str
    passed: bool
    expected: float | int | bool
    actual: float | int | bool


class EvalEvidenceGenerationSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str | None = None
    model: str | None = None
    version: str | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    token_usage: dict[str, int] | None = None

    @field_validator("token_usage")
    @classmethod
    def _safe_token_usage(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        allowed = {"input_tokens", "output_tokens", "total_tokens"}
        return {
            key: item
            for key, item in value.items()
            if key in allowed and not isinstance(item, bool) and isinstance(item, int) and item >= 0
        }


class EvalCaseEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    failure_stage: EvalEvidenceFailureStage | None = None
    matched_documents: tuple[str, ...] = ()
    matched_chunks: tuple[str, ...] = ()
    matched_citations: tuple[str, ...] = ()
    retrieval_result_count: int = 0
    context_item_count: int = 0
    citation_count: int = 0
    unsupported_count: int = 0
    forged_reference_count: int = 0
    prompt_risk_count: int = 0
    request_id: str | None = None
    trace_id: str | None = None
    top_k: int | None = None
    latency_ms: float | None = None
    generation: EvalEvidenceGenerationSummary = Field(default_factory=EvalEvidenceGenerationSummary)

    @field_validator("latency_ms")
    @classmethod
    def _latency_safe(cls, value: float | None) -> float | None:
        if value is not None and (not isfinite(value) or value < 0):
            raise ValueError("latency_ms must be finite and non-negative")
        return value


class EvalEvidenceReportSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_filename: str
    generated_at: str | None = None
    report_type: EvalEvidenceReportType
    dataset_version: str | None = None
    dataset_name: str | None = None
    case_count: int = 0
    passed_count: int | None = None
    failed_count: int | None = None
    retrieval_hit_rate: float | None = None
    citation_coverage: float | None = None
    no_answer_correctness: float | None = None
    acl_isolation: bool | None = None
    prompt_injection: bool | None = None
    average_latency_ms: float | None = None
    decision: str = "unknown"
    failed_metric_names: tuple[str, ...] = ()
    failure_stages: tuple[EvalEvidenceFailureStage, ...] = ()


class EvalEvidenceReportListRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)


class EvalReportListRequest(EvalEvidenceReportListRequest):
    pass


class EvalEvidenceResolveResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: EvalEvidenceReportSummary
    failed_cases: tuple[EvalCaseEvidence, ...] = ()
    gate_metrics: tuple[EvalEvidenceGateMetric, ...] = ()
    next_steps: tuple[str, ...] = ()


class EvalEvidenceReportListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[EvalEvidenceReportSummary, ...]
    next_steps: tuple[str, ...] = ()
