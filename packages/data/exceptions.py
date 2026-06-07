from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError


class DocumentUploadError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            status_code=status_code,
        )


class DocumentUploadForbiddenError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_UPLOAD_FORBIDDEN",
            message="Document upload permission is required.",
            details=details,
            status_code=403,
        )


class DocumentUploadUnsupportedTypeError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_UPLOAD_UNSUPPORTED_TYPE",
            message="Document upload type is not supported.",
            details=details,
            status_code=415,
        )


class DocumentUploadTooLargeError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_UPLOAD_TOO_LARGE",
            message="Document upload exceeds the configured size limit.",
            details=details,
            status_code=413,
        )


class DocumentUploadInvalidMetadataError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_UPLOAD_INVALID_METADATA",
            message="Document upload metadata is invalid.",
            details=details,
            status_code=400,
        )


class DocumentStorageWriteError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_STORAGE_WRITE_FAILED",
            message="Document storage write failed.",
            details=details,
            status_code=502,
        )


class DocumentStorageReadError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_STORAGE_READ_FAILED",
            message="Document storage read failed.",
            details=details,
            status_code=502,
        )


class IngestionJobEnqueueError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="INGESTION_JOB_ENQUEUE_FAILED",
            message="Ingestion job enqueue failed.",
            details=details,
            status_code=502,
        )


class EmbeddingJobEnqueueError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="EMBEDDING_JOB_ENQUEUE_FAILED",
            message="Embedding job enqueue failed.",
            details=details,
            status_code=502,
        )


class ObjectStorageConfigurationError(DocumentUploadError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="OBJECT_STORAGE_CONFIGURATION_ERROR",
            message="Object storage is not configured.",
            details=details,
            status_code=500,
        )


class DocumentLifecycleError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            status_code=status_code,
        )


class DocumentManageForbiddenError(DocumentLifecycleError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_MANAGE_FORBIDDEN",
            message="Document manage permission is required.",
            details=details,
            status_code=403,
        )


class DocumentNotFoundError(DocumentLifecycleError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_NOT_FOUND",
            message="Document was not found.",
            details=details,
            status_code=404,
        )


class DocumentVersionNotFoundError(DocumentLifecycleError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_VERSION_NOT_FOUND",
            message="Document version was not found.",
            details=details,
            status_code=404,
        )


class DocumentDeleteFailedError(DocumentLifecycleError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_DELETE_FAILED",
            message="Document deletion failed.",
            details=details,
            status_code=500,
        )


class DocumentIndexNotReadyError(DocumentLifecycleError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_INDEX_NOT_READY",
            message="Document version index is not ready.",
            details=details,
            status_code=409,
        )


class DocumentVersionInvalidStateError(DocumentLifecycleError):
    def __init__(self, details: Mapping[str, object] | None = None) -> None:
        super().__init__(
            code="DOCUMENT_VERSION_INVALID_STATE",
            message="Document version is in an invalid state for this operation.",
            details=details,
            status_code=409,
        )
