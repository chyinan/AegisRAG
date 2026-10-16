"""PostgreSQL RLS policy contract tests."""

import pytest


def test_rls_covers_every_tenant_bearing_business_table() -> None:
    from packages.data.storage.rls import TENANT_SCOPED_TABLES

    assert set(TENANT_SCOPED_TABLES) == {
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
    }


def test_rls_policy_requires_current_transaction_tenant() -> None:
    from packages.data.storage.rls import postgres_rls_statements

    statements = postgres_rls_statements("documents")

    assert "ALTER TABLE documents ENABLE ROW LEVEL SECURITY" in statements
    assert "ALTER TABLE documents FORCE ROW LEVEL SECURITY" in statements
    assert (
        "CREATE POLICY tenant_isolation_documents ON documents "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    ) in statements


def test_rls_policy_rejects_unknown_table() -> None:
    from packages.data.storage.rls import postgres_rls_statements

    with pytest.raises(ValueError, match="tenant-scoped table"):
        postgres_rls_statements("unknown")


def test_postgres_session_hook_sets_transaction_local_tenant() -> None:
    from packages.common.tenant_context import tenant_scope
    from packages.data.storage.session import _set_postgres_tenant_context

    class Dialect:
        name = "postgresql"

    class ConnectionProbe:
        dialect = Dialect()

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        def execute(self, statement: object, parameters: dict[str, str]) -> None:
            self.calls.append((str(statement), parameters))

    connection = ConnectionProbe()
    with tenant_scope("tenant-a"):
        _set_postgres_tenant_context(None, None, connection)  # type: ignore[arg-type]

    assert connection.calls == [
        (
            "SELECT set_config('app.tenant_id', :tenant_id, true)",
            {"tenant_id": "tenant-a"},
        )
    ]
