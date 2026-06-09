from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import ChunkRecord, DocumentRecord, DocumentVersionRecord
from packages.rag.source_resolver import (
    SOURCE_ACCESS_DENIED,
    SourceResolveCommand,
    SourceResolveError,
    SourceResolveService,
)


class StubSourceRepository:
    def __init__(
        self,
        *,
        document: DocumentRecord | None = None,
        version: DocumentVersionRecord | None = None,
        chunk: ChunkRecord | None = None,
    ) -> None:
        self.document = document
        self.version = version
        self.chunk = chunk

    async def get_document(self, *, tenant_id: str, document_id: str) -> DocumentRecord | None:
        if self.document is None or self.document.tenant_id != tenant_id:
            return None
        if self.document.id != document_id:
            return None
        return self.document

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None:
        if self.version is None or self.version.tenant_id != tenant_id:
            return None
        if self.version.id != version_id:
            return None
        return self.version

    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None:
        if self.chunk is None or self.chunk.tenant_id != tenant_id:
            return None
        if self.chunk.chunk_id != chunk_id:
            return None
        if document_id is not None and self.chunk.document_id != document_id:
            return None
        if version_id is not None and self.chunk.version_id != version_id:
            return None
        return self.chunk


class StubCitationMetadataRepository:
    def __init__(self, metadata: Mapping[str, object] | None) -> None:
        self.metadata = metadata
        self.calls: list[dict[str, object]] = []

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
    ) -> Mapping[str, object] | None:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "request_id": request_id,
                "citation_ref": citation_ref,
                "document_id": document_id,
                "version_id": version_id,
                "chunk_id": chunk_id,
            }
        )
        return self.metadata


@pytest.mark.asyncio
async def test_source_resolve_returns_authorized_safe_excerpt_and_metadata() -> None:
    audit = InMemoryAuditPort()
    service = SourceResolveService(
        repository=StubSourceRepository(
            document=_document(),
            version=_version(),
            chunk=_chunk(
                content="A" * 2100,
                metadata={"retrieval_method": "hybrid", "score": 0.91},
            ),
        ),
        audit=audit,
        max_excerpt_chars=100,
    )

    response = await service.resolve(
        context=_context(),
        command=SourceResolveCommand(
            document_id="doc-1",
            version_id="v1",
            chunk_id="chunk-1",
            page_start=1,
            page_end=2,
            citation_ref="c1",
        ),
    )

    assert response.request_id == "req-1"
    assert response.trace_id == "trace-1"
    assert response.document_id == "doc-1"
    assert response.version_id == "v1"
    assert response.chunk_id == "chunk-1"
    assert response.text_excerpt == "A" * 100
    assert response.excerpt_char_count == 100
    assert response.source_display_name == "Policy"
    assert "source_uri" not in response.model_dump(mode="json")
    assert response.retrieval_method == "hybrid"
    assert response.score == 0.91
    assert dict(response.metadata) == {}
    assert audit.events[0].action == "rag.source.resolve"
    assert audit.events[0].metadata["authorized"] is True
    assert "chunk content" not in str(audit.events[0].metadata).lower()


@pytest.mark.asyncio
async def test_source_resolve_prefers_server_citation_metadata_and_filters_internal_uri() -> None:
    citation_repo = StubCitationMetadataRepository(
        {"retrieval_method": "rerank", "score": 0.77}
    )
    service = SourceResolveService(
        repository=StubSourceRepository(
            document=_document(source_uri="C:\\secret\\policy.md"),
            version=_version(source_uri="minio://bucket/raw/key"),
            chunk=_chunk(
                source_uri="file:///C:/secret/policy.md",
                metadata={"retrieval_method": "hybrid", "score": 0.91},
            ),
        ),
        citation_metadata_repository=citation_repo,
        audit=InMemoryAuditPort(),
    )

    response = await service.resolve(
        context=_context(),
        command=SourceResolveCommand(
            document_id="doc-1",
            version_id="v1",
            chunk_id="chunk-1",
            request_id="req-from-citation",
            citation_ref="1",
        ),
    )

    payload = response.model_dump(mode="json")
    assert response.source_display_name == "Policy"
    assert "source_uri" not in payload
    assert "secret" not in str(payload).lower()
    assert "minio://" not in str(payload).lower()
    assert "file://" not in str(payload).lower()
    assert response.retrieval_method == "rerank"
    assert response.score == 0.77
    assert citation_repo.calls[0]["request_id"] == "req-from-citation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repo_factory", "reason"),
    [
        (
            lambda: StubSourceRepository(document=None, version=_version(), chunk=_chunk()),
            "document_unavailable",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(deleted_at=_now()),
                version=_version(),
                chunk=_chunk(),
            ),
            "document_unavailable",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(document_id="other"),
                chunk=_chunk(),
            ),
            "version_unavailable",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(deleted_at=_now()),
                chunk=_chunk(),
            ),
            "version_unavailable",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(),
                chunk=_chunk(document_id="other"),
            ),
            "chunk_unavailable",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(),
                chunk=_chunk(status="inactive"),
            ),
            "chunk_unavailable",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(),
                chunk=_chunk(deleted_at=_now()),
            ),
            "chunk_unavailable",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(status="failed_terminal"),
                chunk=_chunk(),
            ),
            "version_unavailable",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(),
                chunk=_chunk(acl={"visibility": "private", "allowed_users": ["someone-else"]}),
            ),
            "acl_denied",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(),
                chunk=_chunk(acl={"visibility": "restricted", "allowed_roles": ["finance"]}),
            ),
            "acl_denied",
        ),
        (
            lambda: StubSourceRepository(
                document=_document(),
                version=_version(),
                chunk=_chunk(
                    acl={"visibility": "restricted", "allowed_departments": ["finance"]}
                ),
            ),
            "acl_denied",
        ),
    ],
)
async def test_source_resolve_uses_same_safe_denial_for_missing_inactive_and_acl(
    repo_factory: Callable[[], StubSourceRepository],
    reason: str,
) -> None:
    audit = InMemoryAuditPort()
    service = SourceResolveService(repository=repo_factory(), audit=audit)

    with pytest.raises(SourceResolveError) as exc_info:
        await service.resolve(
            context=_context(),
            command=SourceResolveCommand(document_id="doc-1", version_id="v1", chunk_id="chunk-1"),
        )

    assert exc_info.value.code == SOURCE_ACCESS_DENIED
    assert exc_info.value.status_code == 404
    assert exc_info.value.details["error_code"] == SOURCE_ACCESS_DENIED
    assert "document_id" not in exc_info.value.details
    assert audit.events[0].status == "denied"
    assert audit.events[0].metadata["authorized"] is False
    assert audit.events[0].metadata["denial_reason"] == reason


