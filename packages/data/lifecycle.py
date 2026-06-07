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
    DocumentRecord,
    DocumentVersionRecord,
    DocumentVersionStatusResult,
)
from packages.data.exceptions import (
    DocumentDeleteFailedError,
    DocumentManageForbiddenError,
    DocumentNotFoundError,
    DocumentVersionNotFoundError,
)
from packages.vectorstores.dto import VectorDeleteResult
from packages.vectorstores.ports import VectorStore


class DocumentLifecycleRepository(Protocol):
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
