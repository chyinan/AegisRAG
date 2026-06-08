from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

DIAGNOSTICS_FORBIDDEN = "DIAGNOSTICS_FORBIDDEN"
DIAGNOSTICS_NOT_FOUND = "DIAGNOSTICS_NOT_FOUND"
DIAGNOSTICS_INVALID_LOOKUP = "DIAGNOSTICS_INVALID_LOOKUP"
DIAGNOSTICS_STORAGE_READ_FAILED = "DIAGNOSTICS_STORAGE_READ_FAILED"


class DiagnosticsError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Diagnostics operation failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)
