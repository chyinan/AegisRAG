from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine, UserDefinedType

from packages.data.storage.base import Base, IdMixin, TimestampMixin


def _default_acl() -> dict[str, object]:
    return {"visibility": "tenant"}


class PgVectorType(UserDefinedType[list[float]]):
    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "vector"


class VectorEmbeddingType(TypeDecorator[list[float]]):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PgVectorType())
        return dialect.type_descriptor(JSON())

    def process_bind_param(
        self,
        value: Sequence[float] | None,
        dialect: Dialect,
    ) -> list[float] | str | None:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return "[" + ",".join(str(float(item)) for item in value) + "]"
        return [float(item) for item in value]

    def process_result_value(self, value: object, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip().removeprefix("[").removesuffix("]")
            if not stripped:
                return []
            return [float(item) for item in stripped.split(",")]
        if isinstance(value, Sequence):
            return [float(item) for item in value]
        raise TypeError("Unsupported vector value returned from database.")


class DocumentModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_tenant_id_status", "tenant_id", "status"),
        Index("ix_documents_tenant_id_id", "tenant_id", "id"),
        Index("ix_documents_created_by", "created_by"),
        Index("ix_documents_source_type", "source_type"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    acl: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentVersionModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        Index("ix_document_versions_tenant_id_document_id", "tenant_id", "document_id"),
        Index("ix_document_versions_tenant_id_status", "tenant_id", "status"),
        Index("ix_document_versions_tenant_id_document_id_id", "tenant_id", "document_id", "id"),
    )

    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    acl: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IngestionJobModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        Index("ix_ingestion_jobs_tenant_id_status", "tenant_id", "status"),
        Index("ix_ingestion_jobs_tenant_id_version_id", "tenant_id", "version_id"),
        Index("ix_ingestion_jobs_tenant_id_status_version_id", "tenant_id", "status", "version_id"),
        Index("ix_ingestion_jobs_document_id", "document_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    queue_name: Mapped[str] = mapped_column(String(128), nullable=False)
    queue_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChunkModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "chunk_id",
            name="uq_chunks_tenant_chunk_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "version_id",
            "chunk_id",
            name="uq_chunks_tenant_document_version_chunk_id",
        ),
        Index("ix_chunks_tenant_id_status", "tenant_id", "status"),
        Index("ix_chunks_tenant_document_version", "tenant_id", "document_id", "version_id"),
        Index("ix_chunks_tenant_chunk_id", "tenant_id", "chunk_id"),
        Index(
            "ix_chunks_tenant_document_version_chunk_id",
            "tenant_id",
            "document_id",
            "version_id",
            "chunk_id",
        ),
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_version_id", "version_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title_path: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    acl: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=_default_acl)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    section_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmbeddingJobModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "embedding_jobs"
    __table_args__ = (
        Index("ix_embedding_jobs_tenant_id_status", "tenant_id", "status"),
        Index(
            "ix_embedding_jobs_tenant_document_version",
            "tenant_id",
            "document_id",
            "version_id",
        ),
        Index("ix_embedding_jobs_tenant_id_id", "tenant_id", "id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )


class VectorRecordModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "vector_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "version_id",
            "chunk_id",
            "embedding_model",
            "embedding_version",
            name="uq_vector_records_chunk_embedding_version",
        ),
        Index("ix_vector_records_tenant_status", "tenant_id", "status"),
        Index(
            "ix_vector_records_tenant_document_version",
            "tenant_id",
            "document_id",
            "version_id",
        ),
        Index("ix_vector_records_tenant_chunk", "tenant_id", "chunk_id"),
        Index("ix_vector_records_tenant_status_deleted", "tenant_id", "status", "deleted_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title_path: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    acl: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=_default_acl)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VectorEmbeddingType(), nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
