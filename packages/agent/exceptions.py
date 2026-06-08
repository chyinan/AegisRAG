from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

TOOL_ALREADY_REGISTERED = "TOOL_ALREADY_REGISTERED"
TOOL_NOT_REGISTERED = "TOOL_NOT_REGISTERED"
TOOL_INPUT_VALIDATION_FAILED = "TOOL_INPUT_VALIDATION_FAILED"
TOOL_OUTPUT_VALIDATION_FAILED = "TOOL_OUTPUT_VALIDATION_FAILED"
TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
TOOL_RATE_LIMITED = "TOOL_RATE_LIMITED"
TOOL_TIMEOUT = "TOOL_TIMEOUT"
TOOL_HANDLER_FAILED = "TOOL_HANDLER_FAILED"
TOOL_CALL_AUDIT_FAILED = "TOOL_CALL_AUDIT_FAILED"
AGENT_RUN_FORBIDDEN = "AGENT_RUN_FORBIDDEN"
AGENT_RUN_FAILED = "AGENT_RUN_FAILED"
AGENT_RUN_STORAGE_FAILED = "AGENT_RUN_STORAGE_FAILED"


class AgentToolError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Tool operation failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)


class AgentRunError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Agent run operation failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)


def agent_run_storage_failed(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    run_id: str | None = None,
    reason: str,
) -> AgentRunError:
    details = {
        "request_id": request_id,
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "agent_run_id": run_id,
        "reason": reason,
        "error_code": AGENT_RUN_STORAGE_FAILED,
    }
    return AgentRunError(
        code=AGENT_RUN_STORAGE_FAILED,
        message="Agent run storage operation failed.",
        details={key: value for key, value in details.items() if value is not None},
        status_code=500,
    )
