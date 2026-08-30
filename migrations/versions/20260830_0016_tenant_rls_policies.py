"""Enable PostgreSQL row-level security for tenant-bearing tables.

Revision ID: 20260830_0016
Revises: 20260830_0015
Create Date: 2026-08-30 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0016"
down_revision: str | None = "20260830_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Historical snapshot: keep this list local to the migration so future model
# changes cannot rewrite which tables revision 0016 targets.
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "users",
    "roles",
    "user_roles",
    "audit_logs",
    "documents",
    "document_versions",
    "ingestion_jobs",
    "chunks",
    "embedding_jobs",
    "vector_records",
    "retrieval_logs",
    "chat_sessions",
    "chat_messages",
    "agent_runs",
    "tool_calls",
    "review_items",
    "user_groups",
    "local_users",
)


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    for table_name in _TENANT_SCOPED_TABLES:
        for statement in _postgres_rls_statements(table_name):
            op.execute(sa.text(statement))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    for table_name in reversed(_TENANT_SCOPED_TABLES):
        for statement in _postgres_rls_drop_statements(table_name):
            op.execute(sa.text(statement))


def _postgres_rls_statements(table_name: str) -> tuple[str, ...]:
    policy_name = f"tenant_isolation_{table_name}"
    predicate = "tenant_id = current_setting('app.tenant_id', true)"
    return (
        f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {policy_name} ON {table_name} USING ({predicate}) WITH CHECK ({predicate})",
    )


def _postgres_rls_drop_statements(table_name: str) -> tuple[str, ...]:
    policy_name = f"tenant_isolation_{table_name}"
    return (
        f"DROP POLICY IF EXISTS {policy_name} ON {table_name}",
        f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY",
    )
