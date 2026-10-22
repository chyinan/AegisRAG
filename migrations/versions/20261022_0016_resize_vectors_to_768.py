"""Resize vector records for the 768-dimensional nomic-embed-text model."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261022_0016"
down_revision: str | None = "20260830_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE vector_records ALTER COLUMN embedding TYPE vector(768)")


def downgrade() -> None:
    op.execute("ALTER TABLE vector_records ALTER COLUMN embedding TYPE vector(1536)")
