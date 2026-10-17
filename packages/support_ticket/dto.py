from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator

SupportTicketStatus = Literal["open", "in_progress", "resolved"]
SUPPORT_TICKET_TOOL_VERSION = "1"
SUPPORT_TICKET_CREATE_PERMISSION = "agent:tool:create_support_ticket"
SUPPORT_TICKET_READ_PERMISSION = "support:ticket:read"
SUPPORT_TICKET_MANAGE_PERMISSION = "support:ticket:manage"


class CreateSupportTicketInput(BaseModel):
    """Version-one contract. New incompatible fields require a new tool version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_reference: UUID4
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    priority: Literal["low", "normal", "high"] = "normal"

    @field_validator("title", "description")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text must not be blank")
        return normalized

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {"version": SUPPORT_TICKET_TOOL_VERSION, **self.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()


class CreateSupportTicketOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: UUID
    status: SupportTicketStatus


class SupportTicketRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: str
    tenant_id: str
    user_id: str
    created_by: str
    client_reference: str
    title: str
    description: str
    priority: Literal["low", "normal", "high"]
    status: SupportTicketStatus
    revision: int = Field(ge=1)
    tool_version: str
    created_at: datetime
    updated_at: datetime


class SupportTicketList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SupportTicketRecord, ...]


class SupportTicketStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(ge=1)
    status: Literal["in_progress", "resolved"]
