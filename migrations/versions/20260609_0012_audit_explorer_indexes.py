"""Add audit explorer query indexes.

Revision ID: 20260609_0012
Revises: 20260527_0011
Create Date: 2026-06-09 18:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260609_0012"
down_revision: str | None = "20260527_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_audit_logs_tenant_created", "audit_logs", ["tenant_id", "created_at"])
    op.create_index(
        "ix_audit_logs_tenant_action_created",
        "audit_logs",
        ["tenant_id", "action", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_tenant_resource_created",
        "audit_logs",
        ["tenant_id", "resource_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_tenant_resource_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_action_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_created", table_name="audit_logs")
