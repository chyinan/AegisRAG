from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from packages.auth.policies import FrozenDict
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.dto import ChunkRecord, DocumentRecord, DocumentVersionRecord
from packages.rag.access import acl_allows_auth

SOURCE_ACCESS_DENIED = "SOURCE_ACCESS_DENIED"
SOURCE_REFERENCE_INVALID = "SOURCE_REFERENCE_INVALID"
SOURCE_RESOLVE_FAILED = "SOURCE_RESOLVE_FAILED"
VISIBLE_VERSION_STATUSES = frozenset(
    {
        "retrieval_ready",
    }
)


class SourceResolveError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Source reference cannot be resolved.",
        details: Mapping[str, object] | None = None,
        status_code: int = 404,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)


class SourceResolveCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    request_id: str | None = None
    citation_ref: str | None = None

    @field_validator("document_id", "version_id", "chunk_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("request_id", "citation_ref")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_page_range(self) -> SourceResolveCommand:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class SourceResolveRequestBody(SourceResolveCommand):
    def to_command(self) -> SourceResolveCommand:
        return SourceResolveCommand(**self.model_dump())


class SourceResolveResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    request_id: str
    trace_id: str
    document_id: str
    version_id: str
    chunk_id: str
    source: str | None = None
    source_uri: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    text_excerpt: str
    excerpt_char_count: int
    token_count: int
    retrieval_method: str | None = None
    score: float | None = None
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class SourceResolveRepository(Protocol):
    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentRecord | None: ...

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None: ...

    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None: ...


class SourceCitationMetadataRepository(Protocol):
    async def get_source_citation_metadata(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str | None,
        citation_ref: str | None,
        document_id: str,
        version_id: str,
        chunk_id: str,
    ) -> Mapping[str, object] | None: ...


class SourceResolveService:
    def __init__(
        self,
        *,
        repository: SourceResolveRepository,
        audit: AuditPort,
        citation_metadata_repository: SourceCitationMetadataRepository | None = None,
        max_excerpt_chars: int = 2000,
    ) -> None:
        if max_excerpt_chars <= 0:
            raise ValueError("max_excerpt_chars must be greater than 0")
        self._repository = repository
        self._citation_metadata_repository = citation_metadata_repository
        self._audit = audit
        self._max_excerpt_chars = max_excerpt_chars

    async def resolve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: SourceResolveCommand,
    ) -> SourceResolveResponse:
        started = time.perf_counter()
        denial_reason: str | None = "unknown"
        try:
            document = await self._repository.get_document(
                tenant_id=context.auth.tenant_id,
                document_id=command.document_id,
            )
            denial_reason = _document_denial_reason(document)
            if denial_reason is not None:
                raise _safe_denial(context=context)

            version = await self._repository.get_version(
                tenant_id=context.auth.tenant_id,
                version_id=command.version_id,
            )
            denial_reason = _version_denial_reason(command=command, version=version)
            if denial_reason is not None:
                raise _safe_denial(context=context)

            chunk = await self._repository.get_chunk(
                tenant_id=context.auth.tenant_id,
                document_id=command.document_id,
                version_id=command.version_id,
                chunk_id=command.chunk_id,
            )
            denial_reason = _chunk_denial_reason(
                context=context,
                command=command,
                chunk=chunk,
                document=document,
                version=version,
            )
            if denial_reason is not None:
                raise _safe_denial(context=context)

            assert document is not None
            assert chunk is not None
            citation_metadata = await self._source_citation_metadata(
                context=context,
                command=command,
            )
            response = _response_from_records(
                context=context,
                document=document,
                chunk=chunk,
                citation_metadata=citation_metadata,
                max_excerpt_chars=self._max_excerpt_chars,
            )
            await self._record_audit(
                context=context,
                command=command,
                response=response,
                started=started,
                status=AuditStatus.SUCCESS,
                authorized=True,
                denial_reason=None,
                error_code=None,
            )
            return response
        except SourceResolveError as exc:
            await self._record_audit(
                context=context,
                command=command,
                response=None,
                started=started,
                status=(
                    AuditStatus.DENIED
                    if exc.code == SOURCE_ACCESS_DENIED
                    else AuditStatus.FAILURE
                ),
                authorized=False,
                denial_reason=denial_reason,
                error_code=exc.code,
            )
            raise
        except Exception as exc:
            error = SourceResolveError(
                code=SOURCE_RESOLVE_FAILED,
                message="Source resolve failed.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "error_code": SOURCE_RESOLVE_FAILED,
                },
                status_code=500,
            )
            await self._record_audit(
                context=context,
                command=command,
                response=None,
                started=started,
                status=AuditStatus.FAILURE,
                authorized=False,
                denial_reason="resolve_failed",
                error_code=error.code,
            )
            raise error from exc

    async def _source_citation_metadata(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: SourceResolveCommand,
    ) -> Mapping[str, object] | None:
        repository = self._citation_metadata_repository
        if repository is None:
            return None
        return await repository.get_source_citation_metadata(
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            request_id=command.request_id or context.request_id,
            citation_ref=command.citation_ref,
            document_id=command.document_id,
            version_id=command.version_id,
            chunk_id=command.chunk_id,
        )

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: SourceResolveCommand,
        response: SourceResolveResponse | None,
        started: float,
        status: AuditStatus,
        authorized: bool,
        denial_reason: str | None,
        error_code: str | None,
    ) -> None:
        metadata: dict[str, object] = {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "document_id": command.document_id,
            "version_id": command.version_id,
            "chunk_id": command.chunk_id,
            "citation_ref": command.citation_ref,
            "authorized": authorized,
            "denial_reason": denial_reason,
            "source_type": response.source_type if response is not None else None,
            "excerpt_char_count": response.excerpt_char_count if response is not None else 0,
            "error_code": error_code,
        }
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="rag.source.resolve",
                resource=AuditResource(
                    type="source_reference",
                    id=command.citation_ref or command.chunk_id,
                    metadata=metadata,
                ),
                status=status,
                latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
                error_code=error_code,
                metadata=metadata,
                created_at=datetime.now(tz=UTC),
            )
        )


