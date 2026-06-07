from __future__ import annotations

from collections.abc import Mapping

from packages.common.logging import redact_mapping


class DomainError(Exception):
    code: str
    message: str
    details: dict[str, object]

    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        self.code = code
        self.message = message
        self.details = redact_mapping(details or {})
        self.status_code = status_code
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
