from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

CHAT_SESSION_NOT_FOUND = "CHAT_SESSION_NOT_FOUND"
CHAT_MEMORY_FORBIDDEN = "CHAT_MEMORY_FORBIDDEN"
CHAT_MEMORY_STORAGE_FAILED = "CHAT_MEMORY_STORAGE_FAILED"
CHAT_MEMORY_INVALID_REQUEST = "CHAT_MEMORY_INVALID_REQUEST"
CHAT_MEMORY_BUDGET_EXCEEDED = "CHAT_MEMORY_BUDGET_EXCEEDED"


class ChatMemoryError(DomainError):
    pass


def chat_session_not_found(
    *,
    request_id: str,
    trace_id: str,
    tenant_id: str,
    user_id: str,
    session_id: str | None,
) -> ChatMemoryError:
    return ChatMemoryError(
        code=CHAT_SESSION_NOT_FOUND,
        message="Chat session was not found.",
        details=_safe_details(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            reason="session_unavailable",
        ),
        status_code=404,
    )


def chat_memory_forbidden(
    *,
    request_id: str,
    trace_id: str,
    tenant_id: str,
    user_id: str,
    session_id: str | None,
) -> ChatMemoryError:
    return ChatMemoryError(
        code=CHAT_MEMORY_FORBIDDEN,
        message="Chat memory access is forbidden.",
        details=_safe_details(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            reason="memory_forbidden",
        ),
        status_code=403,
    )


def chat_memory_invalid_request(
    *,
    request_id: str,
    trace_id: str,
    tenant_id: str,
    user_id: str,
    session_id: str | None = None,
    reason: str,
) -> ChatMemoryError:
    return ChatMemoryError(
        code=CHAT_MEMORY_INVALID_REQUEST,
        message="Chat memory request is invalid.",
        details=_safe_details(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            reason=reason,
        ),
        status_code=400,
    )


def chat_memory_storage_failed(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    reason: str = "storage_failed",
) -> ChatMemoryError:
    return ChatMemoryError(
        code=CHAT_MEMORY_STORAGE_FAILED,
        message="Chat memory storage operation failed.",
        details=_safe_details(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            reason=reason,
        ),
        status_code=500,
    )


def _safe_details(**values: object) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def safe_memory_error_details(details: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "request_id",
        "trace_id",
        "tenant_id",
        "user_id",
        "session_id",
        "reason",
        "error_code",
        "safe_counts",
    }
    return {key: value for key, value in details.items() if key in allowed}