def _document_denial_reason(document: DocumentRecord | None) -> str | None:
    if document is None:
        return "document_unavailable"
    if document.deleted_at is not None or document.status == "deleted":
        return "document_unavailable"
    return None


def _version_denial_reason(
    *,
    command: SourceResolveCommand,
    version: DocumentVersionRecord | None,
) -> str | None:
    if version is None:
        return "version_unavailable"
    if version.document_id != command.document_id:
        return "version_unavailable"
    if version.deleted_at is not None or version.status == "deleted":
        return "version_unavailable"
    if version.status not in VISIBLE_VERSION_STATUSES:
        return "version_unavailable"
    return None


def _chunk_denial_reason(
    *,
    context: AuthenticatedRequestContext,
    command: SourceResolveCommand,
    chunk: ChunkRecord | None,
    document: DocumentRecord | None,
    version: DocumentVersionRecord | None,
) -> str | None:
    if chunk is None:
        return "chunk_unavailable"
    if (
        chunk.tenant_id != context.auth.tenant_id
        or chunk.document_id != command.document_id
        or chunk.version_id != command.version_id
        or chunk.chunk_id != command.chunk_id
    ):
        return "identity_mismatch"
    if chunk.status != "active" or chunk.deleted_at is not None:
        return "chunk_unavailable"
    if command.page_start is not None and (
        chunk.page_start != command.page_start or chunk.page_end != command.page_end
    ):
        return "identity_mismatch"
    if document is not None and not acl_allows_auth(document.acl, context.auth):
        return "acl_denied"
    if version is not None and not acl_allows_auth(version.acl, context.auth):
        return "acl_denied"
    if not acl_allows_auth(chunk.acl, context.auth):
        return "acl_denied"
    return None


def _response_from_records(
    *,
    context: AuthenticatedRequestContext,
    document: DocumentRecord,
    chunk: ChunkRecord,
    citation_metadata: Mapping[str, object] | None,
    max_excerpt_chars: int,
) -> SourceResolveResponse:
    excerpt = chunk.content[:max_excerpt_chars]
    metadata = _safe_metadata(chunk.metadata)
    citation_metadata = citation_metadata or {}
    return SourceResolveResponse(
        request_id=context.request_id,
        trace_id=context.trace_id,
        document_id=chunk.document_id,
        version_id=chunk.version_id,
        chunk_id=chunk.chunk_id,
        source=document.title,
        source_uri=_safe_source_uri(chunk.source_uri or document.source_uri),
        source_type=chunk.source_type,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        title_path=tuple(chunk.title_path),
        text_excerpt=excerpt,
        excerpt_char_count=len(excerpt),
        token_count=chunk.token_count,
        retrieval_method=_optional_str(
            citation_metadata.get("retrieval_method") or metadata.pop("retrieval_method", None)
        ),
        score=_optional_float(citation_metadata.get("score") or metadata.pop("score", None)),
        metadata=metadata,
    )


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "chunk_index",
        "sequence",
        "parent_chunk_id",
        "child_chunk_ids",
        "neighbor_prev_chunk_id",
        "neighbor_next_chunk_id",
        "retrieval_method",
        "score",
    }
    return {str(key): value for key, value in metadata.items() if str(key) in allowed}


def _safe_denial(*, context: AuthenticatedRequestContext) -> SourceResolveError:
    return SourceResolveError(
        code=SOURCE_ACCESS_DENIED,
        message="Source reference cannot be resolved.",
        details={
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "error_code": SOURCE_ACCESS_DENIED,
        },
        status_code=404,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _safe_source_uri(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    lower = normalized.lower()
    if lower.startswith(("http://", "https://", "kb://")):
        return normalized
    return None
