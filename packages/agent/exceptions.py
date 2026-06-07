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
