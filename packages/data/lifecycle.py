from __future__ import annotations

import time
from typing import Protocol

from packages.auth.policies import has_document_manage_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.dto import (
    DocumentDeleteCommand,
    DocumentDeleteResult,
    DocumentLifecycleStage,
    DocumentRecord,
    DocumentReviewListItem,
    DocumentReviewListResult,
    DocumentVersionRecord,
    DocumentVersionReviewDetail,
    DocumentVersionStatusResult,
)
from packages.data.exceptions import (
    DocumentDeleteFailedError,
    DocumentManageForbiddenError,
    DocumentNotFoundError,
    DocumentReviewInvalidRequestError,
    DocumentVersionNotFoundError,
)
from packages.vectorstores.dto import VectorDeleteResult
from packages.vectorstores.ports import VectorStore


class DocumentLifecycleRepository(Protocol):
    async def list_documents(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        limit: int | None = None,
        cursor: int | None = None,
    ) -> list[DocumentRecord]: ...

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentRecord | None: ...

    async def list_versions(
        self,
        *,
        tenant_id: str,
        document_id: str,
        status: str | None = None,
    ) -> list[DocumentVersionRecord]: ...

    async def get_document_version_status(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> DocumentVersionStatusResult | None: ...

    async def soft_delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        deleted_by: str,
    ) -> int: ...

    async def soft_delete_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        deleted_by: str,
    ) -> int: ...

    async def soft_delete_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> int: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


REVIEW_LIFECYCLE_STAGES: tuple[tuple[str, str, str, str], ...] = (
    ("uploaded", "Uploaded", "Document metadata and raw object were accepted.", "working"),
    ("parsing", "Parsing", "Parser is extracting normalized content.", "working"),
    ("parsed", "Parsed", "Parsed document sections are available.", "working"),
    ("chunking", "Chunking", "Chunker is creating retrievable chunks.", "working"),
    ("chunked", "Chunked", "Chunks are persisted and ready for embedding.", "working"),
    ("embedding", "Embedding", "Embedding job is processing chunks.", "working"),
    ("indexing", "Indexing", "Vector index update is in progress.", "working"),
    ("retrieval_ready", "Retrieval ready", "Version can participate in retrieval.", "ready"),
    ("failed_retryable", "Retryable failure", "Processing failed and may retry.", "failed"),
    (
        "failed_terminal",
        "Terminal failure",
        "Processing failed and requires intervention.",
        "failed",
    ),
    ("deleted", "Deleted", "Document or version has been soft deleted.", "failed"),
)

REVIEW_STATUS_FILTERS = {stage[0] for stage in REVIEW_LIFECYCLE_STAGES}


