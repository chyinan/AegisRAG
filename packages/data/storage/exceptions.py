from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError


class StorageError(DomainError):
    def __init__(
        self,
        *,
        code: str = "STORAGE_ERROR",
        message: str = "Storage operation failed.",
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(code=code, message=message, details=details)


class StorageConfigurationError(StorageError):
    def __init__(self, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="STORAGE_CONFIGURATION_ERROR",
            message="Database storage is not configured.",
            details=details,
        )
