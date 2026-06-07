from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TenantRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    status: str
    created_by: str | None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class UserRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    status: str
    created_by: str | None
    external_id: str | None
    email: str | None
    display_name: str
    department: str | None
    created_at: datetime
    updated_at: datetime


class RoleRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    status: str
    created_by: str | None
    name: str
    description: str | None
    permissions: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime


class UserRoleRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    status: str
    created_by: str | None
    user_id: str
    role_id: str
    created_at: datetime
    updated_at: datetime
