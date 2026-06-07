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
from packages.common.logging import redact_mapping
from packages.rag.dto import Citation, UnsupportedClaim

RagStreamEventType = Literal["token", "citation", "error", "final", "tool_call", "tool_result"]
StreamStatus = Literal["success", "error"]


class _BaseStreamPayload(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    request_id: str
    trace_id: str

    @field_validator("request_id", "trace_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class TokenEventPayload(_BaseStreamPayload):
    event: Literal["token"] = "token"
    index: int = Field(ge=0)
    delta: str


class CitationEventPayload(_BaseStreamPayload):
    event: Literal["citation"] = "citation"
    citation: Citation


class ErrorEventPayload(_BaseStreamPayload):
    event: Literal["error"] = "error"
    code: str
    message: str
    details: Mapping[str, object] = Field(default_factory=FrozenDict)
    terminal: bool = True

    @field_validator("code", "message")
    @classmethod
    def _required_error_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("details", mode="before")
    @classmethod
    def _safe_details(cls, value: object) -> FrozenDict:
        if value is None:
            return FrozenDict()
        if isinstance(value, FrozenDict):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("details must be a mapping")
        return FrozenDict(redact_mapping(value))

    @field_serializer("details")
    def _serialize_details(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class FinalEventPayload(_BaseStreamPayload):
    event: Literal["final"] = "final"
    status: StreamStatus = "success"
    session_id: str | None = None
    tenant_id: str
    user_id: str
    answer: str
    citations: tuple[Citation, ...] = ()
    no_answer: bool = False
    unsupported_claims: tuple[UnsupportedClaim, ...] = ()
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator("tenant_id", "user_id")
    @classmethod
    def _required_identity_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("session_id")
    @classmethod
    def _optional_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("metadata", mode="before")
    @classmethod
    def _safe_metadata(cls, value: object) -> FrozenDict:
        if value is None:
            return FrozenDict()
        if isinstance(value, FrozenDict):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return FrozenDict(redact_mapping(value))

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class ToolCallEventPayload(_BaseStreamPayload):
    event: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    tool_name: str
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator("tool_call_id", "tool_name")
    @classmethod
    def _required_tool_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def _safe_metadata(cls, value: object) -> FrozenDict:
        if value is None:
            return FrozenDict()
        if isinstance(value, FrozenDict):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return FrozenDict(redact_mapping(value))

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class ToolResultEventPayload(_BaseStreamPayload):
    event: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    tool_name: str
    status: Literal["success", "error"]
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_validator("tool_call_id", "tool_name")
    @classmethod
    def _required_tool_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def _safe_metadata(cls, value: object) -> FrozenDict:
        if value is None:
            return FrozenDict()
        if isinstance(value, FrozenDict):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return FrozenDict(redact_mapping(value))

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


RagStreamPayload = (
    TokenEventPayload
    | CitationEventPayload
    | ErrorEventPayload
    | FinalEventPayload
    | ToolCallEventPayload
    | ToolResultEventPayload
)


class RagStreamEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: RagStreamEventType
    payload: RagStreamPayload

    @model_validator(mode="after")
    def _event_must_match_payload(self) -> RagStreamEvent:
        if self.event != self.payload.event:
            raise ValueError("stream event type must match payload event")
        return self


def format_sse_event(event: RagStreamEvent) -> str:
    if not event.payload.request_id:
        raise ValueError("stream event payload request_id must not be blank")
    return f"event: {event.event}\ndata: {event.payload.model_dump_json()}\n\n"


def safe_error_event(
    *,
    request_id: str,
    trace_id: str,
    code: str,
    message: str,
    details: Mapping[str, object] | None = None,
    terminal: bool = True,
) -> RagStreamEvent:
    return RagStreamEvent(
        event="error",
        payload=ErrorEventPayload(
            request_id=request_id,
            trace_id=trace_id,
            code=code,
            message=message,
            details=details or {},
            terminal=terminal,
        ),
    )


def token_event(
    *,
    request_id: str,
    trace_id: str,
    index: int,
    delta: str,
) -> RagStreamEvent:
    return RagStreamEvent(
        event="token",
        payload=TokenEventPayload(
            request_id=request_id,
            trace_id=trace_id,
            index=index,
            delta=delta,
        ),
    )


def citation_event(
    *,
    request_id: str,
    trace_id: str,
    citation: Citation,
) -> RagStreamEvent:
    return RagStreamEvent(
        event="citation",
        payload=CitationEventPayload(
            request_id=request_id,
            trace_id=trace_id,
            citation=citation,
        ),
    )


def final_event(
    *,
    request_id: str,
    trace_id: str,
    tenant_id: str,
    user_id: str,
    answer: str,
    citations: tuple[Citation, ...],
    no_answer: bool,
    unsupported_claims: tuple[UnsupportedClaim, ...],
    metadata: Mapping[str, object],
    status: StreamStatus = "success",
    session_id: str | None = None,
) -> RagStreamEvent:
    return RagStreamEvent(
        event="final",
        payload=FinalEventPayload(
            request_id=request_id,
            trace_id=trace_id,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            answer=answer,
            citations=citations,
            no_answer=no_answer,
            unsupported_claims=unsupported_claims,
            metadata=metadata,
            status=status,
        ),
    )
