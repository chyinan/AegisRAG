from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

RAG_CONTEXT_UNAUTHORIZED_CHUNK = "RAG_CONTEXT_UNAUTHORIZED_CHUNK"
RAG_CONTEXT_BUDGET_EXCEEDED = "RAG_CONTEXT_BUDGET_EXCEEDED"
RAG_CONTEXT_INVALID_CANDIDATE = "RAG_CONTEXT_INVALID_CANDIDATE"
RAG_CONTEXT_PACKING_FAILED = "RAG_CONTEXT_PACKING_FAILED"
RAG_PROMPT_INVALID_REQUEST = "RAG_PROMPT_INVALID_REQUEST"
RAG_PROMPT_CONTEXT_EMPTY = "RAG_PROMPT_CONTEXT_EMPTY"
RAG_PROMPT_INPUT_TOO_LARGE = "RAG_PROMPT_INPUT_TOO_LARGE"
RAG_PROMPT_BUILD_FAILED = "RAG_PROMPT_BUILD_FAILED"
RAG_GENERATION_INVALID_REQUEST = "RAG_GENERATION_INVALID_REQUEST"
RAG_GENERATION_FAILED = "RAG_GENERATION_FAILED"
RAG_CITATION_INVALID_SOURCE = "RAG_CITATION_INVALID_SOURCE"
RAG_CITATION_EXTRACTION_FAILED = "RAG_CITATION_EXTRACTION_FAILED"
RAG_QUERY_FORBIDDEN = "RAG_QUERY_FORBIDDEN"
RAG_QUERY_FAILED = "RAG_QUERY_FAILED"
RAG_QUERY_CONTEXT_UNAVAILABLE = "RAG_QUERY_CONTEXT_UNAVAILABLE"
RAG_QUERY_CLIENT_DISCONNECTED = "RAG_QUERY_CLIENT_DISCONNECTED"


class RagContextPackingError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "RAG context packing failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)


class RagPromptBuildError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "RAG prompt build failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)


class RagGenerationError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "RAG generation failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)


class RagCitationExtractionError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "RAG citation extraction failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)


class RagQueryError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "RAG query failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)
