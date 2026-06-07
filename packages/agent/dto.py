from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable, Coroutine, Mapping
from enum import StrEnum
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

ToolHandler: TypeAlias = Callable[..., Coroutine[Any, Any, object]]

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


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
