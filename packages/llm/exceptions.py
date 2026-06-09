from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

LLM_PROVIDER_TIMEOUT = "LLM_PROVIDER_TIMEOUT"
LLM_PROVIDER_RATE_LIMITED = "LLM_PROVIDER_RATE_LIMITED"
LLM_PROVIDER_FAILED = "LLM_PROVIDER_FAILED"
LLM_PROVIDER_AUTH_FAILED = "LLM_PROVIDER_AUTH_FAILED"
LLM_PROVIDER_MALFORMED_RESPONSE = "LLM_PROVIDER_MALFORMED_RESPONSE"
LLM_GENERATION_INVALID_REQUEST = "LLM_GENERATION_INVALID_REQUEST"
LLM_STREAM_FAILED = "LLM_STREAM_FAILED"


class LLMProviderError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "LLM provider failed.",
        retryable: bool,
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        self.retryable = retryable
        super().__init__(
            code=code,
            message=message,
            details=_safe_error_details(details),
            status_code=status_code,
        )


def _safe_error_details(details: Mapping[str, object] | None) -> dict[str, object]:
    if details is None:
        return {}
    safe: dict[str, object] = {}
    for key, value in details.items():
        normalized = str(key)
        if normalized in {
            "request_id",
            "trace_id",
            "tenant_id",
            "user_id",
            "provider",
            "model",
            "version",
            "error_code",
        } and isinstance(value, str):
            safe[normalized] = value
        elif (normalized.endswith("_count") or normalized.endswith("_tokens")) and (
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
        ):
            safe[normalized] = value
    return safe
