from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SENSITIVE_PARAMETER_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer_token",
    "client_secret",
    "credential",
    "document_content",
    "id_token",
    "private_key",
    "password",
    "prompt",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
}
_SENSITIVE_KEY_FRAGMENTS = (
    "accesstoken",
    "accesskey",
    "apikey",
    "apitoken",
    "authorization",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "credential",
    "idtoken",
    "jwttoken",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "secretkey",
    "sessiontoken",
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"\b(api[_\s-]?key|access[_\s-]?token|secret|password)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class QueuePayload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    job_type: str
    resource_id: str
    parameters: dict[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "trace_id", "tenant_id", "user_id", "job_type", "resource_id")
    @classmethod
    def _must_be_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be empty")
        _validate_safe_string_value(("identifier",), normalized)
        return normalized

    @model_validator(mode="after")
    def _reject_sensitive_or_non_summary_parameters(self) -> QueuePayload:
        _validate_parameters(self.parameters)
        return self


def _validate_parameters(parameters: dict[str, object]) -> None:
    for key, value in parameters.items():
        _validate_json_value((key,), value)


def _validate_json_value(path: tuple[str, ...], value: object) -> None:
    key = path[-1].strip().lower()
    compact_key = re.sub(r"[^a-z0-9]", "", key)
    if (
        key in _SENSITIVE_PARAMETER_KEYS
        or compact_key in _SENSITIVE_PARAMETER_KEYS
        or any(fragment in compact_key for fragment in _SENSITIVE_KEY_FRAGMENTS)
    ):
        raise ValueError(f"sensitive queue payload field is not allowed: {'.'.join(path)}")

    if value is None or isinstance(value, str | int | float | bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"queue payload number must be finite: {'.'.join(path)}")
        if isinstance(value, str):
            _validate_safe_string_value(path, value)
        return

    if isinstance(value, bytes):
        raise ValueError(f"queue payload field must be JSON serializable: {'.'.join(path)}")

    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value((*path, str(index)), item)
    elif isinstance(value, dict):
        for nested_key, item in value.items():
            if not isinstance(nested_key, str):
                nested_path = ".".join((*path, str(nested_key)))
                raise ValueError(f"queue payload object keys must be strings: {nested_path}")
            _validate_json_value((*path, nested_key), item)
    else:
        raise ValueError(f"queue payload field must be JSON serializable: {'.'.join(path)}")


def _looks_like_local_absolute_path(value: str) -> bool:
    normalized = value.strip()
    return (
        normalized.startswith("/")
        or normalized.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(normalized) is not None
    )


def _validate_safe_string_value(path: tuple[str, ...], value: str) -> None:
    if _looks_like_local_absolute_path(value):
        raise ValueError(f"local absolute paths are not allowed in queue payload: {'.'.join(path)}")
    if any(pattern.search(value) is not None for pattern in _SENSITIVE_VALUE_PATTERNS):
        raise ValueError(f"secret-like values are not allowed in queue payload: {'.'.join(path)}")
