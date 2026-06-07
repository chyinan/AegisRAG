"""Create embedding job table.

Revision ID: 20260527_0004
Revises: 20260527_0003
Create Date: 2026-05-27 16:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_0004"
down_revision: str | None = "20260527_0003"
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
        "embedding_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamp_columns(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=True),
        sa.Column("dim", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_embedding_jobs_tenant_id_status", "embedding_jobs", ["tenant_id", "status"])
    op.create_index(
        "ix_embedding_jobs_tenant_document_version",
        "embedding_jobs",
        ["tenant_id", "document_id", "version_id"],
    )
    op.create_index("ix_embedding_jobs_tenant_id_id", "embedding_jobs", ["tenant_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_embedding_jobs_tenant_id_id", table_name="embedding_jobs")
    op.drop_index("ix_embedding_jobs_tenant_document_version", table_name="embedding_jobs")
    op.drop_index("ix_embedding_jobs_tenant_id_status", table_name="embedding_jobs")
    op.drop_table("embedding_jobs")