@pytest.mark.asyncio
async def test_source_resolve_cross_tenant_uses_same_safe_denial() -> None:
    audit = InMemoryAuditPort()
    service = SourceResolveService(
        repository=StubSourceRepository(document=_document(), version=_version(), chunk=_chunk()),
        audit=audit,
    )

    with pytest.raises(SourceResolveError) as exc_info:
        await service.resolve(
            context=_context(tenant_id="tenant-2"),
            command=SourceResolveCommand(document_id="doc-1", version_id="v1", chunk_id="chunk-1"),
        )

    assert exc_info.value.code == SOURCE_ACCESS_DENIED
    assert exc_info.value.status_code == 404
    assert "doc-1" not in str(exc_info.value.details)
    assert audit.events[0].metadata["denial_reason"] == "document_unavailable"


@pytest.mark.asyncio
async def test_source_resolve_page_identity_mismatch_uses_same_safe_denial() -> None:
    audit = InMemoryAuditPort()
    service = SourceResolveService(
        repository=StubSourceRepository(document=_document(), version=_version(), chunk=_chunk()),
        audit=audit,
    )

    with pytest.raises(SourceResolveError) as exc_info:
        await service.resolve(
            context=_context(),
            command=SourceResolveCommand(
                document_id="doc-1",
                version_id="v1",
                chunk_id="chunk-1",
                page_start=2,
                page_end=3,
            ),
        )

    assert exc_info.value.code == SOURCE_ACCESS_DENIED
    assert exc_info.value.status_code == 404
    assert "chunk-1" not in str(exc_info.value.details)
    assert audit.events[0].metadata["denial_reason"] == "identity_mismatch"


def _context(*, tenant_id: str = "tenant-1") -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="user-1",
            tenant_id=tenant_id,
            roles=("knowledge_user",),
            department="engineering",
            permissions=("document:read", "retrieval:query"),
        ),
    )


def _document(
    *,
    deleted_at: datetime | None = None,
    source_uri: str | None = "kb://policy.md",
) -> DocumentRecord:
    return DocumentRecord(
        id="doc-1",
        tenant_id="tenant-1",
        created_by="user-1",
        status="retrieval_ready",
        source_type="markdown",
        source_uri=source_uri,
        title="Policy",
        acl={"visibility": "tenant"},
        checksum="checksum",
        deleted_at=deleted_at,
    )


def _version(
    *,
    document_id: str = "doc-1",
    status: str = "retrieval_ready",
    deleted_at: datetime | None = None,
    source_uri: str | None = "kb://policy.md",
) -> DocumentVersionRecord:
    return DocumentVersionRecord(
        id="v1",
        document_id=document_id,
        tenant_id="tenant-1",
        created_by="user-1",
        status=status,
        source_type="markdown",
        source_uri=source_uri,
        object_key="secret/object/key",
        filename="policy.md",
        content_type="text/markdown",
        byte_size=100,
        acl={"visibility": "tenant"},
        checksum="checksum",
        deleted_at=deleted_at,
    )


def _chunk(
    *,
    document_id: str = "doc-1",
    status: str = "active",
    content: str = "Authorized source content.",
    source_uri: str | None = "kb://policy.md",
    acl: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
    deleted_at: datetime | None = None,
) -> ChunkRecord:
    return ChunkRecord(
        tenant_id="tenant-1",
        document_id=document_id,
        version_id="v1",
        chunk_id="chunk-1",
        created_by="user-1",
        status=status,
        source_type="markdown",
        source_uri=source_uri,
        title_path=["Policy", "Section"],
        content=content,
        page_start=1,
        page_end=2,
        token_count=42,
        acl=acl or {"visibility": "tenant"},
        checksum="checksum",
        section_ids=["section-1"],
        metadata=metadata or {},
        deleted_at=deleted_at,
    )


def _now() -> datetime:
    return datetime.now(tz=UTC)
