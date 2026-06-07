from __future__ import annotations

import pytest

from packages.vectorstores.adapters.fake import FakeVectorStore
from packages.vectorstores.adapters.pgvector import _postgres_search_query
from packages.vectorstores.dto import (
    AclFilter,
    MetadataFilter,
    VectorRecord,
    VectorSearchRequest,
)
from packages.vectorstores.exceptions import INDEX_DIMENSION_MISMATCH, VectorStoreError


@pytest.mark.asyncio
async def test_fake_vector_store_upsert_search_filters_threshold_and_top_k() -> None:
    store = FakeVectorStore(index_dim=3)
    await store.upsert(
        [
            _record(chunk_id="chunk-1", vector=[1.0, 0.0, 0.0], department="hr"),
            _record(chunk_id="chunk-2", vector=[0.9, 0.1, 0.0], department="hr"),
            _record(chunk_id="chunk-3", vector=[0.0, 1.0, 0.0], department="finance"),
        ]
    )
    await store.upsert(
        [
            _record(
                tenant_id="tenant-2",
                chunk_id="chunk-4",
                vector=[1.0, 0.0, 0.0],
                department="hr",
            )
        ]
    )

    results = await store.search(
        VectorSearchRequest(
            tenant_id="tenant-1",
            query_vector=[1.0, 0.0, 0.0],
            embedding_dim=3,
            top_k=1,
            score_threshold=0.8,
            metadata_filters=[MetadataFilter(key="department", value="hr")],
            acl_filter=AclFilter(user_id="user-1", roles=["hr"]),
        )
    )

    assert [result.chunk_id for result in results] == ["chunk-1"]
    assert results[0].tenant_id == "tenant-1"
    assert results[0].retrieval_method == "dense"
    assert results[0].source == "kb://policy.md"
    assert results[0].page_start == 1
    assert results[0].title_path == ["Policy"]


@pytest.mark.asyncio
async def test_fake_vector_store_soft_delete_removes_records_from_search() -> None:
    store = FakeVectorStore(index_dim=3)
    await store.upsert([_record(chunk_id="chunk-1", vector=[1.0, 0.0, 0.0])])

    delete_result = await store.delete_by_document(
        "doc-1",
        "ver-1",
        tenant_id="tenant-1",
    )
    results = await store.search(
        VectorSearchRequest(
            tenant_id="tenant-1",
            query_vector=[1.0, 0.0, 0.0],
            embedding_dim=3,
            top_k=10,
            acl_filter=AclFilter(user_id="user-1", roles=["hr"]),
        )
    )

    assert delete_result.deleted_count == 1
    assert results == []


@pytest.mark.asyncio
async def test_fake_vector_store_denies_private_acl_without_allow_list() -> None:
    store = FakeVectorStore(index_dim=3)
    await store.upsert(
        [
            _record(
                chunk_id="private",
                vector=[1.0, 0.0, 0.0],
                acl={"visibility": "private"},
            )
        ]
    )

    results = await store.search(
        VectorSearchRequest(
            tenant_id="tenant-1",
            query_vector=[1.0, 0.0, 0.0],
            embedding_dim=3,
            top_k=10,
            acl_filter=AclFilter(user_id="user-1"),
        )
    )

    assert results == []


@pytest.mark.asyncio
async def test_fake_vector_store_dimension_mismatch_is_atomic() -> None:
    store = FakeVectorStore(index_dim=3)
    with pytest.raises(VectorStoreError) as exc_info:
        await store.upsert(
            [
                _record(chunk_id="chunk-1", vector=[1.0, 0.0, 0.0]),
                _record(chunk_id="chunk-2", vector=[1.0, 0.0], embedding_dim=2),
            ]
        )

    assert exc_info.value.code == INDEX_DIMENSION_MISMATCH
    results = await store.search(
        VectorSearchRequest(
            tenant_id="tenant-1",
            query_vector=[1.0, 0.0, 0.0],
            embedding_dim=3,
            top_k=10,
            acl_filter=AclFilter(user_id="user-1", roles=["hr"]),
        )
    )
    assert results == []


def test_pgvector_postgres_search_query_applies_filters_before_limit() -> None:
    statement, params = _postgres_search_query(
        VectorSearchRequest(
            tenant_id="tenant-1",
            query_vector=[1.0, 0.0, 0.0],
            embedding_dim=3,
            top_k=5,
            score_threshold=0.7,
            metadata_filters=[MetadataFilter(key="department", value="hr")],
            acl_filter=AclFilter(
                user_id="user-1",
                roles=["hr"],
                department="people",
                permissions=["documents:read"],
            ),
            embedding_provider="fake",
            embedding_model="fake-embedding",
            embedding_version="fake-v1",
        )
    )

    sql = str(statement)
    assert "tenant_id = :tenant_id" in sql
    assert '"metadata"::jsonb @> CAST(:metadata_filter_0 AS jsonb)' in sql
    assert "NOT (acl::jsonb @> CAST(:denied_user_acl AS jsonb))" in sql
    assert "NOT (acl::jsonb ? 'allowed_users'" not in sql
    assert "CAST(:acl_allowed_departments_0 AS jsonb)" in sql
    assert "CAST(:acl_allowed_permissions_0 AS jsonb)" in sql
    assert "(1.0 - (embedding <=> CAST(:query_vector AS vector))) >= :score_threshold" in sql
    assert "ORDER BY embedding <=> CAST(:query_vector AS vector) ASC, chunk_id ASC" in sql
    assert "LIMIT :top_k" in sql
    assert params["query_vector"] == "[1.0,0.0,0.0]"
    assert params["metadata_filter_0"] == '{"department": "hr"}'
    assert params["top_k"] == 5


def _record(
    *,
    chunk_id: str,
    vector: list[float],
    tenant_id: str = "tenant-1",
    embedding_dim: int = 3,
    department: str = "hr",
    acl: dict[str, object] | None = None,
) -> VectorRecord:
    return VectorRecord(
        tenant_id=tenant_id,
        document_id="doc-1",
        version_id="ver-1",
        chunk_id=chunk_id,
        created_by="user-1",
        status="active",
        vector=vector,
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_version="fake-v1",
        embedding_dim=embedding_dim,
        source_type="markdown",
        source_uri="kb://policy.md",
        title_path=["Policy"],
        page_start=1,
        page_end=1,
        token_count=10,
        acl=acl or {"visibility": "tenant", "allowed_roles": ["hr"]},
        checksum=f"checksum-{chunk_id}",
        metadata={"department": department},
    )
