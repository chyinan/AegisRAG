"""Add PostgreSQL full-text index for chunks.

Revision ID: 20260527_0007
Revises: 20260527_0006
Create Date: 2026-06-06 23:05:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260527_0007"
down_revision: str | None = "20260527_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_content_full_text_simple
        ON chunks
        USING gin (to_tsvector('simple', COALESCE(content, '')))
        WHERE status = 'active' AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_full_text_simple")
