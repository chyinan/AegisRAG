from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from math import isfinite
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvalCategory = Literal[
    "policy",
    "product_manual",
    "faq",
    "technical_doc",
    "tech_doc",
    "policy_compliance",
    "ops_manual",
    "product_knowledge",
    "security_audit",
    "multi_hop",
]
AttackType = Literal["none", "acl_isolation", "prompt_injection"]
FailureStage = Literal[
    "retrieval",
    "rerank",
    "context_packing",
    "prompt_build",
    "generation",
    "citation",
    "permission",
    "no_answer",
    "dataset",
    "runner",
]
ScalarJson = str | int | float | bool | None
SAFE_FIXTURE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_METADATA_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
FORBIDDEN_METADATA_FILTER_KEYS = {
    "tenant_id",
    "user_id",
    "acl",
    "roles",
    "permissions",
    "allowed_users",
    "allowed_roles",
    "denied_users",
}
FORBIDDEN_TEXT_MARKERS = (
    "api_key",
    "access_token",
    "bearer ",
    "sk-",
    "-----begin",
)


def _default_acl() -> dict[str, object]:
    return {"visibility": "tenant"}


class RagEvalAcl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    visibility: Literal["tenant", "private"]
    allowed_roles: tuple[str, ...] = ()
    allowed_users: tuple[str, ...] = ()
    allowed_departments: tuple[str, ...] = ()

    @field_validator("allowed_roles", "allowed_users", "allowed_departments", mode="before")
    @classmethod
    def _safe_tuple(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value)

    @field_validator("allowed_roles", "allowed_users", "allowed_departments")
    @classmethod
    def _safe_acl_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _reject_forbidden_markers(item, "acl")
        return value

    @model_validator(mode="after")
    def _private_acl_has_allowlist(self) -> Self:
        if (
            self.visibility == "private"
            and not self.allowed_roles
            and not self.allowed_users
            and not self.allowed_departments
        ):
            raise ValueError("private acl must define an allowlist")
        return self


class ExpectedCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    required: bool = True

    @field_validator("document_id", "version_id", "chunk_id")
    @classmethod
    def _safe_required_identifier(cls, value: str) -> str:
        return _normalize_safe_fixture_id(value)

    @field_validator("required", mode="before")
    @classmethod
    def _required_bool(cls, value: object) -> object:
        if not isinstance(value, bool):
            raise ValueError("required must be a boolean")
        return value

    @field_validator("page_start", "page_end", mode="before")
    @classmethod
    def _strict_optional_page(cls, value: object) -> object:
        return _normalize_optional_int(value, "page")

    @model_validator(mode="after")
    def _page_range(self) -> Self:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("page range must be valid")
        return self


class ExpectedAnswerPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    must_include_terms: tuple[str, ...] = ()
    must_not_include_terms: tuple[str, ...] = ()

    @field_validator("must_include_terms", "must_not_include_terms", mode="before")
    @classmethod
    def _safe_terms(cls, value: object) -> tuple[str, ...]:
        terms = _normalize_text_tuple(value)
        for term in terms:
            if len(term) > 80:
                raise ValueError("answer expectation terms must be short")
            _reject_forbidden_markers(term, "answer expectation terms")
        return terms


class RagEvalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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
    expected_citations: tuple[ExpectedCitation, ...] = ()
    answerable: bool
    expected_no_answer: bool = False
    expected_answer: ExpectedAnswerPolicy = Field(default_factory=ExpectedAnswerPolicy)
    attack_type: AttackType = "none"
    top_k: int = 5

    @field_validator("query")
    @classmethod
    def _required_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        if len(normalized) > 500:
            raise ValueError("query must be short enough for eval fixtures")
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
        return _normalize_text_tuple(value)

    @field_validator("expected_documents", "expected_chunks")
    @classmethod
    def _safe_expected_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_normalize_safe_fixture_id(item) for item in value)

    @field_validator("metadata_filter", mode="before")
    @classmethod
    def _metadata_filter(cls, value: object) -> dict[str, ScalarJson]:
        return _normalize_metadata(value, filter_mode=True)

    @field_validator("answerable", "expected_no_answer", mode="before")
    @classmethod
    def _strict_bool(cls, value: object) -> object:
        if not isinstance(value, bool):
            raise ValueError("value must be a boolean")
        return value

    @field_validator("top_k", mode="before")
    @classmethod
    def _top_k_int(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("top_k must be an integer")
        return value

    @field_validator("top_k")
    @classmethod
    def _top_k_range(cls, value: int) -> int:
        if value <= 0 or value > 100:
            raise ValueError("top_k must be between 1 and 100")
        return value

    @model_validator(mode="after")
    def _answer_expectation_consistency(self) -> Self:
        required_citations = [citation for citation in self.expected_citations if citation.required]
        if not self.answerable:
            if not self.expected_no_answer:
                raise ValueError("unanswerable cases must set expected_no_answer")
            if required_citations:
                raise ValueError("unanswerable cases must not require citations")
        if self.answerable and self.expected_no_answer:
            raise ValueError("answerable cases must not set expected_no_answer")
        if self.answerable and (
            not self.expected_documents and not self.expected_chunks and not self.expected_citations
        ):
            raise ValueError("answerable cases must define expected ids")
        return self


class RagEvalCorpusRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    version_id: str
    chunk_id: str
    tenant_id: str
    content: str
    token_count: int
    source: str = "synthetic"
    source_uri: str
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str = "hybrid"
    acl: RagEvalAcl = Field(default_factory=lambda: RagEvalAcl(visibility="tenant"))
    metadata: dict[str, ScalarJson] = Field(default_factory=dict)
    relevant_case_ids: tuple[str, ...]

    @field_validator("document_id", "version_id", "chunk_id", "tenant_id")
    @classmethod
    def _safe_required_identifier(cls, value: str) -> str:
        return _normalize_safe_fixture_id(value)

    @field_validator("content")
    @classmethod
    def _safe_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must not be blank")
        if len(normalized) > 2000:
            raise ValueError("content must be at most 2000 characters")
        _reject_forbidden_markers(normalized, "content")
        return normalized

    @field_validator("source_type", "retrieval_method")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("source")
    @classmethod
    def _synthetic_source(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != "synthetic":
            raise ValueError("source must be synthetic")
        return normalized

    @field_validator("source_uri")
    @classmethod
    def _synthetic_source_uri(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("synthetic://rag-eval/"):
            raise ValueError("source_uri must use synthetic://rag-eval/")
        if (
            "\\" in normalized
            or normalized.lower().startswith("file:")
            or re.search(r"(^|/)[A-Za-z]:($|/|\\)", normalized)
        ):
            raise ValueError("source_uri must not be a local path")
        return normalized

    @field_validator("title_path", "relevant_case_ids", mode="before")
    @classmethod
    def _required_tuple(cls, value: object) -> tuple[str, ...]:
        items = _normalize_text_tuple(value)
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
        return _normalize_metadata(value, filter_mode=False)

    @field_validator("token_count", mode="before")
    @classmethod
    def _token_count_int(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("token_count must be an integer")
        return value

    @field_validator("token_count")
    @classmethod
    def _token_count_range(cls, value: int) -> int:
        if value <= 0 or value > 2000:
            raise ValueError("token_count must be between 1 and 2000")
        return value

    @field_validator("score")
    @classmethod
    def _score(cls, value: float) -> float:
        if not isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError("score must be between 0 and 1")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def _score_number(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("score must be a number")
        return value

    @field_validator("page_start", "page_end", mode="before")
    @classmethod
    def _strict_optional_page(cls, value: object) -> object:
        return _normalize_optional_int(value, "page")

    @model_validator(mode="after")
    def _page_range(self) -> Self:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("page range must be valid")
        return self


class RagEvalDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_version: str
    cases: tuple[RagEvalCase, ...]
    corpus: tuple[RagEvalCorpusRecord, ...]

    @field_validator("dataset_version")
    @classmethod
    def _safe_dataset_version(cls, value: str) -> str:
        return _normalize_safe_fixture_id(value)


class RagEvalGenerationSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str | None = None
    model: str | None = None
    version: str | None = None
    token_usage: dict[str, int] | None = None
    finish_reason: str | None = None
    error_code: str | None = None


class RagEvalCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    top_k: int
    latency_ms: float
    passed: bool
    failure_stage: FailureStage | None = None
    matched_documents: tuple[str, ...] = ()
    matched_chunks: tuple[str, ...] = ()
    matched_citations: tuple[str, ...] = ()
    retrieval_result_count: int = 0
    context_item_count: int = 0
    citation_count: int = 0
    unsupported_count: int = 0
    forged_reference_count: int = 0
    prompt_risk_count: int = 0
    generation: RagEvalGenerationSummary = Field(default_factory=RagEvalGenerationSummary)


class RagEvalReportSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_count: int
    passed_count: int
    failed_count: int
    retrieval_hit_rate: float
    citation_coverage: float
    required_citation_count: int = 0
    matched_required_citation_count: int = 0
    no_answer_correctness: float
    no_answer_case_count: int = 0
    acl_isolation_passed: bool
    prompt_injection_passed: bool
    average_latency_ms: float


class RagEvalReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    generated_at: str
    report_type: Literal["rag_quality_runner"] = "rag_quality_runner"
    summary: RagEvalReportSummary
    cases: tuple[RagEvalCaseResult, ...]


def _normalize_text_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ValueError("value must be an iterable of strings")
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


def _normalize_metadata(value: object, *, filter_mode: bool) -> dict[str, ScalarJson]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping")

    normalized: dict[str, ScalarJson] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        normalized_key = key.strip()
        if not SAFE_METADATA_KEY_PATTERN.fullmatch(normalized_key):
            raise ValueError("metadata keys must be structured field names")
        if filter_mode and normalized_key in FORBIDDEN_METADATA_FILTER_KEYS:
            raise ValueError("metadata_filter cannot widen authorization scope")
        normalized[normalized_key] = _normalize_scalar(item)
    return normalized


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
    _reject_forbidden_markers(normalized, "identifier")
    return normalized


def _reject_forbidden_markers(value: str, field_name: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in FORBIDDEN_TEXT_MARKERS):
        raise ValueError(f"{field_name} must not contain secret-like markers")


def _normalize_optional_int(value: object, field_name: str) -> object:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value
