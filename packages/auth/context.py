from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class AuthContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_id: str
    roles: tuple[str, ...] = ()
    department: str | None = None
    permissions: tuple[str, ...] = ()

    @field_validator("user_id", "tenant_id")
    @classmethod
    def _required_identifier_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("roles", "permissions", mode="before")
    @classmethod
    def _normalize_tuple_values(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            candidates = value.split(",")
        elif isinstance(value, Mapping) or not isinstance(value, Iterable):
            raise ValueError("value must be a string or iterable of values")
        else:
            candidates = value
        return tuple(str(item).strip() for item in candidates if str(item).strip())

    @field_validator("department")
    @classmethod
    def _normalize_department(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
