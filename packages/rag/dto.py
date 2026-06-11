from __future__ import annotations

import re
from collections.abc import Mapping
from math import isfinite
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from packages.auth.policies import FrozenDict
from packages.rag.source_metadata import build_safe_source_metadata, safe_source_display_name

OversizedPolicy = Literal["drop", "fail_closed"]
PromptRole = Literal["system", "user"]
SAFE_TEXT_PATTERN = re.compile(r"^[^\r\n\t\x00-\x1f\x7f]+$")
MAX_QUERY_CHARS = 4000
MAX_OUTPUT_TOKENS = 4096


class ContextPackingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_tokens: int = 3000
    merge_adjacent: bool = False
    include_parent_context: bool = False
    include_child_context: bool = False
    include_neighbor_context: bool = False
    max_related_chunks_per_candidate: int = 2
    oversized_policy: OversizedPolicy = "drop"

    @field_validator("max_tokens", "max_related_chunks_per_candidate")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be greater than 0")
        return value


class ContextCandidate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    content: str
    token_count: int
    document_id: str
    version_id: str
    chunk_id: str
    tenant_id: str
    acl: Mapping[str, object] = Field(default_factory=FrozenDict)
    source: str | None = None
    source_uri: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator(
        "content",
        "document_id",
        "version_id",
        "chunk_id",
        "tenant_id",
        "source_type",
        "retrieval_method",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("source", "source_uri")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("token_count")
    @classmethod
    def _token_count_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("token_count must be greater than 0")
        return value

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value if item.strip())
        if not normalized:
            raise ValueError("title_path must contain at least one non-blank item")
        return normalized

    @field_validator("score")
    @classmethod
    def _score_must_be_normalized(cls, value: float) -> float:
        if not isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError("score must be a finite value between 0 and 1")
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

    @field_serializer("acl", "metadata")
    def _serialize_frozen_dict(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)

    @model_validator(mode="after")
    def _validate_page_range(self) -> ContextCandidate:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_start < 1 or self.page_end < 1:
            raise ValueError("page numbers must be 1-based")
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class PackedCitationSource(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    source: str | None = None
    source_uri: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    token_count: int
    inclusion_reason: str
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator(
        "document_id",
        "version_id",
        "chunk_id",
        "source_type",
        "retrieval_method",
        "inclusion_reason",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("source", "source_uri")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("token_count")
    @classmethod
    def _token_count_positive(cls, value: int) -> int:
        return _positive_int(value, field_name="token_count")

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _title_path(value)

    @field_validator("score")
    @classmethod
    def _score_must_be_normalized(cls, value: float) -> float:
        return _normalized_score(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _structured_mapping(cls, value: object) -> FrozenDict:
        return _frozen_mapping(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)

    @model_validator(mode="after")
    def _validate_page_range(self) -> PackedCitationSource:
        _validate_page_range(self.page_start, self.page_end)
        return self


class PackedContextItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    token_count: int
    document_id: str
    version_id: str
    chunk_ids: tuple[str, ...]
    source: str | None = None
    source_uri: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    citation_sources: tuple[PackedCitationSource, ...]

    @field_validator(
        "content",
        "document_id",
        "version_id",
        "source_type",
        "retrieval_method",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("source", "source_uri")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("token_count")
    @classmethod
    def _token_count_positive(cls, value: int) -> int:
        return _positive_int(value, field_name="token_count")

    @field_validator("chunk_ids")
    @classmethod
    def _chunk_ids_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_required_text(item) for item in value)
        if not normalized:
            raise ValueError("chunk_ids must contain at least one item")
        return normalized

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _title_path(value)

    @field_validator("score")
    @classmethod
    def _score_must_be_normalized(cls, value: float) -> float:
        return _normalized_score(value)

    @model_validator(mode="after")
    def _validate_page_range(self) -> PackedContextItem:
        _validate_page_range(self.page_start, self.page_end)
        return self


class ContextDroppedCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str
    document_id: str | None = None
    version_id: str | None = None
    chunk_id: str | None = None
    tenant_id: str | None = None
    token_count: int | None = None
    score: float | None = None
    retrieval_method: str | None = None
    related_reason: str | None = None


class ContextPackingTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    input_count: int
    authorized_count: int
    packed_count: int
    dropped_count: int
    total_tokens: int
    budget: int
    drop_reasons: Mapping[str, int] = Field(default_factory=dict)
    merged_groups: tuple[Mapping[str, object], ...] = ()
    related_context_items: tuple[Mapping[str, object], ...] = ()
    related_context_counts: Mapping[str, int] = Field(default_factory=dict)
    error_code: str | None = None
    safe_counts: Mapping[str, int] = Field(default_factory=dict)

    @field_serializer("drop_reasons", "related_context_counts", "safe_counts")
    def _serialize_mappings(self, value: Mapping[str, int]) -> dict[str, int]:
        return dict(value)

    @field_serializer("merged_groups")
    def _serialize_merged_groups(
        self,
        value: tuple[Mapping[str, object], ...],
    ) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in value)

    @field_serializer("related_context_items")
    def _serialize_related_context_items(
        self,
        value: tuple[Mapping[str, object], ...],
    ) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in value)


class PackedContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[PackedContextItem, ...]
    total_tokens: int
    budget: int
    dropped_candidates: tuple[ContextDroppedCandidate, ...] = ()
    packing_trace: ContextPackingTrace


class Citation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    source_display_name: str
    source_ref: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    retrieval_method: str
    score: float

    @classmethod
    def from_source(cls, source: PackedCitationSource) -> Citation:
        safe_source = build_safe_source_metadata(
            source=source.source,
            source_uri=source.source_uri,
            source_type=source.source_type,
            document_id=source.document_id,
            version_id=source.version_id,
            chunk_id=source.chunk_id,
            page_start=source.page_start,
            page_end=source.page_end,
            title_path=source.title_path,
        )
        return cls(
            document_id=safe_source.document_id,
            version_id=safe_source.version_id,
            chunk_id=safe_source.chunk_id,
            source_display_name=safe_source.source_display_name,
            source_ref=safe_source.source_ref,
            source_type=safe_source.source_type,
            page_start=safe_source.page_start,
            page_end=safe_source.page_end,
            title_path=safe_source.title_path,
            retrieval_method=source.retrieval_method,
            score=source.score,
        )

    @field_validator(
        "document_id",
        "version_id",
        "chunk_id",
        "source_type",
        "retrieval_method",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("source_display_name")
    @classmethod
    def _safe_source_display_name(cls, value: str) -> str:
        return safe_source_display_name(value)

    @field_validator("source_ref")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _title_path(value)

    @field_validator("score")
    @classmethod
    def _score_must_be_normalized(cls, value: float) -> float:
        return _normalized_score(value)

    @model_validator(mode="after")
    def _validate_page_range(self) -> Citation:
        _validate_page_range(self.page_start, self.page_end)
        return self


class UnsupportedClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str
    summary: str
    severity: str = "low"

    @field_validator("reason", "summary", "severity")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


class CitationExtractionTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    input_source_count: int
    allowed_source_count: int
    citation_count: int
    unsupported_count: int
    forged_reference_count: int = 0
    no_answer: bool = False
    error_code: str | None = None
    safe_counts: Mapping[str, int] = Field(default_factory=dict)

    @field_serializer("safe_counts")
    def _serialize_safe_counts(self, value: Mapping[str, int]) -> dict[str, int]:
        return dict(value)


class CitationExtractionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str
    citations: tuple[Citation, ...] = ()
    unsupported_claims: tuple[UnsupportedClaim, ...] = ()
    no_answer: bool = False
    trace: CitationExtractionTrace


class QueryCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_style: str | None = None
    max_output_tokens: int | None = Field(default=None, gt=0, le=MAX_OUTPUT_TOKENS)

    @field_validator("query")
    @classmethod
    def _query_required(cls, value: str) -> str:
        normalized = _required_text(value)
        if len(normalized) > MAX_QUERY_CHARS:
            raise ValueError(f"query must be less than or equal to {MAX_QUERY_CHARS} characters")
        return normalized

    @field_validator("answer_style")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("metadata_filter", mode="before")
    @classmethod
    def _metadata_filter_object(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata_filter must be an object")
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("metadata_filter keys must be strings")
            normalized_key = key.strip()
            if not normalized_key:
                raise ValueError("metadata_filter keys must not be blank")
            if normalized_key.startswith("$") or any(char.isspace() for char in normalized_key):
                raise ValueError("metadata_filter keys must be structured field names")
            if not _is_scalar_metadata_value(item):
                raise ValueError("metadata_filter values must be scalar")
            normalized[normalized_key] = item
        return normalized


class QueryRequestBody(QueryCommand):
    def to_command(self) -> QueryCommand:
        return QueryCommand(**self.model_dump())


class ChatRequestBody(QueryCommand):
    session_id: str | None = None

    @field_validator("session_id")
    @classmethod
    def _optional_session_id(cls, value: str | None) -> str | None:
        return _optional_text(value)

    def to_command(self) -> QueryCommand:
        values = self.model_dump(exclude={"session_id"})
        return QueryCommand(**values)


class QueryResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    answer: str
    citations: tuple[Citation, ...] = ()
    no_answer: bool = False
    unsupported_claims: tuple[UnsupportedClaim, ...] = ()
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator("request_id", "trace_id", "tenant_id", "user_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _structured_mapping(cls, value: object) -> FrozenDict:
        return _frozen_mapping(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class ChatResponse(QueryResponse):
    session_id: str

    @field_validator("session_id")
    @classmethod
    def _session_id_required(cls, value: str) -> str:
        return _required_text(value)


class ChatHistoryMessageResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    role: str
    content: str
    sequence_no: int
    request_id: str
    trace_id: str
    created_at: str
    citations: tuple[Citation, ...] = ()
    no_answer: bool = False

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        normalized = _required_text(value)
        if normalized not in {"user", "assistant", "system_summary"}:
            raise ValueError("role must be user, assistant, or system_summary")
        return normalized

    @field_validator("content", "request_id", "trace_id", "created_at")
    @classmethod
    def _required_string(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("sequence_no")
    @classmethod
    def _sequence_no(cls, value: int) -> int:
        return _positive_int(value, field_name="sequence_no")


class ChatHistoryResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    session_id: str
    messages: tuple[ChatHistoryMessageResponse, ...] = ()

    @field_validator("session_id")
    @classmethod
    def _session_id(cls, value: str) -> str:
        return _required_text(value)


class PromptBuilderConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_query_chars: int = 4000
    max_context_item_chars: int = 12000
    max_context_items: int = 20
    include_source_metadata: bool = True
    language: str = "zh-CN"
    default_no_answer_text: str = "无法从给定上下文确认。"

    @field_validator("max_query_chars", "max_context_item_chars", "max_context_items")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be greater than 0")
        return value

    @field_validator("language")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = _required_text(value)
        if not SAFE_TEXT_PATTERN.fullmatch(normalized):
            raise ValueError("value must be a single-line plain text value")
        return normalized

    @field_validator("default_no_answer_text")
    @classmethod
    def _safe_no_answer_text(cls, value: str) -> str:
        normalized = _required_text(value)
        if not SAFE_TEXT_PATTERN.fullmatch(normalized):
            raise ValueError("default_no_answer_text must be single-line plain text")
        return normalized


class PromptBuildRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    query: str
    packed_context: PackedContext
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    session_id: str | None = None
    language: str = "zh-CN"
    answer_style: str | None = None
    max_output_tokens: int | None = None
    memory_context: PromptMemoryContext | None = None

    @field_validator("packed_context", mode="before")
    @classmethod
    def _packed_context_must_be_dto(cls, value: object) -> PackedContext:
        if not isinstance(value, PackedContext):
            raise ValueError("packed_context must be a PackedContext DTO")
        return value

    @field_validator("memory_context", mode="before")
    @classmethod
    def _memory_context_must_be_dto(cls, value: object) -> PromptMemoryContext | None:
        if value is None:
            return None
        if not isinstance(value, PromptMemoryContext):
            raise ValueError("memory_context must be a PromptMemoryContext DTO")
        return value

    @field_validator("query", "request_id", "trace_id", "tenant_id", "user_id", "language")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("session_id", "answer_style")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("max_output_tokens")
    @classmethod
    def _positive_optional_int(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("max_output_tokens must be greater than 0")
        return value


class PromptHistoryMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    content: str
    token_count: int
    sequence_no: int

    @field_validator("role")
    @classmethod
    def _allowed_role(cls, value: str) -> str:
        normalized = _required_text(value)
        if normalized not in {"user", "assistant", "system_summary"}:
            raise ValueError("role must be user, assistant, or system_summary")
        return normalized

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("token_count")
    @classmethod
    def _token_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("token_count must be greater than or equal to 0")
        return value

    @field_validator("sequence_no")
    @classmethod
    def _sequence_no(cls, value: int) -> int:
        return _positive_int(value, field_name="sequence_no")


class PromptMemoryContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    messages: tuple[PromptHistoryMessage, ...] = ()
    message_count: int = 0
    used_count: int = 0
    dropped_count: int = 0
    token_count: int = 0

    @field_validator("session_id")
    @classmethod
    def _session_id(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("message_count", "used_count", "dropped_count", "token_count")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("value must be greater than or equal to 0")
        return value


class PromptMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: PromptRole
    name: str
    content: str

    @field_validator("name", "content")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


class PromptBuildTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    context_item_count: int
    source_chunk_count: int
    input_char_count: int
    prompt_part_count: int
    detected_risk_count: int
    risk_types: tuple[str, ...] = ()
    injection_pattern_detected: bool = False
    error_code: str | None = None
    safe_counts: Mapping[str, int] = Field(default_factory=dict)

    @field_serializer("safe_counts")
    def _serialize_safe_counts(self, value: Mapping[str, int]) -> dict[str, int]:
        return dict(value)


class PromptBuildResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    messages: tuple[PromptMessage, ...]
    trace: PromptBuildTrace
    citation_source_ids: tuple[str, ...]
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _structured_mapping(cls, value: object) -> FrozenDict:
        if value is None:
            return FrozenDict()
        if isinstance(value, FrozenDict):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return FrozenDict(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


def _required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _positive_int(value: int, *, field_name: str) -> int:
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return value


def _title_path(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in value if item.strip())
    if not normalized:
        raise ValueError("title_path must contain at least one non-blank item")
    return normalized


def _normalized_score(value: float) -> float:
    if not isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("score must be a finite value between 0 and 1")
    return value


def _validate_page_range(page_start: int | None, page_end: int | None) -> None:
    if page_start is None and page_end is None:
        return
    if page_start is None or page_end is None:
        raise ValueError("page_start and page_end must both be set or both be None")
    if page_start < 1 or page_end < 1:
        raise ValueError("page numbers must be 1-based")
    if page_end < page_start:
        raise ValueError("page_end must be greater than or equal to page_start")


def _frozen_mapping(value: object) -> FrozenDict:
    if value is None:
        return FrozenDict()
    if isinstance(value, FrozenDict):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("value must be a mapping")
    return FrozenDict(value)


def _is_scalar_metadata_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | bool):
        return True
    return isinstance(value, float) and isfinite(value)
