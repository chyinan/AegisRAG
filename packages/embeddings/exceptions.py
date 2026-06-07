from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

EMBEDDING_PROVIDER_TIMEOUT = "EMBEDDING_PROVIDER_TIMEOUT"
EMBEDDING_PROVIDER_RATE_LIMITED = "EMBEDDING_PROVIDER_RATE_LIMITED"
EMBEDDING_PROVIDER_FAILED = "EMBEDDING_PROVIDER_FAILED"
EMBEDDING_VECTOR_DIMENSION_MISMATCH = "EMBEDDING_VECTOR_DIMENSION_MISMATCH"
EMBEDDING_BATCH_SIZE_MISMATCH = "EMBEDDING_BATCH_SIZE_MISMATCH"
EMBEDDING_VECTOR_EMPTY = "EMBEDDING_VECTOR_EMPTY"
EMBEDDING_JOB_NOT_FOUND = "EMBEDDING_JOB_NOT_FOUND"
EMBEDDING_JOB_INVALID_STATE = "EMBEDDING_JOB_INVALID_STATE"
EMBEDDING_JOB_PAYLOAD_MISMATCH = "EMBEDDING_JOB_PAYLOAD_MISMATCH"
EMBEDDING_DOCUMENT_VERSION_NOT_CHUNKED = "EMBEDDING_DOCUMENT_VERSION_NOT_CHUNKED"
EMBEDDING_CHUNKS_NOT_FOUND = "EMBEDDING_CHUNKS_NOT_FOUND"
EMBEDDING_CHUNK_SNAPSHOT_MISMATCH = "EMBEDDING_CHUNK_SNAPSHOT_MISMATCH"


class EmbeddingJobError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Embedding job failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)


class EmbeddingProviderError(EmbeddingJobError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Embedding provider failed.",
        retryable: bool,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.retryable = retryable
        super().__init__(code=code, message=message, details=details)
