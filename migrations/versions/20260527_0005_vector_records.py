"""Create vector records table.

Revision ID: 20260527_0005
Revises: 20260527_0004
Create Date: 2026-05-27 17:00:00
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_0005"
down_revision: str | None = "20260527_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class PgVector(sa.types.UserDefinedType[object]):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: object) -> str:
        return f"vector({self.dimensions})"


def _timestamp_columns() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _embedding_column_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        return PgVector(_vector_index_dim())
    return sa.JSON()


def _vector_index_dim() -> int:
    raw_value = os.getenv("VECTOR_INDEX_DIM", "8")
    try:
        value = int(raw_value)
    except ValueError as exc:
        msg = "VECTOR_INDEX_DIM must be an integer for PostgreSQL vector migrations."
        raise ValueError(msg) from exc
    if value <= 0:
        msg = "VECTOR_INDEX_DIM must be greater than 0 for PostgreSQL vector migrations."
        raise ValueError(msg)
    return value


def upgrade() -> None:
    op.create_table(
        "vector_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamp_columns(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("title_path", sa.JSON(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("acl", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("embedding_provider", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_version", sa.String(length=128), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("embedding", _embedding_column_type(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "version_id",
            "chunk_id",
            "embedding_model",
            "embedding_version",
            name="uq_vector_records_chunk_embedding_version",
        ),
    )
    op.create_index("ix_vector_records_tenant_status", "vector_records", ["tenant_id", "status"])
    op.create_index(
        "ix_vector_records_tenant_document_version",
        "vector_records",
        ["tenant_id", "document_id", "version_id"],
    )
    op.create_index(
        "ix_vector_records_tenant_chunk",
        "vector_records",
        ["tenant_id", "chunk_id"],
    )
    op.create_index(
        "ix_vector_records_tenant_status_deleted",
        "vector_records",
        ["tenant_id", "status", "deleted_at"],
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_index(
            "ix_vector_records_embedding_hnsw_cosine",
            "vector_records",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("ix_vector_records_embedding_hnsw_cosine", table_name="vector_records")
    op.drop_index("ix_vector_records_tenant_status_deleted", table_name="vector_records")
    op.drop_index("ix_vector_records_tenant_chunk", table_name="vector_records")
    op.drop_index("ix_vector_records_tenant_document_version", table_name="vector_records")
    op.drop_index("ix_vector_records_tenant_status", table_name="vector_records")
    op.drop_table("vector_records")