class DocumentLifecycleService:
    def __init__(
        self,
        *,
        repository: DocumentLifecycleRepository,
        vector_store: VectorStore,
        audit: AuditPort,
    ) -> None:
        self._repository = repository
        self._vector_store = vector_store
        self._audit = audit

    async def list_review_documents(
        self,
        context: AuthenticatedRequestContext,
        *,
        status: str | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> DocumentReviewListResult:
        started = time.perf_counter()
        try:
            self._ensure_manage_permission(context)
            normalized_limit = _normalize_review_limit(limit)
            normalized_cursor = _normalize_review_cursor(cursor)
            normalized_status = _normalize_review_status(status)
            documents = await self._repository.list_documents(
                tenant_id=context.auth.tenant_id,
                status=normalized_status,
                limit=normalized_limit + 1,
                cursor=normalized_cursor,
            )
            visible_documents = documents[:normalized_limit]
            items = []
            for document in visible_documents:
                versions = await self._repository.list_versions(
                    tenant_id=context.auth.tenant_id,
                    document_id=document.id,
                )
                latest_version = _latest_review_version(versions)
                status_result = None
                if latest_version is not None:
                    status_result = await self._repository.get_document_version_status(
                        tenant_id=context.auth.tenant_id,
                        document_id=document.id,
                        version_id=latest_version.id,
                    )
                items.append(
                    _review_list_item(
                        context=context,
                        document=document,
                        version=latest_version,
                        status_result=status_result,
                    )
                )
            result = DocumentReviewListResult(
                items=items,
                limit=normalized_limit,
                next_cursor=(
                    str(normalized_cursor + normalized_limit)
                    if len(documents) > normalized_limit
                    else None
                ),
                request_id=context.request_id,
                trace_id=context.trace_id,
            )
            await self._record_review_audit(
                context=context,
                action="document.review.list",
                resource_id="documents",
                started=started,
                status=AuditStatus.SUCCESS,
                error_code=None,
                metadata={
                    "status_filter": normalized_status,
                    "limit": normalized_limit,
                    "result_count": len(items),
                    "next_cursor": result.next_cursor,
                },
            )
            await self._repository.commit()
            return result
        except Exception as exc:
            error_code = exc.code if isinstance(exc, DomainError) else "DOCUMENT_REVIEW_FAILED"
            await self._record_review_audit(
                context=context,
                action="document.review.list",
                resource_id="documents",
                started=started,
                status=(
                    AuditStatus.DENIED
                    if error_code == "DOCUMENT_MANAGE_FORBIDDEN"
                    else AuditStatus.FAILURE
                ),
                error_code=error_code,
                metadata={"status_filter": status, "limit": limit},
            )
            await self._repository.commit()
            raise

    async def get_review_document_detail(
        self,
        context: AuthenticatedRequestContext,
        *,
        document_id: str,
        version_id: str | None = None,
    ) -> DocumentVersionReviewDetail:
        started = time.perf_counter()
        resolved_version_id = version_id
        try:
            self._ensure_manage_permission(context)
            document = await self._repository.get_document(
                tenant_id=context.auth.tenant_id,
                document_id=document_id,
            )
            if document is None:
                raise DocumentNotFoundError(details={"document_id": document_id})
            versions = await self._repository.list_versions(
                tenant_id=context.auth.tenant_id,
                document_id=document_id,
            )
            version = (
                _latest_review_version(versions)
                if version_id is None
                else next((candidate for candidate in versions if candidate.id == version_id), None)
            )
            if version is None:
                raise DocumentVersionNotFoundError(
                    details={"document_id": document_id, "version_id": version_id}
                )
            resolved_version_id = version.id
            status_result = await self._repository.get_document_version_status(
                tenant_id=context.auth.tenant_id,
                document_id=document_id,
                version_id=version.id,
            )
            if status_result is None:
                raise DocumentVersionNotFoundError(
                    details={"document_id": document_id, "version_id": version.id}
                )
            result = _review_detail(
                context=context,
                document=document,
                version=version,
                status_result=status_result,
            )
            await self._record_review_audit(
                context=context,
                action="document.review.detail",
                resource_id=version.id,
                started=started,
                status=AuditStatus.SUCCESS,
                error_code=None,
                metadata={
                    "document_id": document_id,
                    "version_id": version.id,
                    "status": result.status,
                    "chunk_count": result.chunk_count,
                    "vector_count": result.vector_count,
                    "index_status": result.index_status,
                },
            )
            await self._repository.commit()
            return result
        except Exception as exc:
            error_code = exc.code if isinstance(exc, DomainError) else "DOCUMENT_REVIEW_FAILED"
            await self._record_review_audit(
                context=context,
                action="document.review.detail",
                resource_id=resolved_version_id or document_id,
                started=started,
                status=(
                    AuditStatus.DENIED
                    if error_code == "DOCUMENT_MANAGE_FORBIDDEN"
                    else AuditStatus.FAILURE
                ),
                error_code=error_code,
                metadata={"document_id": document_id, "version_id": resolved_version_id},
            )
            await self._repository.commit()
            raise

    async def get_version_status(
        self,
        context: AuthenticatedRequestContext,
        *,
        document_id: str,
        version_id: str,
    ) -> DocumentVersionStatusResult:
        started = time.perf_counter()
        try:
            self._ensure_manage_permission(context)
            document = await self._repository.get_document(
                tenant_id=context.auth.tenant_id,
                document_id=document_id,
            )
            if document is None:
                raise DocumentNotFoundError(details={"document_id": document_id})
            result = await self._repository.get_document_version_status(
                tenant_id=context.auth.tenant_id,
                document_id=document_id,
                version_id=version_id,
            )
            if result is None:
                raise DocumentVersionNotFoundError(
                    details={"document_id": document_id, "version_id": version_id}
                )
            response = result.model_copy(
                update={"request_id": context.request_id, "trace_id": context.trace_id}
            )
            await self._record_status_audit(
                context=context,
                document_id=document_id,
                version_id=version_id,
                result=response,
                started=started,
                status=AuditStatus.SUCCESS,
                error_code=None,
            )
            await self._repository.commit()
            return response
        except Exception as exc:
            error_code = exc.code if isinstance(exc, DomainError) else "DOCUMENT_STATUS_FAILED"
            await self._record_status_audit(
                context=context,
                document_id=document_id,
                version_id=version_id,
                result=None,
                started=started,
                status=(
                    AuditStatus.DENIED
                    if error_code == "DOCUMENT_MANAGE_FORBIDDEN"
                    else AuditStatus.FAILURE
                ),
                error_code=error_code,
            )
            await self._repository.commit()
            raise

    async def delete(
        self,
        context: AuthenticatedRequestContext,
        command: DocumentDeleteCommand,
    ) -> DocumentDeleteResult:
        started = time.perf_counter()
        try:
            self._ensure_manage_permission(context)
            document = await self._repository.get_document(
                tenant_id=context.auth.tenant_id,
                document_id=command.document_id,
            )
            if document is None:
                raise DocumentNotFoundError(details={"document_id": command.document_id})

            if command.version_id is None:
                versions = await self._repository.list_versions(
                    tenant_id=context.auth.tenant_id,
                    document_id=command.document_id,
                )
                deleted_chunks = 0
                for version in versions:
                    deleted_chunks += await self._repository.soft_delete_chunks_for_version(
                        tenant_id=context.auth.tenant_id,
                        document_id=command.document_id,
                        version_id=version.id,
                    )
                deleted_versions = await self._repository.soft_delete_document(
                    tenant_id=context.auth.tenant_id,
                    document_id=command.document_id,
                    deleted_by=context.auth.user_id,
                )
                vector_result = await self._delete_vectors(
                    context=context,
                    document_id=command.document_id,
                    version_id=None,
                )
                result = DocumentDeleteResult(
                    document_id=command.document_id,
                    version_id=None,
                    status="deleted",
                    deleted_versions=deleted_versions,
                    deleted_chunks=deleted_chunks,
                    deleted_vectors=vector_result.deleted_count,
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                )
            else:
                status = await self._repository.get_document_version_status(
                    tenant_id=context.auth.tenant_id,
                    document_id=command.document_id,
                    version_id=command.version_id,
                )
                if status is None:
                    raise DocumentVersionNotFoundError(
                        details={
                            "document_id": command.document_id,
                            "version_id": command.version_id,
                        }
                    )
                deleted_chunks = await self._repository.soft_delete_chunks_for_version(
                    tenant_id=context.auth.tenant_id,
                    document_id=command.document_id,
                    version_id=command.version_id,
                )
                deleted_versions = await self._repository.soft_delete_document_version(
                    tenant_id=context.auth.tenant_id,
                    document_id=command.document_id,
                    version_id=command.version_id,
                    deleted_by=context.auth.user_id,
                )
                vector_result = await self._delete_vectors(
                    context=context,
                    document_id=command.document_id,
                    version_id=command.version_id,
                )
                result = DocumentDeleteResult(
                    document_id=command.document_id,
                    version_id=command.version_id,
                    status="deleted",
                    deleted_versions=deleted_versions,
                    deleted_chunks=deleted_chunks,
                    deleted_vectors=vector_result.deleted_count,
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                )
            await self._record_delete_audit(
                context=context,
                started=started,
                result=result,
                status=AuditStatus.SUCCESS,
                error_code=None,
            )
            await self._repository.commit()
            return result
        except Exception as exc:
            await self._repository.rollback()
            error_code = exc.code if isinstance(exc, DomainError) else "DOCUMENT_DELETE_FAILED"
            failure = DocumentDeleteResult(
                document_id=command.document_id,
                version_id=command.version_id,
                status="failed",
                deleted_versions=0,
                deleted_chunks=0,
                deleted_vectors=0,
                request_id=context.request_id,
                trace_id=context.trace_id,
            )
            await self._record_delete_audit(
                context=context,
                started=started,
                result=failure,
                status=(
                    AuditStatus.DENIED
                    if error_code == "DOCUMENT_MANAGE_FORBIDDEN"
                    else AuditStatus.FAILURE
                ),
                error_code=error_code,
            )
            await self._repository.commit()
            raise

    def _ensure_manage_permission(self, context: AuthenticatedRequestContext) -> None:
        if not has_document_manage_permission(context.auth):
            raise DocumentManageForbiddenError(
                details={"required_permissions": ["document:manage"]}
            )

    async def _delete_vectors(
        self,
        *,
        context: AuthenticatedRequestContext,
        document_id: str,
        version_id: str | None,
    ) -> VectorDeleteResult:
        try:
            return await self._vector_store.delete_by_document(
                document_id,
                version_id,
                tenant_id=context.auth.tenant_id,
            )
        except Exception as exc:
            raise DocumentDeleteFailedError(
                details={"document_id": document_id, "version_id": version_id}
            ) from exc

    async def _record_delete_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        result: DocumentDeleteResult,
        status: AuditStatus,
        error_code: str | None,
    ) -> None:
        metadata: dict[str, object] = {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "document_id": result.document_id,
            "version_id": result.version_id,
            "deleted_versions": result.deleted_versions,
            "deleted_chunks": result.deleted_chunks,
            "deleted_vectors": result.deleted_vectors,
            "status": result.status,
            "error_code": error_code,
        }
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="document.delete",
                resource=AuditResource(
                    type="document",
                    id=result.document_id,
                    metadata=metadata,
                ),
                status=status,
                latency_ms=(time.perf_counter() - started) * 1000,
                error_code=error_code,
                metadata=metadata,
            )
        )

    async def _record_status_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        document_id: str,
        version_id: str,
        result: DocumentVersionStatusResult | None,
        started: float,
        status: AuditStatus,
        error_code: str | None,
    ) -> None:
        metadata: dict[str, object] = {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "document_id": document_id,
            "version_id": version_id,
            "status": result.status if result is not None else None,
            "job_id": result.job_id if result is not None else None,
            "chunk_count": result.chunk_count if result is not None else None,
            "vector_count": result.vector_count if result is not None else None,
            "index_status": result.index_status if result is not None else None,
            "attempt_count": result.attempt_count if result is not None else None,
            "error_code": error_code,
        }
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="document.version.status",
                resource=AuditResource(
                    type="document_version",
                    id=version_id,
                    metadata={
                        "request_id": context.request_id,
                        "trace_id": context.trace_id,
                        "document_id": document_id,
                        "version_id": version_id,
                    },
                ),
                status=status,
                latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
                error_code=error_code,
                metadata=metadata,
            )
        )

    async def _record_review_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        action: str,
        resource_id: str,
        started: float,
        status: AuditStatus,
        error_code: str | None,
        metadata: dict[str, object],
    ) -> None:
        safe_metadata: dict[str, object] = {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "error_code": error_code,
            **metadata,
        }
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action=action,
                resource=AuditResource(
                    type="document_review",
                    id=resource_id,
                    metadata={
                        "request_id": context.request_id,
                        "trace_id": context.trace_id,
                    },
                ),
                status=status,
                latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
                error_code=error_code,
                metadata=safe_metadata,
            )
        )


