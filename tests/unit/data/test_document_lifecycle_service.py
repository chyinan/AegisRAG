from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import (
    DocumentDeleteCommand,
    DocumentRecord,
    DocumentReviewListResult,
    DocumentVersionRecord,
    DocumentVersionReviewDetail,
    DocumentVersionStatusResult,
)
from packages.data.exceptions import DocumentDeleteFailedError, DocumentManageForbiddenError
from packages.data.lifecycle import DocumentLifecycleService
from packages.vectorstores.adapters.fake import FakeVectorStore as BaseFakeVectorStore
from packages.vectorstores.dto import VectorDeleteResult


class FakeRepository:
    def __init__(self) -> None:
        self.document = _document()
        self.versions = [_version(version_id="ver-1"), _version(version_id="ver-2")]
        self.status = DocumentVersionStatusResult(
            document_id="doc-1",
            version_id="ver-1",
            status="retrieval_ready",
            chunk_count=2,
            embedding_provider="fake",
            embedding_model="fake-embedding",
            embedding_version="fake-v1",
            embedding_dim=4,
            vector_count=2,
            index_status="indexed",
            job_id="embed-job-1",
            attempt_count=1,
            last_attempt_at=None,
            next_retry_at=None,
            error_summary=None,
            request_id="",
            trace_id="",
        )
        self.deleted_document = False
        self.deleted_versions: list[str] = []
        self.deleted_chunk_versions: list[str] = []
        self.list_document_calls: list[dict[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    async def get_document(self, *, tenant_id: str, document_id: str) -> DocumentRecord | None:
        if tenant_id != self.document.tenant_id or document_id != self.document.id:
            return None
        return self.document

    async def list_documents(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        limit: int | None = None,
        cursor: int | None = None,
    ) -> list[DocumentRecord]:
        self.list_document_calls.append(
            {"tenant_id": tenant_id, "status": status, "limit": limit, "cursor": cursor}
        )
        if tenant_id != self.document.tenant_id:
            return []
        documents = [self.document]
        if status is not None:
            documents = [document for document in documents if document.status == status]
        offset = cursor or 0
        if limit is None:
            return documents[offset:]
        return documents[offset : offset + limit]

    async def list_versions(
        self,
        *,
        tenant_id: str,
        document_id: str,
        status: str | None = None,
    ) -> list[DocumentVersionRecord]:
        return [
            version
            for version in self.versions
            if version.tenant_id == tenant_id and version.document_id == document_id
        ]

    async def get_document_version_status(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> DocumentVersionStatusResult | None:
        if (
            tenant_id != "tenant-1"
            or document_id != self.status.document_id
            or version_id != self.status.version_id
        ):
            return None
        return self.status

    async def soft_delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        deleted_by: str,
    ) -> int:
        self.deleted_document = True
        return 2

    async def soft_delete_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        deleted_by: str,
    ) -> int:
        self.deleted_versions.append(version_id)
        return 1

    async def soft_delete_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> int:
        self.deleted_chunk_versions.append(version_id)
        return 2

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeVectorStore(BaseFakeVectorStore):
    def __init__(self) -> None:
        super().__init__(index_dim=4)
        self.deleted: list[tuple[str, str | None, str]] = []

    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        self.deleted.append((document_id, version_id, tenant_id))
        return VectorDeleteResult(
            deleted_count=3 if version_id is None else 1,
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
        )


class FailingVectorStore(FakeVectorStore):
    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        raise RuntimeError("vector delete failed")


@pytest.mark.asyncio
async def test_get_version_status_requires_manage_and_returns_safe_summary() -> None:
    repository = FakeRepository()
    audit = InMemoryAuditPort()
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=FakeVectorStore(),
        audit=audit,
    )

    result = await service.get_version_status(
        _context(permissions=("document:manage",)),
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "retrieval_ready"
    assert result.chunk_count == 2
    assert result.vector_count == 2
    assert result.job_id == "embed-job-1"
    assert result.attempt_count == 1
    assert result.error_summary is None
    assert result.request_id == "req-1"
    assert result.trace_id == "trace-1"
    assert "content" not in result.model_dump_json()
    assert "object_key" not in result.model_dump_json()
    assert repository.commits == 1
    assert audit.events[-1].action == "document.version.status"
    assert audit.events[-1].status is AuditStatus.SUCCESS
    assert audit.events[-1].metadata["job_id"] == "embed-job-1"


@pytest.mark.asyncio
async def test_get_version_status_returns_deleted_summary_for_same_tenant_admin() -> None:
    repository = FakeRepository()
    repository.document = repository.document.model_copy(
        update={"status": "deleted", "deleted_at": datetime.now(tz=UTC)}
    )
    repository.status = repository.status.model_copy(
        update={"status": "deleted", "deleted_at": datetime.now(tz=UTC)}
    )
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=FakeVectorStore(),
        audit=InMemoryAuditPort(),
    )

    result = await service.get_version_status(
        _context(permissions=("document:manage",)),
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "deleted"
    assert result.deleted_at is not None


@pytest.mark.asyncio
async def test_get_version_status_records_denied_audit() -> None:
    repository = FakeRepository()
    audit = InMemoryAuditPort()
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=FakeVectorStore(),
        audit=audit,
    )

    with pytest.raises(DocumentManageForbiddenError):
        await service.get_version_status(
            _context(permissions=("document:read",)),
            document_id="doc-1",
            version_id="ver-1",
        )

    assert repository.commits == 1
    assert audit.events[-1].action == "document.version.status"
    assert audit.events[-1].status is AuditStatus.DENIED
    assert audit.events[-1].error_code == "DOCUMENT_MANAGE_FORBIDDEN"


