from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvalCategory = Literal["policy", "product_manual", "faq", "technical_doc"]
AttackType = Literal["none", "acl_isolation", "prompt_injection"]
FailureStage = Literal[
    "dense",
    "sparse",
    "merge",
    "rerank",
    "threshold",
    "permission",
    "no_answer",
    "dataset",
    "runner",
]
ScalarJson = str | int | float | bool | None
SAFE_FIXTURE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _default_acl() -> dict[str, object]:
    return {"visibility": "tenant"}


class RetrievalEvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    category: EvalCategory
    query: str
    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()
    department: str | None = None
    permissions: tuple[str, ...] = ()
    metadata_filter: dict[str, ScalarJson] = Field(default_factory=dict)
    expected_documents: tuple[str, ...] = ()
    expected_chunks: tuple[str, ...] = ()
    answerable: bool
    attack_type: AttackType = "none"
    top_k: int = 5

    @field_validator("query")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("case_id", "tenant_id", "user_id")
    @classmethod
    def _safe_required_identifier(cls, value: str) -> str:
        return _normalize_safe_fixture_id(value)

    @field_validator("department")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("roles", "permissions", "expected_documents", "expected_chunks", mode="before")
    @classmethod
    def _text_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            values: Iterable[object] = (value,)
        elif isinstance(value, Mapping) or not isinstance(value, Iterable):
            raise ValueError("value must be an iterable of strings")
        else:
            values = value

        normalized: list[str] = []
        for item in values:
            if not isinstance(item, str):
                raise ValueError("values must be strings")
            text = item.strip()
            if not text:
                raise ValueError("values must not contain blank items")
            normalized.append(text)
        return tuple(normalized)

    @field_validator("expected_documents", "expected_chunks")
    @classmethod
    def _safe_expected_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_normalize_safe_fixture_id(item) for item in value)

    @field_validator("metadata_filter", mode="before")
    @classmethod
    def _metadata_filter(cls, value: object) -> dict[str, ScalarJson]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata_filter must be a mapping")

        normalized: dict[str, ScalarJson] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("metadata_filter keys must be strings")
            normalized_key = key.strip()
            if (
                not normalized_key
                or normalized_key.startswith("$")
                or normalized_key == "tenant_id"
                or any(char.isspace() for char in normalized_key)
            ):
                raise ValueError("metadata_filter keys must be structured field names")
            normalized[normalized_key] = _normalize_scalar(item)
        return normalized

    @field_validator("top_k", mode="before")
    @classmethod
    def _top_k_int(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("top_k must be an integer")
        return value

    @field_validator("top_k")
    @classmethod
    def _top_k_range(cls, value: int) -> int:
        if value <= 0 or value > 100:
            raise ValueError("top_k must be between 1 and 100")
        return value

    @model_validator(mode="after")
    def _answerable_cases_need_expectations(self) -> RetrievalEvalCase:
        if self.answerable and not self.expected_documents and not self.expected_chunks:
            raise ValueError("answerable cases must define expected ids")
        return self


class RetrievalEvalCorpusRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    tenant_id: str
    source_type: str = "markdown"
    source_uri: str = "synthetic://retrieval-eval"
    page_start: int | None = 1
    page_end: int | None = 1
    title_path: tuple[str, ...]
    score: float = 0.9
    retrieval_method: str = "hybrid"
    acl: dict[str, object] = Field(default_factory=_default_acl)
    metadata: dict[str, ScalarJson] = Field(default_factory=dict)
    relevant_case_ids: tuple[str, ...]

    @field_validator("document_id", "version_id", "chunk_id", "tenant_id")
    @classmethod
    def _safe_required_identifier(cls, value: str) -> str:
        return _normalize_safe_fixture_id(value)

    @field_validator("source_type", "source_uri", "retrieval_method")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("title_path", "relevant_case_ids", mode="before")
    @classmethod
    def _required_tuple(cls, value: object) -> tuple[str, ...]:
        items = RetrievalEvalCase._text_tuple(value)
        if not items:
            raise ValueError("value must not be empty")
        return items

    @field_validator("relevant_case_ids")
    @classmethod
    def _safe_relevant_case_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_normalize_safe_fixture_id(item) for item in value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, ScalarJson]:
        return RetrievalEvalCase._metadata_filter(value)

    @field_validator("score")
    @classmethod
    def _score(cls, value: float) -> float:
        if not isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError("score must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def _page_range(self) -> RetrievalEvalCorpusRecord:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("page range must be valid")
        return self


class RetrievalEvalCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    top_k: int
    latency_ms: float
    passed: bool
    failure_stage: FailureStage | None
    matched_documents: tuple[str, ...] = ()
    matched_chunks: tuple[str, ...] = ()


class RetrievalEvalReportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_count: int
    passed_count: int
    failed_count: int
    retrieval_hit_rate: float
    acl_isolation_passed: bool
    no_answer_passed: bool
    prompt_injection_passed: bool
    average_latency_ms: float
    top_k: Mapping[str, object]


class RetrievalEvalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: RetrievalEvalReportSummary
    cases: tuple[RetrievalEvalCaseResult, ...]


def _normalize_scalar(value: object) -> ScalarJson:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise ValueError("metadata values must be scalar")


def _normalize_safe_fixture_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("identifier must not be blank")
    if not SAFE_FIXTURE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("identifier must be a safe fixture id")
    return normalized
