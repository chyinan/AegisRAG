from __future__ import annotations

from packages.data.dto import ChunkRecord
from packages.embeddings.dto import EmbeddingResponse, EmbeddingVector
from packages.vectorstores.service import map_embedding_response_to_vector_records


def test_maps_embedding_response_and_chunks_to_vector_records_without_content() -> None:
    chunks = [
        ChunkRecord(
            tenant_id="tenant-1",
            document_id="doc-1",
            version_id="ver-1",
            chunk_id="chunk-1",
            created_by="user-1",
            status="active",
            source_type="pdf",
            source_uri="kb://handbook.pdf",
            title_path=["Handbook", "Leave"],
            content="secret chunk text must not be copied",
            page_start=3,
            page_end=4,
            token_count=128,
            acl={"visibility": "tenant", "allowed_roles": ["hr"]},
            checksum="checksum-chunk-1",
            section_ids=["section-1"],
            metadata={"department": "hr"},
        )
    ]
    response = EmbeddingResponse(
        vectors=[EmbeddingVector(index=0, chunk_id="chunk-1", vector=[0.1, 0.2, 0.3])],
        provider="fake",
        model="fake-embedding",
        version="fake-v1",
        dim=3,
        usage={"text_count": 1},
        latency_ms=1.0,
    )

    records = map_embedding_response_to_vector_records(response=response, chunks=chunks)

    assert len(records) == 1
    record = records[0]
    assert record.tenant_id == "tenant-1"
    assert record.document_id == "doc-1"
    assert record.version_id == "ver-1"
    assert record.chunk_id == "chunk-1"
    assert record.source_type == "pdf"
    assert record.source_uri == "kb://handbook.pdf"
    assert record.title_path == ["Handbook", "Leave"]
    assert record.page_start == 3
    assert record.page_end == 4
    assert record.acl == {"visibility": "tenant", "allowed_roles": ["hr"]}
    assert record.checksum == "checksum-chunk-1"
    assert record.embedding_provider == "fake"
    assert record.embedding_model == "fake-embedding"
    assert record.embedding_version == "fake-v1"
    assert record.embedding_dim == 3
    assert "secret chunk text" not in str(record.model_dump())
