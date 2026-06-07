from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.common.logging import redact_mapping


class AuditStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class AuditResource(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    id: str
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("type", "id")
    @classmethod
    def _required_identifier_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return redact_mapping(value)


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    action: str
    resource: AuditResource
    status: AuditStatus
    latency_ms: float
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "trace_id", "tenant_id", "user_id", "action")
    @classmethod
    def _required_text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("latency_ms")
    @classmethod
    def _latency_must_not_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("latency_ms must not be negative")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return redact_mapping(value)


class AuditPort(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


class InMemoryAuditPort:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    async def record(self, event: AuditEvent) -> None:
        self._events.append(event)


FakeAuditPort = InMemoryAuditPort
