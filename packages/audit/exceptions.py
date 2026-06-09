from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

AUDIT_EXPLORER_FORBIDDEN = "AUDIT_EXPLORER_FORBIDDEN"
AUDIT_EXPLORER_INVALID_QUERY = "AUDIT_EXPLORER_INVALID_QUERY"
AUDIT_EXPLORER_STORAGE_READ_FAILED = "AUDIT_EXPLORER_STORAGE_READ_FAILED"
AUDIT_EXPLORER_EXPORT_FAILED = "AUDIT_EXPLORER_EXPORT_FAILED"


class AuditExplorerError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Audit explorer operation failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)
