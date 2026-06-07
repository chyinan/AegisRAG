from __future__ import annotations

from collections.abc import Mapping
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

LLMRole = Literal["system", "user", "assistant"]


class LLMMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: LLMRole
    name: str | None = None
    content: str

    @field_validator("name")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("content")
    @classmethod
    def _content_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must not be blank")
        return normalized


class GenerateRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    messages: tuple[LLMMessage, ...]
    provider: str
    model: str
    timeout_seconds: float
    retry_budget: int
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    session_id: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    stream_options: Mapping[str, object] = Field(default_factory=FrozenDict)
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator("messages", mode="before")
    @classmethod
    def _messages_must_be_dtos(cls, value: object) -> tuple[LLMMessage, ...]:
        if not isinstance(value, tuple | list):
            raise ValueError("messages must be a tuple or list of LLMMessage DTOs")
        messages = tuple(value)
        if not messages:
            raise ValueError("messages must not be empty")
        if not all(isinstance(message, LLMMessage) for message in messages):
            raise ValueError("messages must contain only LLMMessage DTOs")
        return messages

    @field_validator("provider", "model", "request_id", "trace_id", "tenant_id", "user_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("session_id")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        return value

    @field_validator("retry_budget")
    @classmethod
    def _retry_budget_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retry_budget must not be negative")
        return value

    @field_validator("temperature")
    @classmethod
    def _temperature_range(cls, value: float | None) -> float | None:
        if value is not None and (value < 0.0 or value > 2.0):
            raise ValueError("temperature must be between 0 and 2")
        return value

    @field_validator("max_output_tokens")
    @classmethod
    def _max_output_tokens_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("max_output_tokens must be greater than 0")
        return value

    @field_validator("stream_options", "metadata", mode="before")
    @classmethod
    def _safe_mapping(cls, value: object) -> FrozenDict:
        return _safe_metadata(value)

    @field_serializer("stream_options", "metadata")
    def _serialize_mapping(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    @field_validator("input_tokens", "output_tokens", "total_tokens")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("token usage values must not be negative")
        return value


class GenerationMetadata(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    provider: str
    model: str
    version: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: float
    finish_reason: str
    error_code: str | None = None
    chunk_count: int | None = None
    token_count: int | None = None
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator(
        "request_id",
        "trace_id",
        "tenant_id",
        "user_id",
        "provider",
        "model",
        "finish_reason",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("version", "error_code")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("latency_ms")
    @classmethod
    def _latency_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("latency_ms must not be negative")
        return value

    @field_validator("chunk_count", "token_count")
    @classmethod
    def _optional_count_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("count values must not be negative")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_mapping(cls, value: object) -> FrozenDict:
        return _safe_metadata(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class GenerateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    provider: str
    model: str
    version: str | None = None
    usage: TokenUsage
    latency_ms: float
    finish_reason: str
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    error_code: str | None = None
    metadata: GenerationMetadata

    @field_validator("text")
    @classmethod
    def _text_string(cls, value: str) -> str:
        return value

    @field_validator(
        "provider",
        "model",
        "finish_reason",
        "request_id",
        "trace_id",
        "tenant_id",
        "user_id",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("latency_ms")
    @classmethod
    def _latency_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("latency_ms must not be negative")
        return value


class GenerateChunkMetadata(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    provider: str
    model: str
    version: str | None = None
    chunk_count: int
    token_count: int
    error_code: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator(
        "request_id",
        "trace_id",
        "tenant_id",
        "user_id",
        "provider",
        "model",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("chunk_count", "token_count")
    @classmethod
    def _count_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("count values must not be negative")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_mapping(cls, value: object) -> FrozenDict:
        return _safe_metadata(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class GenerateChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    delta: str
    index: int
    is_final: bool = False
    response: GenerateResponse | None = None
    metadata: GenerateChunkMetadata | None = None

    @field_validator("index")
    @classmethod
    def _index_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("index must not be negative")
        return value

    @model_validator(mode="after")
    def _validate_final_response_contract(self) -> GenerateChunk:
        if self.is_final and self.response is None:
            raise ValueError("final chunk must include response")
        if not self.is_final and self.response is not None:
            raise ValueError("non-final chunk must not include response")
        return self


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


def _safe_metadata(value: object) -> FrozenDict:
    if value is None:
        return FrozenDict()
    if isinstance(value, FrozenDict):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping")
    return FrozenDict(
        {
            str(key): item
            for key, item in value.items()
            if _is_safe_metadata_item(str(key), item)
        }
    )


def _is_safe_metadata_item(key: str, value: object) -> bool:
    normalized = key.lower()
    if normalized == "include_usage":
        return isinstance(value, bool)
    if normalized.endswith("_count") or normalized.endswith("_tokens"):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if normalized == "latency_ms":
        return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
    return False