def _normalize_review_limit(limit: int) -> int:
    if limit < 1 or limit > 100:
        raise DocumentReviewInvalidRequestError(details={"field": "limit"})
    return limit


def _normalize_review_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    normalized = cursor.strip()
    if not normalized.isdigit():
        raise DocumentReviewInvalidRequestError(details={"field": "cursor"})
    return int(normalized)


def _normalize_review_status(status: str | None) -> str | None:
    if status is None or status.strip() == "":
        return None
    normalized = status.strip()
    if normalized not in REVIEW_STATUS_FILTERS:
        raise DocumentReviewInvalidRequestError(details={"field": "status"})
    return normalized


def _latest_review_version(versions: list[DocumentVersionRecord]) -> DocumentVersionRecord | None:
    if not versions:
        return None
    return sorted(
        versions,
        key=lambda version: (
            version.created_at.isoformat() if version.created_at is not None else "",
            version.id,
        ),
    )[-1]


def _review_list_item(
    *,
    context: AuthenticatedRequestContext,
    document: DocumentRecord,
    version: DocumentVersionRecord | None,
    status_result: DocumentVersionStatusResult | None,
) -> DocumentReviewListItem:
    return DocumentReviewListItem(
        document_id=document.id,
        version_id=version.id if version is not None else None,
        source_display_name=_source_display_name(document=document, version=version),
        source_type=version.source_type if version is not None else document.source_type,
        status=status_result.status if status_result is not None else document.status,
        created_by=version.created_by if version is not None else document.created_by,
        created_at=version.created_at if version is not None else document.created_at,
        updated_at=version.updated_at if version is not None else document.updated_at,
        chunk_count=status_result.chunk_count if status_result is not None else 0,
        embedding_provider=(
            status_result.embedding_provider if status_result is not None else None
        ),
        embedding_model=status_result.embedding_model if status_result is not None else None,
        embedding_version=status_result.embedding_version if status_result is not None else None,
        embedding_dim=status_result.embedding_dim if status_result is not None else None,
        vector_count=status_result.vector_count if status_result is not None else None,
        index_status=status_result.index_status if status_result is not None else None,
        error_code=status_result.error_code if status_result is not None else None,
        error_summary=status_result.error_summary if status_result is not None else None,
        request_id=context.request_id,
        trace_id=context.trace_id,
    )


