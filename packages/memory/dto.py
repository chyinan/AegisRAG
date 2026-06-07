from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
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

ChatSessionStatus = Literal["active", "closed", "deleted"]
ChatMessageStatus = Literal["active", "deleted"]
ChatMessageRole = Literal["user", "assistant", "system_summary"]

MAX_MESSAGE_CONTENT_CHARS = 4000
MAX_MESSAGE_SUMMARY_CHARS = 512
MAX_SESSION_TITLE_CHARS = 120


class ChatMemoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_messages: int = 10
    max_history_tokens: int = 1000
    max_message_chars: int = MAX_MESSAGE_CONTENT_CHARS
    max_summary_chars: int = MAX_MESSAGE_SUMMARY_CHARS
    allowed_roles: tuple[ChatMessageRole, ...] = ("user", "assistant", "system_summary")

    @field_validator("max_messages", "max_history_tokens", "max_message_chars", "max_summary_chars")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be greater than 0")
        return value

    @field_validator("allowed_roles")
    @classmethod
    def _roles_required(cls, value: tuple[ChatMessageRole, ...]) -> tuple[ChatMessageRole, ...]:
        if not value:
            raise ValueError("allowed_roles must contain at least one role")
        return value


class ChatSessionCreate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    created_by: str
    title: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator("request_id", "trace_id", "tenant_id", "user_id", "created_by")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        title = _optional_text(value)
        if title is not None and len(title) > MAX_SESSION_TITLE_CHARS:
            raise ValueError(f"title must be less than or equal to {MAX_SESSION_TITLE_CHARS} chars")
        return title

    @field_validator("metadata", mode="before")
    @classmethod
    def _mapping(cls, value: object) -> FrozenDict:
        return _frozen_mapping(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class ChatSessionRecord(ChatSessionCreate):
    id: str
    status: ChatSessionStatus
    last_message_at: datetime | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    @field_validator("id")
    @classmethod
    def _id_required(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("message_count")
    @classmethod
    def _non_negative_count(cls, value: int) -> int:
        return _non_negative_int(value, field_name="message_count")


class ChatMessageCreate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    session_id: str
    role: ChatMessageRole
    content: str
    content_summary: str
    token_count: int
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator("request_id", "trace_id", "tenant_id", "user_id", "session_id")
    @classmethod
    def _required_identity(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        normalized = _required_text(value)
        if len(normalized) > MAX_MESSAGE_CONTENT_CHARS:
            raise ValueError(
                f"content must be less than or equal to {MAX_MESSAGE_CONTENT_CHARS} chars"
            )
        return normalized

    @field_validator("content_summary")
    @classmethod
    def _summary(cls, value: str) -> str:
        normalized = _required_text(value)
        if len(normalized) > MAX_MESSAGE_SUMMARY_CHARS:
            raise ValueError(
                f"content_summary must be less than or equal to {MAX_MESSAGE_SUMMARY_CHARS} chars"
            )
        return normalized

    @field_validator("token_count")
    @classmethod
    def _token_count(cls, value: int) -> int:
        return _non_negative_int(value, field_name="token_count")

    @field_validator("metadata", mode="before")
    @classmethod
    def _mapping(cls, value: object) -> FrozenDict:
        return _frozen_mapping(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class ChatMessageRecord(ChatMessageCreate):
    id: str
    created_by: str
    status: ChatMessageStatus
    sequence_no: int
    created_at: datetime
    updated_at: datetime

    @field_validator("id", "created_by")
    @classmethod
    def _required_text_field(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("sequence_no")
    @classmethod
    def _sequence_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("sequence_no must be greater than 0")
        return value


class ChatHistoryMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: ChatMessageRole
    content: str
    token_count: int
    sequence_no: int

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("token_count")
    @classmethod
    def _token_count(cls, value: int) -> int:
        return _non_negative_int(value, field_name="token_count")

    @field_validator("sequence_no")
    @classmethod
    def _sequence_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("sequence_no must be greater than 0")
        return value


class PackedChatHistory(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    session_id: str
    messages: tuple[ChatHistoryMessage, ...] = ()
    message_count: int = 0
    used_count: int = 0
    dropped_count: int = 0
    token_count: int = 0
    safe_counts: Mapping[str, int] = Field(default_factory=dict)

    @field_validator("session_id")
    @classmethod
    def _session_id_required(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("message_count", "used_count", "dropped_count", "token_count")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        return _non_negative_int(value, field_name="value")

    @field_validator("safe_counts", mode="before")
    @classmethod
    def _safe_counts(cls, value: object) -> FrozenDict:
        counts = _frozen_mapping(value)
        derived = {
            "memory_message_count": 0,
            "memory_used_count": 0,
            "memory_dropped_count": 0,
            "memory_token_count": 0,
            **dict(counts),
        }
        return FrozenDict(
            {
                str(key): item
                for key, item in derived.items()
                if isinstance(item, int) and not isinstance(item, bool)
            }
        )

    @field_serializer("safe_counts")
    def _serialize_safe_counts(self, value: Mapping[str, int]) -> dict[str, int]:
        return dict(value)

    @model_validator(mode="after")
    def _derive_safe_counts(self) -> PackedChatHistory:
        counts = {
            "memory_message_count": self.message_count,
            "memory_used_count": self.used_count,
            "memory_dropped_count": self.dropped_count,
            "memory_token_count": self.token_count,
            **dict(self.safe_counts),
        }
        counts["memory_message_count"] = self.message_count
        counts["memory_used_count"] = self.used_count
        counts["memory_dropped_count"] = self.dropped_count
        counts["memory_token_count"] = self.token_count
        object.__setattr__(self, "safe_counts", FrozenDict(counts))
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


def _non_negative_int(value: int, *, field_name: str) -> int:
    if value < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return value


def _frozen_mapping(value: object) -> FrozenDict:
    if value is None:
        return FrozenDict()
    if isinstance(value, FrozenDict):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("value must be a mapping")
    return FrozenDict(value)
