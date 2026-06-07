"""Create chunk metadata table.

Revision ID: 20260527_0003
Revises: 20260527_0002
Create Date: 2026-05-27 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_0003"
down_revision: str | None = "20260527_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamp_columns(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("title_path", sa.JSON(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("acl", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("section_ids", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "chunk_id",
            name="uq_chunks_tenant_chunk_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "version_id",
            "chunk_id",
            name="uq_chunks_tenant_document_version_chunk_id",
        ),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_version_id", "chunks", ["version_id"])
    op.create_index("ix_chunks_tenant_chunk_id", "chunks", ["tenant_id", "chunk_id"])
    op.create_index(
        "ix_chunks_tenant_document_version",
        "chunks",
        ["tenant_id", "document_id", "version_id"],
    )
    op.create_index(
        "ix_chunks_tenant_document_version_chunk_id",
        "chunks",
        ["tenant_id", "document_id", "version_id", "chunk_id"],
    )
    op.create_index("ix_chunks_tenant_id_status", "chunks", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_chunks_tenant_id_status", table_name="chunks")
    op.drop_index("ix_chunks_tenant_document_version_chunk_id", table_name="chunks")
    op.drop_index("ix_chunks_tenant_document_version", table_name="chunks")
    op.drop_index("ix_chunks_tenant_chunk_id", table_name="chunks")
    op.drop_index("ix_chunks_version_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
