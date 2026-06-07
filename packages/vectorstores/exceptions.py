from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

INDEX_DIMENSION_MISMATCH = "INDEX_DIMENSION_MISMATCH"
VECTOR_STORE_WRITE_FAILED = "VECTOR_STORE_WRITE_FAILED"
VECTOR_STORE_SEARCH_FAILED = "VECTOR_STORE_SEARCH_FAILED"
VECTOR_STORE_DELETE_FAILED = "VECTOR_STORE_DELETE_FAILED"
VECTOR_RECORD_SCOPE_MISMATCH = "VECTOR_RECORD_SCOPE_MISMATCH"


class VectorStoreError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Vector store operation failed.",
        retryable: bool = True,
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        self.retryable = retryable
        super().__init__(code=code, message=message, details=details, status_code=status_code)
