"""Allow configured embedding dimensions without rewriting existing vectors.

Revision ID: 20261014_0017
Revises: 20260830_0016
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0017"
down_revision: str | None = "20260830_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIMENSION_CHECK = "ck_vector_records_embedding_dimension"
_DOWNGRADE_CHECK = "ck_vector_records_downgrade_1536"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Remove only the column typmod; pgvector's stored float32 values stay intact.
    # Do not create a mixed-dimension ANN index or reinterpret existing models.
    op.execute(
        "ALTER TABLE vector_records ALTER COLUMN embedding TYPE vector USING embedding::vector"
    )
    op.create_check_constraint(
        _DIMENSION_CHECK,
        "vector_records",
        "embedding_dim > 0 AND vector_dims(embedding) = embedding_dim",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # FORCE RLS hides other tenants from the migration role. A normal SELECT
    # would miss incompatible rows. DDL constraint validation scans every row,
    # including soft-deleted rows, under the same transaction/lock as narrowing.
    op.execute(
        f"ALTER TABLE vector_records ADD CONSTRAINT {_DOWNGRADE_CHECK} "
        "CHECK (embedding_dim = 1536 AND vector_dims(embedding) = 1536) NOT VALID"
    )
    try:
        op.execute(f"ALTER TABLE vector_records VALIDATE CONSTRAINT {_DOWNGRADE_CHECK}")
    except sa.exc.IntegrityError as exc:
        raise RuntimeError(
            "Cannot downgrade vector storage: non-1536-dimensional records exist, "
            "including records hidden by tenant isolation or soft deletion. "
            "No records were changed; retain the current schema."
        ) from exc
    op.drop_constraint(_DIMENSION_CHECK, "vector_records", type_="check")
    op.execute(
        "ALTER TABLE vector_records ALTER COLUMN embedding TYPE vector(1536) "
        "USING embedding::vector(1536)"
    )
    op.drop_constraint(_DOWNGRADE_CHECK, "vector_records", type_="check")
