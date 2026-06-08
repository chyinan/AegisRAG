from __future__ import annotations

import logging as std_logging
import os
import re
from collections.abc import Mapping
from typing import Final, Protocol, cast

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from packages.common.context import RequestContext

REQUEST_COMPLETED_EVENT: Final = "api.request.completed"
READINESS_CHECKED_EVENT: Final = "api.readiness.checked"
REDACTED_VALUE: Final = "[REDACTED]"

REQUEST_LOG_FIELDS: Final[tuple[str, ...]] = (
    "event",
    "request_id",
    "trace_id",
    "tenant_id",
    "user_id",
    "session_id",
    "method",
    "path",
    "status_code",
    "latency_ms",
    "error_code",
    "role_count",
    "permission_count",
)
SENSITIVE_KEYWORDS: Final[tuple[str, ...]] = (
    "authorization",
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "cookie",
    "private_key",
    "privatekey",
)
SENSITIVE_CONTENT_KEYS: Final[tuple[str, ...]] = (
    "body",
    "request_body",
    "response_body",
    "query",
    "full_query",
    "user_message",
    "assistant_message",
    "message_content",
    "answer",
    "assistant_answer",
    "prompt",
    "content",
    "document_content",
    "document_chunk",
    "chunk_content",
    "chunk_text",
    "sql",
    "sql_query",
    "tsquery",
    "tsvector",
    "vector",
    "query_vector",
    "embedding",
    "embedding_vector",
    "raw_response",
    "provider_raw_response",
    "provider_raw_payload",
    "tool_args",
    "tool_params",
    "path",
    "file_path",
    "absolute_path",
    "local_path",
    "bucket",
    "bucket_path",
    "object_key",
    "source_uri",
    "storage_key",
    "storage_locator",
    "uri",
    "url",
)
SAFE_OBSERVABILITY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "token_usage",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }
)
SENSITIVE_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"\b(api[_\s-]?key|access[_\s-]?token|secret|password)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s]+"),
    re.compile(r"\\\\[^\s]+"),
    re.compile(r"/(?:home|Users|etc|var|opt)/[^\s]+"),
    re.compile(r"\b(?:file|s3|minio)://[^\s]+", re.IGNORECASE),
    re.compile(
        r"\bhttps?://[^\s?]+/[^\s]*\?[^\s]*(?:token|secret|api[_-]?key)[^\s]*",
        re.IGNORECASE,
    ),
)


class StructuredLogger(Protocol):
    def info(self, event: str, **kwargs: object) -> object: ...


def configure_logging(log_level: str | None = None) -> None:
    level_name = log_level if log_level is not None else os.getenv("LOG_LEVEL") or "INFO"
    level = _log_level(level_name)
    std_logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_request_logger() -> StructuredLogger:
    return cast(StructuredLogger, structlog.stdlib.get_logger("apps.api.request"))


def get_readiness_logger() -> StructuredLogger:
    return cast(StructuredLogger, structlog.stdlib.get_logger("apps.api.readiness"))


def bind_request_context(context: RequestContext) -> None:
    clear_contextvars()
    bind_contextvars(
        request_id=context.request_id,
        trace_id=context.trace_id,
        session_id=context.session_id,
    )


def clear_log_context() -> None:
    clear_contextvars()


def log_structured_event(logger: StructuredLogger, event: Mapping[str, object]) -> None:
    event_name = str(event.get("event", "event"))
    payload = {key: value for key, value in event.items() if key != "event"}
    logger.info(event_name, **payload)


def build_request_log_event(
    *,
    context: RequestContext,
    tenant_id: str | None,
    user_id: str | None,
    method: str,
    path: str,
    status_code: int,
    latency_ms: float,
    error_code: str | None,
    role_count: int | None = None,
    permission_count: int | None = None,
) -> dict[str, object]:
    return {
        "event": REQUEST_COMPLETED_EVENT,
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "session_id": context.session_id,
        "method": method,
        "path": path,
        "status_code": status_code,
        "latency_ms": round(latency_ms, 3),
        "error_code": error_code,
        "role_count": role_count,
        "permission_count": permission_count,
    }


def redact_mapping(data: Mapping[str, object]) -> dict[str, object]:
    redacted = redact_sensitive_data(data)
    if not isinstance(redacted, dict):
        return {}
    return redacted


def redact_sensitive_data(value: object) -> object:
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key_text] = REDACTED_VALUE
            else:
                redacted[key_text] = redact_sensitive_data(item)
        return redacted
    if isinstance(value, list | tuple):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str) and _contains_sensitive_value(value):
        return REDACTED_VALUE
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in SAFE_OBSERVABILITY_KEYS:
        return False
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    compact_content_keys = {re.sub(r"[^a-z0-9]", "", item) for item in SENSITIVE_CONTENT_KEYS}
    return (
        any(keyword in normalized or keyword in compact for keyword in SENSITIVE_KEYWORDS)
        or normalized in SENSITIVE_CONTENT_KEYS
        or compact in compact_content_keys
    )


def _contains_sensitive_value(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in SENSITIVE_VALUE_PATTERNS)


def _log_level(value: str) -> int:
    normalized = value.strip().upper()
    level = getattr(std_logging, normalized, std_logging.INFO)
    if isinstance(level, int):
        return level
    return std_logging.INFO
