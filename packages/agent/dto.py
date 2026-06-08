from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable, Coroutine, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ToolHandler: TypeAlias = Callable[..., Coroutine[Any, Any, object]]
FinalAnswerValidationStatus: TypeAlias = Literal["valid", "degraded", "invalid"]
MAX_FINAL_ANSWER_LENGTH = 12_000
MAX_FINAL_CITATIONS = 50

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_CITATION_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
_SAFE_SUMMARY_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")
_FORBIDDEN_SUMMARY_KEY_PARTS = (
    "absolute_path",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "chunk_text",
    "content",
    "cookie",
    "credential",
    "embedding",
    "file_path",
    "hidden_reasoning",
    "local_path",
    "password",
    "private_key",
    "prompt",
    "provider_payload",
    "query",
    "raw",
    "secret",
    "sql",
    "text",
    "token",
    "vector",
)
_SUMMARY_VALUE_SECRET_MARKERS = (
    "api_key",
    "authorization:",
    "bearer ",
    "password=",
    "secret=",
    "token=",
)


class ToolInvocationStatus(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


class ToolRateLimit(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_calls: int = Field(gt=0)
    window_seconds: float = Field(gt=0)

    @field_validator("window_seconds")
    @classmethod
    def _window_seconds_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("window_seconds must be finite")
        return value


class ToolRateLimitKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    user_id: str
    tool_name: str

    @field_validator("tenant_id", "user_id", "tool_name")
    @classmethod
    def _identifier_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized


class ToolRateLimitDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    remaining: int = Field(ge=0)
    reset_after_seconds: float = Field(ge=0)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    permission: str
    timeout_seconds: float = Field(gt=0)
    rate_limit: ToolRateLimit
    handler: ToolHandler

    @field_validator("name")
    @classmethod
    def _name_must_be_safe_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not _TOOL_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("name must be a lower snake_case identifier")
        return normalized

    @field_validator("description", "permission")
    @classmethod
    def _required_text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schema_must_be_pydantic_model(cls, value: object) -> type[BaseModel]:
        if not isinstance(value, type) or not issubclass(value, BaseModel):
            raise ValueError("schema must be a Pydantic BaseModel class")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        return value

    @field_validator("handler")
    @classmethod
    def _handler_must_be_callable(cls, value: object) -> ToolHandler:
        if isinstance(value, str) or not callable(value):
            raise ValueError("handler must be an explicitly registered async callable")
        call_method = value.__call__
        if not inspect.iscoroutinefunction(value) and not inspect.iscoroutinefunction(call_method):
            raise ValueError("handler must be an explicitly registered async callable")
        return value

    @property
    def input_json_schema(self) -> dict[str, Any]:
        return self.input_schema.model_json_schema()

    @property
    def output_json_schema(self) -> dict[str, Any]:
        return self.output_schema.model_json_schema()


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    status: ToolInvocationStatus
    output: dict[str, Any] | None = None
    latency_ms: float = Field(ge=0)
    metadata: Mapping[str, object] = Field(default_factory=dict)


AgentRunStorageStatus = Literal["running", "completed", "stopped", "failed"]
ToolCallStorageStatus = Literal["success", "denied", "failure"]

AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION = "AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION"
AGENT_FINAL_ANSWER_UNAUTHORIZED_SOURCE = "AGENT_FINAL_ANSWER_UNAUTHORIZED_SOURCE"
AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE = "AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE"
AGENT_FINAL_ANSWER_VALIDATION_FAILED = "AGENT_FINAL_ANSWER_VALIDATION_FAILED"


class AgentCitationRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)
    chunk_id: str = Field(min_length=1, max_length=128)
    source: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    tool_name: str | None = None
    observation_index: int | None = Field(default=None, ge=0)

    @field_validator("document_id", "version_id", "chunk_id")
    @classmethod
    def _identifier_must_be_safe(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        if not _is_safe_citation_identifier(normalized):
            raise ValueError("identifier must be a safe citation identifier")
        return normalized

    @field_validator("source")
    @classmethod
    def _optional_source_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if (
            len(normalized) > 200
            or _looks_like_absolute_path(normalized)
            or _looks_like_sensitive_value(normalized)
        ):
            raise ValueError("value must be a safe short identifier")
        return normalized

    @field_validator("tool_name")
    @classmethod
    def _optional_tool_name_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not _TOOL_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("tool_name must be a lower snake_case identifier")
        return normalized

    @model_validator(mode="after")
    def _page_window_must_be_ordered(self) -> AgentCitationRef:
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_start > self.page_end
        ):
            raise ValueError("page_start must be before or equal to page_end")
        return self

    @property
    def evidence_key(self) -> tuple[str, str, str, str | None, int | None, int | None]:
        return (
            self.document_id,
            self.version_id,
            self.chunk_id,
            self.source,
            self.page_start,
            self.page_end,
        )


class AgentFinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str = Field(max_length=MAX_FINAL_ANSWER_LENGTH)
    citations: tuple[AgentCitationRef, ...] = Field(default=(), max_length=MAX_FINAL_CITATIONS)

    @field_validator("answer")
    @classmethod
    def _answer_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("answer must not be blank")
        return normalized


class FinalAnswerValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_run_id: str | None
    answer: str = Field(max_length=MAX_FINAL_ANSWER_LENGTH)
    citations: tuple[AgentCitationRef, ...] = Field(default=(), max_length=MAX_FINAL_CITATIONS)

    @field_validator("agent_run_id")
    @classmethod
    def _optional_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("answer")
    @classmethod
    def _request_answer_must_be_bounded(cls, value: str) -> str:
        return value.strip()


class FinalAnswerValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: FinalAnswerValidationStatus
    answer: str | None = Field(default=None, max_length=MAX_FINAL_ANSWER_LENGTH)
    citations: tuple[AgentCitationRef, ...] = Field(default=(), max_length=MAX_FINAL_CITATIONS)
    latency_ms: float = Field(ge=0)
    error_code: str | None = None
    validated_citation_count: int = Field(default=0, ge=0)
    unsupported_citation_count: int = Field(default=0, ge=0)
    failed_tool_reference_count: int = Field(default=0, ge=0)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("latency_ms")
    @classmethod
    def _validation_latency_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("latency_ms must be finite")
        return value

    @field_validator("metadata")
    @classmethod
    def _validation_metadata_must_be_safe(cls, value: dict[str, object]) -> dict[str, object]:
        _validate_safe_summary_mapping(value)
        return value

    @model_validator(mode="after")
    def _status_and_answer_must_be_consistent(self) -> FinalAnswerValidationResult:
        if self.status in ("valid", "degraded"):
            if self.answer is None or not self.answer.strip():
                raise ValueError("valid or degraded validation requires a safe answer")
            return self
        if self.answer is not None:
            raise ValueError("invalid validation must not include an answer")
        return self


class ToolCallCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_run_id: str
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    tool_name: str
    permission: str | None
    status: ToolCallStorageStatus
    latency_ms: float = Field(ge=0)
    error_code: str | None = None
    arguments_summary: dict[str, object] = Field(default_factory=dict)
    result_summary: dict[str, object] = Field(default_factory=dict)

    @field_validator(
        "agent_run_id",
        "request_id",
        "trace_id",
        "tenant_id",
        "user_id",
        "tool_name",
    )
    @classmethod
    def _required_identifier_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("permission", "error_code")
    @classmethod
    def _optional_text_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("arguments_summary", "result_summary")
    @classmethod
    def _summary_must_be_safe(cls, value: dict[str, object]) -> dict[str, object]:
        _validate_safe_summary_mapping(value)
        return value


class ToolCallRecord(ToolCallCreate):
    id: str
    created_at: datetime
    updated_at: datetime

    @field_validator("id")
    @classmethod
    def _id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized


class ToolCallQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    user_id: str | None = None
    agent_run_id: str | None = None
    tool_name: str | None = None
    status: ToolCallStorageStatus | None = None
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None
    limit: int = Field(default=100, gt=0, le=500)

    @field_validator("tenant_id")
    @classmethod
    def _tenant_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("user_id", "agent_run_id", "tool_name")
    @classmethod
    def _optional_identifier_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @model_validator(mode="after")
    def _created_at_window_must_be_ordered(self) -> ToolCallQuery:
        if (
            self.created_at_from is not None
            and self.created_at_to is not None
            and self.created_at_from > self.created_at_to
        ):
            raise ValueError("created_at_from must be before or equal to created_at_to")
        return self


class ToolCallRecorderPort(Protocol):
    async def record_tool_call(self, record: ToolCallCreate) -> None: ...


class ToolCallRepositoryPort(ToolCallRecorderPort, Protocol):
    async def create_tool_call(self, record: ToolCallCreate) -> ToolCallRecord: ...

    async def list_tool_calls(self, query: ToolCallQuery) -> list[ToolCallRecord]: ...

    async def list_by_agent_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_run_id: str,
    ) -> list[ToolCallRecord]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


