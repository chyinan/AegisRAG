from __future__ import annotations

from packages.common.errors import DomainError

DOCUMENT_PARSE_UNSUPPORTED_TYPE = "DOCUMENT_PARSE_UNSUPPORTED_TYPE"
DOCUMENT_PARSE_EMPTY_CONTENT = "DOCUMENT_PARSE_EMPTY_CONTENT"
DOCUMENT_PARSE_ENCODING_FAILED = "DOCUMENT_PARSE_ENCODING_FAILED"
DOCUMENT_PARSE_FAILED = "DOCUMENT_PARSE_FAILED"
DOCUMENT_CLEAN_EMPTY_CONTENT = "DOCUMENT_CLEAN_EMPTY_CONTENT"
DOCUMENT_CHUNK_CONFIG_INVALID = "DOCUMENT_CHUNK_CONFIG_INVALID"
DOCUMENT_CHUNK_EMPTY_CONTENT = "DOCUMENT_CHUNK_EMPTY_CONTENT"
DOCUMENT_CHUNK_FAILED = "DOCUMENT_CHUNK_FAILED"

TERMINAL_PARSE_ERROR_CODES = {
    DOCUMENT_PARSE_UNSUPPORTED_TYPE,
    DOCUMENT_PARSE_EMPTY_CONTENT,
    DOCUMENT_PARSE_ENCODING_FAILED,
}


class DocumentParseError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code=code, message=message, details=details)


class UnsupportedDocumentTypeError(DocumentParseError):
    def __init__(self, *, source_type: str) -> None:
        super().__init__(
            code=DOCUMENT_PARSE_UNSUPPORTED_TYPE,
            message="Document source type is not supported by parser registry.",
            details={"source_type": source_type},
        )


class EmptyDocumentContentError(DocumentParseError):
    def __init__(self) -> None:
        super().__init__(
            code=DOCUMENT_PARSE_EMPTY_CONTENT,
            message="Document content is empty.",
        )


class DocumentEncodingError(DocumentParseError):
    def __init__(self) -> None:
        super().__init__(
            code=DOCUMENT_PARSE_ENCODING_FAILED,
            message="Document content could not be decoded as UTF-8.",
        )


class GenericDocumentParseError(DocumentParseError):
    def __init__(self, *, details: dict[str, object] | None = None) -> None:
        super().__init__(
            code=DOCUMENT_PARSE_FAILED,
            message="Document parsing failed.",
            details=details,
        )


class DocumentCleanError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code=code, message=message, details=details)


class EmptyCleanedDocumentError(DocumentCleanError):
    def __init__(self) -> None:
        super().__init__(
            code=DOCUMENT_CLEAN_EMPTY_CONTENT,
            message="Document cleaning removed all content.",
        )


class DocumentChunkError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code=code, message=message, details=details)


class InvalidChunkConfigError(DocumentChunkError):
    def __init__(self, *, details: dict[str, object] | None = None) -> None:
        super().__init__(
            code=DOCUMENT_CHUNK_CONFIG_INVALID,
            message="Document chunker configuration is invalid.",
            details=details,
        )


class EmptyChunkContentError(DocumentChunkError):
    def __init__(self, *, details: dict[str, object] | None = None) -> None:
        super().__init__(
            code=DOCUMENT_CHUNK_EMPTY_CONTENT,
            message="Document chunking found no safe content to chunk.",
            details=details,
        )


class GenericDocumentChunkError(DocumentChunkError):
    def __init__(self, *, details: dict[str, object] | None = None) -> None:
        super().__init__(
            code=DOCUMENT_CHUNK_FAILED,
            message="Document chunking failed.",
            details=details,
        )
