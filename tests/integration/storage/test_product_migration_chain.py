"""Positive upgrade/rollback contracts for the three additive product migrations."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from packages.billing.models import CostAccountModel, UsageChargeModel
from packages.ingestion.folder_sync.storage import FolderSyncPlanModel
from packages.support_ticket.models import SupportTicketModel


def test_product_migrations_chain_preserves_predecessor_data_on_each_rollback(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'product-migrations.db'}")
    names = (
        "20261016_0019_folder_sync",
        "20261016_0020_usage_costs",
        "20261016_0021_support_tickets",
    )
    migrations = [import_module(f"migrations.versions.{name}") for name in names]
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                for migration in migrations:
                    migration.upgrade()
            models = (FolderSyncPlanModel, CostAccountModel, UsageChargeModel, SupportTicketModel)
            inspector = inspect(connection)
            for model in models:
                actual = {
                    column["name"]: column["nullable"]
                    for column in inspector.get_columns(model.__tablename__)
                }
                expected = {column.name: column.nullable for column in model.__table__.columns}
                assert actual == expected
            with Session(connection) as session:
                session.add(
                    FolderSyncPlanModel(
                        id="sync-a",
                        tenant_id="tenant-a",
                        created_by="user-a",
                        connector_id="folder",
                        status="idle",
                        enabled=True,
                        next_run_at=0,
                        revision=0,
                        snapshot={},
                    )
                )
                session.add(
                    CostAccountModel(
                        id="account-a",
                        tenant_id="tenant-a",
                        created_by="user-a",
                        status="active",
                        currency="USD",
                        period="2026-10",
                        spent_nanos=50,
                    )
                )
                session.add(
                    UsageChargeModel(
                        id="charge-a",
                        tenant_id="tenant-a",
                        user_id="user-a",
                        created_by="user-a",
                        status="completed",
                        request_id="ordinary-request",
                        trace_id="ordinary-trace",
                        provider="fake",
                        model="fake-model",
                        operation="rag.query",
                        currency="USD",
                        period="2026-10",
                        usage_known=True,
                        amount_nanos=50,
                    )
                )
                session.add(
                    SupportTicketModel(
                        id="ticket-a",
                        tenant_id="tenant-a",
                        user_id="user-a",
                        created_by="user-a",
                        client_reference="ordinary-reference",
                        request_fingerprint="a" * 64,
                        title="Workstation issue",
                        description="Ordinary support request",
                        priority="normal",
                        tool_version="1",
                    )
                )
                session.commit()
            with Operations.context(MigrationContext.configure(connection)):
                migrations[2].downgrade()
                assert not inspect(connection).has_table("support_tickets")
                assert connection.scalar(text("SELECT spent_nanos FROM cost_accounts")) == 50
                assert connection.scalar(text("SELECT COUNT(*) FROM folder_sync_plans")) == 1
                migrations[1].downgrade()
                assert not inspect(connection).has_table("usage_charges")
                assert not inspect(connection).has_table("cost_accounts")
                assert connection.scalar(text("SELECT COUNT(*) FROM folder_sync_plans")) == 1
                migrations[0].downgrade()
                assert not inspect(connection).has_table("folder_sync_plans")
    finally:
        engine.dispose()