def _review_detail(
    *,
    context: AuthenticatedRequestContext,
    document: DocumentRecord,
    version: DocumentVersionRecord,
    status_result: DocumentVersionStatusResult,
) -> DocumentVersionReviewDetail:
    return DocumentVersionReviewDetail(
        document_id=document.id,
        version_id=version.id,
        source_display_name=_source_display_name(document=document, version=version),
        source_type=version.source_type,
        status=status_result.status,
        created_by=version.created_by,
        created_at=version.created_at,
        updated_at=version.updated_at,
        chunk_count=status_result.chunk_count,
        embedding_provider=status_result.embedding_provider,
        embedding_model=status_result.embedding_model,
        embedding_version=status_result.embedding_version,
        embedding_dim=status_result.embedding_dim,
        vector_count=status_result.vector_count,
        index_status=status_result.index_status,
        job_id=status_result.job_id,
        attempt_count=status_result.attempt_count,
        last_attempt_at=status_result.last_attempt_at,
        next_retry_at=status_result.next_retry_at,
        deleted_at=status_result.deleted_at,
        error_code=status_result.error_code,
        error_summary=status_result.error_summary,
        lifecycle=_lifecycle_for_status(status_result.status),
        request_id=context.request_id,
        trace_id=context.trace_id,
    )


def _source_display_name(
    *,
    document: DocumentRecord,
    version: DocumentVersionRecord | None,
) -> str:
    for candidate in (document.title, version.filename if version is not None else None):
        if candidate is None:
            continue
        normalized = str(candidate).replace("\\", "/").split("/")[-1].strip()
        if normalized:
            return normalized[:160]
    return f"document-{document.id[:12]}"


def _lifecycle_for_status(status: str) -> list[DocumentLifecycleStage]:
    stages = []
    known_statuses = {stage[0] for stage in REVIEW_LIFECYCLE_STAGES}
    for position, (stage_status, label, description, tone) in enumerate(
        REVIEW_LIFECYCLE_STAGES,
        start=1,
    ):
        stages.append(
            DocumentLifecycleStage(
                status=stage_status,
                label=label,
                description=description,
                position=position,
                tone=tone,
                is_current=stage_status == status,
                is_failure=tone == "failed",
                is_known=True,
            )
        )
    if status not in known_statuses:
        stages.append(
            DocumentLifecycleStage(
                status="unknown",
                label="Unknown status",
                description=f"Backend returned unrecognized status: {status}",
                position=None,
                tone="unknown",
                is_current=True,
                is_failure=False,
                is_known=False,
            )
        )
    return stages
