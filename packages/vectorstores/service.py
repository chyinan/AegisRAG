from __future__ import annotations

from packages.data.dto import ChunkRecord
from packages.embeddings.dto import EmbeddingResponse
from packages.vectorstores.dto import VectorRecord


def map_embedding_response_to_vector_records(
    *,
    response: EmbeddingResponse,
    chunks: list[ChunkRecord],
) -> list[VectorRecord]:
    vectors_by_chunk_id = {
        vector.chunk_id: vector.vector for vector in response.vectors if vector.chunk_id is not None
    }
    records: list[VectorRecord] = []
    for index, chunk in enumerate(chunks):
        vector = vectors_by_chunk_id.get(chunk.chunk_id)
        if vector is None:
            vector = response.vectors[index].vector
        records.append(
            VectorRecord(
                tenant_id=chunk.tenant_id,
                document_id=chunk.document_id,
                version_id=chunk.version_id,
                chunk_id=chunk.chunk_id,
                created_by=chunk.created_by,
                status="active",
                vector=vector,
                embedding_provider=response.provider,
                embedding_model=response.model,
                embedding_version=response.version,
                embedding_dim=response.dim,
                source_type=chunk.source_type,
                source_uri=chunk.source_uri,
                title_path=chunk.title_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                token_count=chunk.token_count,
                acl=chunk.acl,
                checksum=chunk.checksum,
                metadata=chunk.metadata,
            )
        )
    return records
