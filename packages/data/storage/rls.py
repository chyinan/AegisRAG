"""Functional builder for PostgreSQL tenant-isolation policy statements."""

# pattern: Functional Core

from __future__ import annotations

from typing import Final

TENANT_SCOPED_TABLES: Final[tuple[str, ...]] = (
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
    "durable_agent_runs",
    "durable_tool_ledger",
    "durable_graph_checkpoints",
    "folder_sync_plans",
    "cost_accounts",
    "usage_charges",
    "support_tickets",
)


def postgres_rls_statements(table_name: str) -> tuple[str, ...]:
    if table_name not in TENANT_SCOPED_TABLES:
        raise ValueError(f"{table_name} is not a tenant-scoped table")
    policy_name = f"tenant_isolation_{table_name}"
    predicate = "tenant_id = current_setting('app.tenant_id', true)"
    return (
        f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {policy_name} ON {table_name} USING ({predicate}) WITH CHECK ({predicate})",
    )


def postgres_rls_drop_statements(table_name: str) -> tuple[str, ...]:
    if table_name not in TENANT_SCOPED_TABLES:
        raise ValueError(f"{table_name} is not a tenant-scoped table")
    policy_name = f"tenant_isolation_{table_name}"
    return (
        f"DROP POLICY IF EXISTS {policy_name} ON {table_name}",
        f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY",
    )