@pytest.mark.asyncio
async def test_list_review_documents_requires_manage_and_returns_safe_allowlist() -> None:
    repository = FakeRepository()
    audit = InMemoryAuditPort()
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=FakeVectorStore(),
        audit=audit,
    )

    result = await service.list_review_documents(
        _context(permissions=("document:manage",)),
        status="retrieval_ready",
        limit=10,
        cursor=None,
    )

    assert isinstance(result, DocumentReviewListResult)
    assert result.items[0].document_id == "doc-1"
    assert result.items[0].version_id == "ver-2"
    assert result.items[0].source_display_name == "Policy"
    assert result.items[0].source_type == "txt"
    assert result.items[0].status == "retrieval_ready"
    assert result.items[0].chunk_count == 0
    assert result.request_id == "req-1"
    assert result.trace_id == "trace-1"
    dumped = result.model_dump_json()
    assert "source_uri" not in dumped
    assert "object_key" not in dumped
    assert "acl" not in dumped
    assert "content" not in dumped
    assert repository.list_document_calls[-1] == {
        "tenant_id": "tenant-1",
        "status": "retrieval_ready",
        "limit": 11,
        "cursor": 0,
    }
    assert audit.events[-1].action == "document.review.list"
    assert audit.events[-1].status is AuditStatus.SUCCESS


@pytest.mark.asyncio
async def test_get_review_document_detail_uses_safe_lifecycle_for_unknown_status() -> None:
    repository = FakeRepository()
    repository.status = repository.status.model_copy(update={"status": "vendor_custom_state"})
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=FakeVectorStore(),
        audit=InMemoryAuditPort(),
    )

    result = await service.get_review_document_detail(
        _context(permissions=("document:manage",)),
        document_id="doc-1",
        version_id="ver-1",
    )

    assert isinstance(result, DocumentVersionReviewDetail)
    assert result.status == "vendor_custom_state"
    assert result.lifecycle[-1].status == "unknown"
    assert result.lifecycle[-1].tone == "unknown"
    assert result.lifecycle[-1].is_current is True
    assert all(stage.tone != "working" for stage in result.lifecycle if stage.is_current)
    assert "vendor_custom_state" in result.lifecycle[-1].description


@pytest.mark.asyncio
async def test_review_methods_record_denied_audit_without_listing_documents() -> None:
    repository = FakeRepository()
    audit = InMemoryAuditPort()
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=FakeVectorStore(),
        audit=audit,
    )

    with pytest.raises(DocumentManageForbiddenError):
        await service.list_review_documents(
            _context(permissions=("document:read",)),
            status=None,
            limit=20,
            cursor=None,
        )

    assert repository.list_document_calls == []
    assert audit.events[-1].action == "document.review.list"
    assert audit.events[-1].status is AuditStatus.DENIED