def _validate_safe_summary_mapping(value: Mapping[str, object]) -> None:
    for key, item in value.items():
        if _is_forbidden_summary_key(str(key)):
            raise ValueError("summary contains unsafe key")
        _validate_safe_summary_value(item)


def _validate_safe_summary_value(value: object) -> None:
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("summary contains non-finite number")
        return
    if isinstance(value, str):
        if not _is_safe_summary_string(value):
            raise ValueError("summary contains unsafe string")
        return
    if isinstance(value, Mapping):
        _validate_safe_summary_mapping({str(key): item for key, item in value.items()})
        return
    if isinstance(value, list | tuple):
        for item in value:
            _validate_safe_summary_value(item)
        return
    raise ValueError("summary contains unsupported value")


def _is_safe_citation_identifier(value: str) -> bool:
    if not _SAFE_CITATION_IDENTIFIER_PATTERN.fullmatch(value):
        return False
    if _looks_like_absolute_path(value) or _looks_like_sensitive_value(value):
        return False
    return True


def _is_forbidden_summary_key(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return any(
        part in normalized or part.replace("_", "") in compact
        for part in _FORBIDDEN_SUMMARY_KEY_PARTS
    )


def _is_safe_summary_string(value: str) -> bool:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        return False
    if _looks_like_absolute_path(normalized):
        return False
    lowered = normalized.lower()
    if any(marker in lowered for marker in _SUMMARY_VALUE_SECRET_MARKERS):
        return False
    return bool(_SAFE_SUMMARY_IDENTIFIER_PATTERN.fullmatch(normalized))


class AgentRunCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: str = Field(min_length=1, max_length=4000)
    max_steps: int | None = Field(default=None, gt=0, le=20)
    max_tool_calls: int | None = Field(default=None, ge=0, le=20)
    timeout_seconds: float | None = Field(default=None, gt=0, le=120)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("input")
    @classmethod
    def _input_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("input must not be blank")
        return normalized

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_must_be_safe_mapping(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be an object")
        safe: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("metadata keys must be non-empty strings")
            if _metadata_key_is_forbidden(key):
                continue
            safe_value = _safe_metadata_scalar(item)
            if safe_value is not _DROP_VALUE:
                safe[key.strip()] = safe_value
        return safe

    @model_validator(mode="after")
    def _at_least_input_or_query(self) -> AgentRunCommand:
        return self


class AgentRunRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str | None = Field(default=None, min_length=1, max_length=4000)
    input: str | None = Field(default=None, min_length=1, max_length=4000)
    max_steps: int | None = Field(default=None, gt=0, le=20)
    max_tool_calls: int | None = Field(default=None, ge=0, le=20)
    timeout_seconds: float | None = Field(default=None, gt=0, le=120)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("input", "query")
    @classmethod
    def _optional_input_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("input must not be blank")
        return normalized

    @field_validator("timeout_seconds")
    @classmethod
    def _body_timeout_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _body_metadata_must_be_safe_mapping(cls, value: object) -> dict[str, object]:
        return AgentRunCommand._metadata_must_be_safe_mapping(value)

    @model_validator(mode="after")
    def _exactly_one_input(self) -> AgentRunRequestBody:
        if self.input is None and self.query is None:
            raise ValueError("input or query is required")
        if self.input is not None and self.query is not None:
            raise ValueError("provide only one of input or query")
        return self

    def to_command(self) -> AgentRunCommand:
        return AgentRunCommand(
            input=self.input if self.input is not None else self.query or "",
            max_steps=self.max_steps,
            max_tool_calls=self.max_tool_calls,
            timeout_seconds=self.timeout_seconds,
            metadata=self.metadata,
        )


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    created_by: str
    status: Literal["running"]
    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    input_summary: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("timeout_seconds")
    @classmethod
    def _create_timeout_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        return value


class AgentRunUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["completed", "stopped", "failed"]
    termination_reason: str
    steps_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    error_code: str | None = None
    latency_ms: float = Field(ge=0)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    created_by: str
    status: AgentRunStorageStatus
    max_steps: int
    max_tool_calls: int
    timeout_seconds: float
    steps_used: int = 0
    tool_calls_used: int = 0
    termination_reason: str | None = None
    error_code: str | None = None
    latency_ms: float | None = None
    input_summary: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_run_id: str
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    status: AgentRunStorageStatus
    termination_reason: str | None
    steps_used: int
    tool_calls_used: int
    final_answer: str | None = None
    final_citations: tuple[AgentCitationRef, ...] = ()
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_record(
        cls,
        record: AgentRunRecord,
        *,
        final_answer: str | None = None,
        final_citations: tuple[AgentCitationRef, ...] = (),
    ) -> AgentRunResponse:
        return cls(
            agent_run_id=record.id,
            request_id=record.request_id,
            trace_id=record.trace_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            status=record.status,
            termination_reason=record.termination_reason,
            steps_used=record.steps_used,
            tool_calls_used=record.tool_calls_used,
            final_answer=final_answer,
            final_citations=final_citations,
            error_code=record.error_code,
            created_at=record.created_at,
            updated_at=record.updated_at,
            metadata=dict(record.metadata),
        )


_DROP_VALUE = object()
_FORBIDDEN_METADATA_KEYS = {
    "absolute_path",
    "access_token",
    "answer",
    "api_key",
    "authorization",
    "content",
    "file_content",
    "file_path",
    "hidden_reasoning",
    "local_path",
    "messages",
    "password",
    "prompt",
    "query",
    "raw_output",
    "raw_tool_arguments",
    "raw_tool_output",
    "secret",
    "thought",
    "token",
    "tool_results",
}


def _metadata_key_is_forbidden(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return normalized in _FORBIDDEN_METADATA_KEYS or compact in {
        item.replace("_", "") for item in _FORBIDDEN_METADATA_KEYS
    }


def _safe_metadata_scalar(value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value if not isinstance(value, float) or math.isfinite(value) else _DROP_VALUE
    if isinstance(value, str):
        return value if len(value) <= 200 and not _looks_like_absolute_path(value) else _DROP_VALUE
    return _DROP_VALUE


def _looks_like_sensitive_value(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SUMMARY_VALUE_SECRET_MARKERS)


def _looks_like_absolute_path(value: str) -> bool:
    normalized = value.strip()
    return (
        normalized.startswith("/")
        or normalized.startswith("\\\\")
        or bool(re.match(r"^[A-Za-z]:[\\/]", normalized))
    )
