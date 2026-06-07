"""Create document upload governance tables.

Revision ID: 20260527_0002
Revises: 20260527_0001
Create Date: 2026-05-27 14:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_0002"
down_revision: str | None = "20260527_0001"
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
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamp_columns(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("acl", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_created_by", "documents", ["created_by"])
    op.create_index("ix_documents_source_type", "documents", ["source_type"])
    op.create_index("ix_documents_tenant_id_id", "documents", ["tenant_id", "id"])
    op.create_index("ix_documents_tenant_id_status", "documents", ["tenant_id", "status"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamp_columns(),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("acl", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_versions_tenant_id_document_id",
        "document_versions",
        ["tenant_id", "document_id"],
    )
    op.create_index(
        "ix_document_versions_tenant_id_document_id_id",
        "document_versions",
        ["tenant_id", "document_id", "id"],
    )
    op.create_index(
        "ix_document_versions_tenant_id_id",
        "document_versions",
        ["tenant_id", "id"],
    )
    op.create_index(
        "ix_document_versions_tenant_id_status",
        "document_versions",
        ["tenant_id", "status"],
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamp_columns(),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("queue_name", sa.String(length=128), nullable=False),
        sa.Column("queue_job_id", sa.String(length=128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingestion_jobs_document_id", "ingestion_jobs", ["document_id"])
    op.create_index(
        "ix_ingestion_jobs_tenant_id_status",
        "ingestion_jobs",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_ingestion_jobs_tenant_id_status_version_id",
        "ingestion_jobs",
        ["tenant_id", "status", "version_id"],
    )
    op.create_index(
        "ix_ingestion_jobs_tenant_id_version_id",
        "ingestion_jobs",
        ["tenant_id", "version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_tenant_id_version_id", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_tenant_id_status_version_id", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_tenant_id_status", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_document_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")

    op.drop_index("ix_document_versions_tenant_id_status", table_name="document_versions")
    op.drop_index("ix_document_versions_tenant_id_id", table_name="document_versions")
    op.drop_index("ix_document_versions_tenant_id_document_id_id", table_name="document_versions")
    op.drop_index("ix_document_versions_tenant_id_document_id", table_name="document_versions")
    op.drop_table("document_versions")

    op.drop_index("ix_documents_tenant_id_status", table_name="documents")
    op.drop_index("ix_documents_tenant_id_id", table_name="documents")
    op.drop_index("ix_documents_source_type", table_name="documents")
    op.drop_index("ix_documents_created_by", table_name="documents")
    op.drop_table("documents")