@pytest.mark.asyncio
async def test_delete_requires_manage_permission_and_records_denied_audit() -> None:
    repository = FakeRepository()
    audit = InMemoryAuditPort()
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=FakeVectorStore(),
        audit=audit,
    )

    with pytest.raises(DocumentManageForbiddenError):
        await service.delete(
            _context(permissions=("document:read",)),
            DocumentDeleteCommand(document_id="doc-1"),
        )

    assert repository.deleted_document is False
    assert repository.rollbacks == 1
    assert audit.events[-1].status is AuditStatus.DENIED
    assert audit.events[-1].error_code == "DOCUMENT_MANAGE_FORBIDDEN"


@pytest.mark.asyncio
async def test_delete_document_soft_deletes_versions_chunks_vectors_and_audits() -> None:
    repository = FakeRepository()
    vector_store = FakeVectorStore()
    audit = InMemoryAuditPort()
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=vector_store,
        audit=audit,
    )

    result = await service.delete(
        _context(permissions=("document:manage",)),
        DocumentDeleteCommand(document_id="doc-1"),
    )

    assert result.status == "deleted"
    assert result.deleted_versions == 2
    assert result.deleted_chunks == 4
    assert result.deleted_vectors == 3
    assert repository.deleted_chunk_versions == ["ver-1", "ver-2"]
    assert vector_store.deleted == [("doc-1", None, "tenant-1")]
    assert repository.commits == 1
    assert audit.events[-1].action == "document.delete"
    assert audit.events[-1].metadata["deleted_vectors"] == 3
    assert "secret" not in str(audit.events[-1].metadata)


@pytest.mark.asyncio
async def test_delete_document_version_only_deletes_that_version_scope() -> None:
    repository = FakeRepository()
    vector_store = FakeVectorStore()
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=vector_store,
        audit=InMemoryAuditPort(),
    )

    result = await service.delete(
        _context(permissions=("document:manage",)),
        DocumentDeleteCommand(document_id="doc-1", version_id="ver-1"),
    )

    assert result.deleted_versions == 1
    assert result.deleted_chunks == 2
    assert result.deleted_vectors == 1
    assert repository.deleted_document is False
    assert repository.deleted_versions == ["ver-1"]
    assert repository.deleted_chunk_versions == ["ver-1"]
    assert vector_store.deleted == [("doc-1", "ver-1", "tenant-1")]


@pytest.mark.asyncio
async def test_delete_vector_failure_rolls_back_and_records_stable_error() -> None:
    repository = FakeRepository()
    audit = InMemoryAuditPort()
    service = DocumentLifecycleService(
        repository=repository,
        vector_store=FailingVectorStore(),
        audit=audit,
    )

    with pytest.raises(DocumentDeleteFailedError):
        await service.delete(
            _context(permissions=("document:manage",)),
            DocumentDeleteCommand(document_id="doc-1"),
        )

    assert repository.rollbacks == 1
    assert audit.events[-1].status is AuditStatus.FAILURE
    assert audit.events[-1].error_code == "DOCUMENT_DELETE_FAILED"


def _context(*, permissions: tuple[str, ...]) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-1",
            roles=("knowledge_admin",),
            permissions=permissions,
        ),
    )


def _document() -> DocumentRecord:
    return DocumentRecord(
        id="doc-1",
        tenant_id="tenant-1",
        created_by="user-1",
        status="retrieval_ready",
        source_type="txt",
        source_uri="kb://policy.txt",
        title="Policy",
        acl={"visibility": "tenant"},
        checksum="checksum-doc-1",
        metadata={},
    )


def _version(*, version_id: str) -> DocumentVersionRecord:
    return DocumentVersionRecord(
        id=version_id,
        document_id="doc-1",
        tenant_id="tenant-1",
        created_by="user-1",
        status="retrieval_ready",
        source_type="txt",
        source_uri="kb://policy.txt",
        object_key=f"raw/tenant-1/doc-1/{version_id}/policy.txt",
        filename="policy.txt",
        content_type="text/plain",
        byte_size=12,
        acl={"visibility": "tenant"},
        checksum=f"checksum-{version_id}",
        metadata={},
        created_at=datetime.now(tz=UTC),
    )
