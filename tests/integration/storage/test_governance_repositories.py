import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.auth.dto import RoleRecord, TenantRecord, UserRecord, UserRoleRecord
from packages.auth.storage.repositories import AuthRepository
from packages.common.audit import AuditEvent, AuditResource, AuditStatus
from packages.common.logging import REDACTED_VALUE
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.audit_repositories import (
    AuditLogRecord,
    AuditLogRepository,
    SqlAlchemyAuditPort,
)
from packages.data.storage.exceptions import StorageError

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _run_migrations(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")


def test_sqlite_common_storage_smoke_repositories_return_typed_dtos_after_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SQLite covers portable storage behavior only; PostgreSQL verification is Story 1.6.
    database_url = _sqlite_async_url(tmp_path / "repository-smoke.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repositories() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with session_factory() as session:
                auth_repository = AuthRepository(session)
                audit_repository = AuditLogRepository(session)

                tenant = await auth_repository.create_tenant(
                    tenant_id="tenant-1",
                    name="Acme",
                    created_by="system",
                )
                user = await auth_repository.create_user(
                    user_id="user-1",
                    tenant_id=tenant.id,
                    created_by="system",
                    external_id="employee-1",
                    email="employee@example.com",
                    display_name="Employee One",
                    department="Operations",
                )
                role = await auth_repository.create_role(
                    role_id="role-1",
                    tenant_id=tenant.id,
                    created_by="system",
                    name="knowledge_admin",
                    description="Knowledge administrator",
                    permissions=("document:read", "audit:read"),
                )
                assignment = await auth_repository.assign_role(
                    assignment_id="assignment-1",
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role_id=role.id,
                    created_by="system",
                )
                audit_log = await audit_repository.create(
                    AuditEvent(
                        request_id="req-1",
                        trace_id="trace-1",
                        tenant_id=tenant.id,
                        user_id=user.id,
                        action="tenant.bootstrap",
                        resource=AuditResource(type="tenant", id=tenant.id),
                        status=AuditStatus.SUCCESS,
                        latency_ms=5.25,
                        metadata={
                            "Authorization": "Bearer abc.def.ghi",
                            "document_content": "enterprise secret",
                            "safe": "created",
                        },
                    )
                )

                await session.commit()

            assert isinstance(tenant, TenantRecord)
            assert isinstance(user, UserRecord)
            assert isinstance(role, RoleRecord)
            assert isinstance(assignment, UserRoleRecord)
            assert isinstance(audit_log, AuditLogRecord)
            assert not hasattr(tenant, "_sa_instance_state")
            assert not hasattr(user, "_sa_instance_state")
            assert not hasattr(audit_log, "_sa_instance_state")
            assert role.permissions == ("document:read", "audit:read")
            assert audit_log.metadata["Authorization"] == REDACTED_VALUE
            assert audit_log.metadata["document_content"] == REDACTED_VALUE
            assert audit_log.metadata["safe"] == "created"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repositories())


def test_sqlalchemy_audit_port_auto_commit_persists_record_after_session_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "audit-port-auto-commit.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                audit = SqlAlchemyAuditPort(session, auto_commit=True)
                await audit.record(
                    AuditEvent(
                        request_id="req-query",
                        trace_id="trace-query",
                        tenant_id="tenant-1",
                        user_id="user-1",
                        action="rag.query",
                        resource=AuditResource(type="rag_query", id="req-query"),
                        status=AuditStatus.SUCCESS,
                        latency_ms=1.0,
                        metadata={"model": "fake-llm"},
                    )
                )

            async with session_factory() as session:
                model = await session.scalar(
                    select(AuditLogModel).where(AuditLogModel.request_id == "req-query")
                )
                assert model is not None
                assert model.action == "rag.query"
                assert model.metadata_["model"] == "fake-llm"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_sqlite_common_storage_smoke_rejects_invalid_identity_and_cross_tenant_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SQLite covers portable storage behavior only; PostgreSQL verification is Story 1.6.
    database_url = _sqlite_async_url(tmp_path / "repository-tenant-guards.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repositories() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with session_factory() as session:
                auth_repository = AuthRepository(session)

                await auth_repository.create_tenant(
                    tenant_id="tenant-a",
                    name="Tenant A",
                    created_by="system",
                )
                await auth_repository.create_tenant(
                    tenant_id="tenant-b",
                    name="Tenant B",
                    created_by="system",
                )

                with pytest.raises(StorageError) as invalid_identity:
                    await auth_repository.create_user(
                        user_id="user-without-identity",
                        tenant_id="tenant-a",
                        created_by="system",
                        display_name="No Identity",
                    )
                assert invalid_identity.value.code == "AUTH_STORAGE_INVALID_USER_IDENTITY"

                user = await auth_repository.create_user(
                    user_id="user-a",
                    tenant_id="tenant-a",
                    created_by="system",
                    external_id="employee-a",
                    display_name="Tenant A User",
                )
                role = await auth_repository.create_role(
                    role_id="role-b",
                    tenant_id="tenant-b",
                    created_by="system",
                    name="tenant_b_reader",
                    permissions=("document:read",),
                )

                with pytest.raises(StorageError) as tenant_mismatch:
                    await auth_repository.assign_role(
                        assignment_id="assignment-cross-tenant",
                        tenant_id="tenant-a",
                        user_id=user.id,
                        role_id=role.id,
                        created_by="system",
                    )
                assert tenant_mismatch.value.code == "AUTH_STORAGE_TENANT_MISMATCH"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repositories())


def test_sqlite_common_storage_smoke_rejects_single_string_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SQLite covers portable storage behavior only; PostgreSQL verification is Story 1.6.
    database_url = _sqlite_async_url(tmp_path / "repository-permission-guards.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repositories() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with session_factory() as session:
                auth_repository = AuthRepository(session)
                await auth_repository.create_tenant(
                    tenant_id="tenant-1",
                    name="Permission Tenant",
                    created_by="system",
                )

                with pytest.raises(StorageError) as invalid_permissions:
                    await auth_repository.create_role(
                        role_id="role-invalid",
                        tenant_id="tenant-1",
                        created_by="system",
                        name="invalid_permissions",
                        permissions="document:read",
                    )
                assert invalid_permissions.value.code == "AUTH_STORAGE_INVALID_PERMISSIONS"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repositories())
