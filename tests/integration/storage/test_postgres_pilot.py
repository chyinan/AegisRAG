"""Synthetic live PostgreSQL acceptance probes; no model/object-store calls."""

import asyncio
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import bcrypt
import pytest
from alembic import command
from alembic.config import Config
from psycopg import connect
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import CheckViolation, InsufficientPrivilege
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.bootstrap import BootstrapRequest, bootstrap_admin
from packages.common.tenant_context import tenant_scope
from packages.data.dto import DocumentRecord, DocumentVersionRecord, IngestionJobRecord
from packages.data.storage.models import DocumentModel, VectorRecordModel
from packages.data.storage.repositories import DocumentRepository
from packages.data.storage.rls import TENANT_SCOPED_TABLES
from packages.data.storage.session import create_async_db_engine, create_session_factory
from packages.vectorstores.adapters.pgvector import PgVectorStore
from packages.vectorstores.dto import AclFilter, MetadataFilter, VectorRecord, VectorSearchRequest
from packages.vectorstores.exceptions import VectorStoreError
from tests.integration.storage.test_effective_document_status import (
    exercise_effective_status_matrix,
    exercise_filtered_pagination,
    exercise_latest_version_and_job,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_fresh_postgres() -> dict[str, tuple[object, ...]]:
    """Opt-in only: URLs must identify a disposable, initially empty database.

    Pre-create vector plus migration/runtime roles using ensure-roles.sql.
    The fixture refuses a database that already has public tables; it never
    drops or resets any data. CI must provide both URLs to exercise this gate.
    """
    migration_url = os.getenv("AEGISRAG_PILOT_MIGRATION_URL")
    runtime_url = os.getenv("AEGISRAG_PILOT_RUNTIME_URL")
    if not migration_url or not runtime_url:
        pytest.skip("Set AEGISRAG_PILOT_{MIGRATION,RUNTIME}_URL for a fresh PostgreSQL gate.")
    assert os.getenv("AEGISRAG_PILOT_DISPOSABLE_DATABASE") == "1", (
        "Set AEGISRAG_PILOT_DISPOSABLE_DATABASE=1 for an explicitly disposable database."
    )
    assert make_url(migration_url).drivername == "postgresql+asyncpg"
    assert make_url(runtime_url).drivername == "postgresql+asyncpg"
    assert make_url(migration_url).database == make_url(runtime_url).database
    assert os.getenv("AEGISRAG_RLS_ADMIN_DSN")
    assert os.getenv("AEGISRAG_RLS_RUNTIME_DSN")

    def endpoint(url: str) -> tuple[object, int, object]:
        parsed = make_url(url)
        return (
            parsed.query.get("host", parsed.host),
            int(parsed.query.get("port", parsed.port or 5432)),
            parsed.database,
        )

    expected_endpoint = endpoint(migration_url)
    assert expected_endpoint == endpoint(runtime_url), "PostgreSQL test endpoints must match."
    for name in ("AEGISRAG_RLS_ADMIN_DSN", "AEGISRAG_RLS_RUNTIME_DSN"):
        dsn = conninfo_to_dict(os.environ[name])
        assert (dsn.get("host"), int(dsn.get("port", "5432")), dsn.get("dbname")) == (
            expected_endpoint
        ), "PostgreSQL test DSNs must identify the same isolated database."
    assert make_url(runtime_url).username == os.environ["AEGISRAG_RLS_RUNTIME_USER"]

    async def assert_empty() -> None:
        engine = create_async_db_engine(migration_url)
        try:
            async with engine.connect() as connection:
                tables = list(
                    await connection.scalars(
                        text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                    )
                )
                assert tables == [], "Fresh PostgreSQL gate refuses an existing application schema."
        finally:
            await engine.dispose()

    asyncio.run(assert_empty())
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("DATABASE_URL", migration_url)
        environment.setenv("APP_ENV", "test")
        # Regression: 0015 must bind its reused tenant ID consistently for asyncpg.
        command.upgrade(config, "20260830_0016")

        async def seed_legacy() -> None:
            engine = create_async_db_engine(runtime_url)
            try:
                with tenant_scope("legacy-tenant"):
                    async with create_session_factory(engine)() as session:
                        await _upload(session, "legacy-tenant", "legacy-doc", "legacy-ver")
                        await PgVectorStore(session, index_dim=1536).upsert(
                            [
                                _record(
                                    "legacy-chunk",
                                    x=0.125,
                                    y=-0.25,
                                    tenant="legacy-tenant",
                                    doc="legacy-doc",
                                    version="legacy-ver",
                                )
                            ]
                        )
                        await session.commit()
            finally:
                await engine.dispose()

        asyncio.run(seed_legacy())
        legacy_before = _vector_snapshot()
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert _vector_snapshot() == legacy_before
    return legacy_before


def _vector_snapshot() -> dict[str, tuple[object, ...]]:
    with connect(os.environ["AEGISRAG_RLS_ADMIN_DSN"]) as connection:
        rows = connection.execute(
            "SELECT id, encode(vector_send(embedding), 'hex'), row_to_json(vector_records)::text "
            "FROM vector_records ORDER BY id"
        ).fetchall()
        return {row[0]: row[1:] for row in rows}


def test_migration_catalog_pgvector_and_runtime_privileges() -> None:
    with connect(os.environ["AEGISRAG_RLS_ADMIN_DSN"]) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "20261014_0017",
        )
        assert (
            connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            ).fetchone()
            is not None
        )
        assert connection.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid='vector_records'::regclass AND attname='embedding'"
        ).fetchone() == ("vector",)
        role = connection.execute(
            "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname='rag_runtime'"
        ).fetchone()
        assert role == (False, False, False, False)
        rows = connection.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner) "
            "FROM pg_class WHERE relname = ANY(%s) ORDER BY relname",
            (list(TENANT_SCOPED_TABLES),),
        ).fetchall()
        assert len(rows) == len(TENANT_SCOPED_TABLES) == 18
        assert all(row[1:] == (True, True, "rag_migrator") for row in rows)
        assert connection.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
            "AND policyname LIKE 'tenant_isolation_%%'"
        ).fetchone() == (18,)
    with connect(os.environ["AEGISRAG_RLS_RUNTIME_DSN"]) as connection:
        assert connection.execute(
            "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
        ).fetchone() == (False,)
        with pytest.raises(InsufficientPrivilege):
            connection.execute("CREATE TABLE forbidden_runtime_ddl (id int)")


def test_bootstrap_cli_real_postgres_is_idempotent_and_does_not_reset_password(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "synthetic-password"
    first_password, second_password = "Synthetic-Bootstrap42!", "Changed-Synthetic42!"
    secret.write_text(first_password + "\n")
    args = [
        sys.executable,
        "-m",
        "packages.auth.bootstrap_cli",
        "--tenant-id",
        "default",
        "--tenant-name",
        "Default Tenant",
        "--admin-username",
        "admin",
        "--password-file",
        str(secret),
    ]
    environment = {**os.environ, "DATABASE_URL": os.environ["AEGISRAG_PILOT_RUNTIME_URL"]}
    first = subprocess.run(
        args, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30
    )
    assert first.returncode == 0, first.stderr
    with connect(os.environ["AEGISRAG_RLS_RUNTIME_DSN"]) as connection:
        connection.execute("SELECT set_config('app.tenant_id','default',true)")
        original = connection.execute(
            "SELECT id, password_hash, group_id FROM local_users WHERE tenant_id='default'"
        ).fetchone()
    assert original is not None
    assert bcrypt.checkpw(first_password.encode(), original[1].encode())
    secret.write_text(second_password + "\n")
    second = subprocess.run(
        args, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30
    )
    assert second.returncode == 0, second.stderr
    with connect(os.environ["AEGISRAG_RLS_RUNTIME_DSN"]) as connection:
        connection.execute("SELECT set_config('app.tenant_id','default',true)")
        assert connection.execute(
            "SELECT id, password_hash, group_id FROM local_users WHERE tenant_id='default'"
        ).fetchall() == [original]
        assert connection.execute(
            "SELECT count(*) FROM user_groups WHERE tenant_id='default'"
        ).fetchone() == (1,)
    assert not bcrypt.checkpw(second_password.encode(), original[1].encode())
    output = first.stdout + first.stderr + second.stdout + second.stderr
    for forbidden in (first_password, second_password, original[1], str(secret)):
        assert forbidden not in output


def test_runtime_app_tables_and_pool_reuse_are_tenant_isolated() -> None:
    async def exercise() -> None:
        engine = create_async_db_engine(
            os.environ["AEGISRAG_PILOT_RUNTIME_URL"], pool_size=1, max_overflow=0
        )
        factory = create_session_factory(engine)
        try:
            for tenant in ("probe-a", "probe-b"):
                async with factory() as session:
                    result = await bootstrap_admin(
                        session,
                        BootstrapRequest(
                            tenant, f"Synthetic {tenant}", "admin", "Synthetic-Only42!"
                        ),
                    )
                    assert result.created
                with tenant_scope(tenant):
                    async with factory() as session:
                        session.add(
                            DocumentModel(
                                id=f"doc-{tenant}",
                                tenant_id=tenant,
                                created_by=result.user_id,
                                status="chunked",
                                source_type="txt",
                                title="Synthetic fixture",
                                checksum=tenant,
                                acl={},
                                metadata_={},
                            )
                        )
                        await session.commit()
            # One pooled connection moves A -> B -> missing -> A. No tenant leaks.
            for tenant, expected in [
                ("probe-a", ["doc-probe-a"]),
                ("probe-b", ["doc-probe-b"]),
                (None, []),
                ("probe-a", ["doc-probe-a"]),
            ]:
                with tenant_scope(tenant) if tenant else nullcontext():
                    async with factory() as session:
                        assert (
                            list(
                                await session.scalars(
                                    select(DocumentModel.id).order_by(DocumentModel.id)
                                )
                            )
                            == expected
                        )
                        if tenant:
                            assert (
                                await session.scalar(
                                    text("SELECT current_setting('app.tenant_id',true)")
                                )
                                == tenant
                            )
            with tenant_scope("probe-a"):
                async with factory() as session:
                    session.add(
                        DocumentModel(
                            id="cross-tenant-write",
                            tenant_id="probe-b",
                            created_by="synthetic",
                            status="chunked",
                            source_type="txt",
                            checksum="synthetic",
                            acl={},
                            metadata_={},
                        )
                    )
                    with pytest.raises(DBAPIError):
                        await session.flush()
                    await session.rollback()
            async with factory() as session:
                session.add(
                    DocumentModel(
                        id="missing-scope-write",
                        tenant_id="probe-a",
                        created_by="synthetic",
                        status="chunked",
                        source_type="txt",
                        checksum="synthetic",
                        acl={},
                        metadata_={},
                    )
                )
                with pytest.raises(DBAPIError):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def _vector(x: float, y: float) -> list[float]:
    return [float(x), float(y)] + [0.0] * 1534


async def _upload(
    session: AsyncSession, tenant: str, doc: str, version: str
) -> tuple[DocumentRecord, DocumentVersionRecord, IngestionJobRecord]:
    return await DocumentRepository(session).create_upload_records(
        document=DocumentRecord(
            id=doc,
            tenant_id=tenant,
            created_by="synthetic",
            status="chunked",
            source_type="txt",
            source_uri="synthetic://postgres/policy",
            title="Synthetic policy",
            checksum=f"checksum-{doc}",
            acl={"visibility": "tenant"},
            metadata={},
        ),
        version=DocumentVersionRecord(
            id=version,
            document_id=doc,
            tenant_id=tenant,
            created_by="synthetic",
            status="chunked",
            source_type="txt",
            source_uri="synthetic://postgres/policy",
            object_key=f"synthetic/{version}",
            filename="policy.txt",
            content_type="text/plain",
            byte_size=24,
            checksum=f"checksum-{version}",
            acl={"visibility": "tenant"},
            metadata={},
        ),
        job=IngestionJobRecord(
            id=f"job-{doc}",
            tenant_id=tenant,
            created_by="synthetic",
            status="chunked",
            document_id=doc,
            version_id=version,
            queue_name="ingestion",
        ),
    )


def _record(
    chunk: str,
    x: float = 1,
    y: float = 0,
    acl: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
    tenant: str = "vector-a",
    doc: str = "vector-doc-a",
    version: str = "vector-ver-a",
) -> VectorRecord:
    return VectorRecord(
        tenant_id=tenant,
        document_id=doc,
        version_id=version,
        chunk_id=chunk,
        created_by="synthetic",
        vector=_vector(x, y),
        embedding_provider="fake",
        embedding_model="synthetic-1536",
        embedding_version="v1",
        embedding_dim=1536,
        source_type="txt",
        source_uri="synthetic://postgres/policy",
        title_path=["Synthetic policy"],
        page_start=1,
        page_end=1,
        token_count=6,
        acl=acl or {"visibility": "tenant"},
        checksum=f"checksum-{chunk}",
        metadata=metadata or {},
    )


def _request(**overrides: Any) -> VectorSearchRequest:
    return VectorSearchRequest(
        **{
            "tenant_id": "vector-a",
            "query_vector": _vector(1, 0),
            "embedding_dim": 1536,
            "top_k": 10,
            "acl_filter": AclFilter(user_id="reader"),
            **overrides,
        }
    )


def test_real_pgvector_persistence_acl_filtering_before_topk_upsert_and_soft_delete() -> None:
    async def exercise() -> None:
        engine = create_async_db_engine(os.environ["AEGISRAG_PILOT_RUNTIME_URL"])
        factory = create_session_factory(engine)
        try:
            for tenant, doc, version in [
                ("vector-a", "vector-doc-a", "vector-ver-a"),
                ("vector-b", "vector-doc-b", "vector-ver-b"),
            ]:
                with tenant_scope(tenant):
                    async with factory() as session:
                        await _upload(session, tenant, doc, version)
                        store = PgVectorStore(session, index_dim=1536)
                        if tenant == "vector-a":
                            records = [
                                _record(
                                    "denied-perfect",
                                    acl={"visibility": "tenant", "denied_users": ["reader"]},
                                ),
                                _record(
                                    "restricted-perfect",
                                    acl={"visibility": "restricted", "allowed_roles": ["hr"]},
                                ),
                                _record(
                                    "allowed-policy", x=0.9, y=0.1, metadata={"category": "policy"}
                                ),
                                _record(
                                    "allowed-other", x=0.8, y=0.2, metadata={"category": "other"}
                                ),
                                _record(
                                    "department",
                                    acl={
                                        "visibility": "restricted",
                                        "allowed_departments": ["legal"],
                                    },
                                ),
                                _record(
                                    "permission",
                                    acl={
                                        "visibility": "restricted",
                                        "allowed_permissions": ["documents:read"],
                                    },
                                ),
                            ]
                        else:
                            records = [
                                _record("foreign-perfect", tenant=tenant, doc=doc, version=version)
                            ]
                        assert (await store.upsert(records)).upserted_count == len(records)
                        await session.commit()
            # Fresh session proves persistent storage and PostgreSQL vector codec roundtrip.
            with tenant_scope("vector-a"):
                async with factory() as session:
                    store = PgVectorStore(session, index_dim=1536)
                    row = await session.scalar(
                        select(VectorRecordModel).where(
                            VectorRecordModel.chunk_id == "allowed-policy"
                        )
                    )
                    assert len(row.embedding) == 1536 and row.embedding[:2] == pytest.approx(
                        [0.9, 0.1]
                    )
                    result = await store.search(_request(top_k=1))
                    assert [r.chunk_id for r in result] == ["allowed-policy"]
                    assert result[0].score > 0.99 and result[0].tenant_id == "vector-a"
                    assert result[0].source_uri == "synthetic://postgres/policy"
                    assert result[0].page_start == 1 and result[0].retrieval_method == "dense"
                    filtered = await store.search(
                        _request(
                            metadata_filters=[MetadataFilter(key="category", value="other")],
                            top_k=1,
                        )
                    )
                    assert [r.chunk_id for r in filtered] == ["allowed-other"]
                    assert await store.search(_request(tenant_id="vector-b")) == []
                    assert await store.search(_request(embedding_model="absent")) == []
                    assert await store.search(_request(embedding_version="absent")) == []
                    assert await store.search(_request(score_threshold=1.01)) == []
                    assert [
                        r.chunk_id
                        for r in await store.search(_request(top_k=1, distance_metric="l2"))
                    ] == ["allowed-policy"]
                    role = await store.search(
                        _request(acl_filter=AclFilter(user_id="reader", roles=["hr"]), top_k=1)
                    )
                    assert [r.chunk_id for r in role] == ["restricted-perfect"]
                    department = await store.search(
                        _request(
                            acl_filter=AclFilter(user_id="reader", department="legal"), top_k=1
                        )
                    )
                    assert [r.chunk_id for r in department] == ["department"]
                    permission = await store.search(
                        _request(
                            acl_filter=AclFilter(user_id="reader", permissions=["documents:read"]),
                            top_k=1,
                        )
                    )
                    assert [r.chunk_id for r in permission] == ["permission"]
                    await store.upsert(
                        [_record("allowed-policy", x=0.7, y=0.3, metadata={"category": "updated"})]
                    )
                    assert await session.scalar(text("SELECT count(*) FROM vector_records")) == 6
                    await session.commit()
                async with factory() as session:
                    store = PgVectorStore(session, index_dim=1536)
                    updated = await store.search(
                        _request(metadata_filters=[MetadataFilter(key="category", value="updated")])
                    )
                    assert [r.chunk_id for r in updated] == ["allowed-policy"]
                    with pytest.raises(VectorStoreError) as mismatch:
                        await store.search(_request(query_vector=[1.0, 0.0, 0.0], embedding_dim=3))
                    assert mismatch.value.code == "INDEX_DIMENSION_MISMATCH"
                    assert (
                        await store.delete_by_document(
                            "vector-doc-a", "vector-ver-a", tenant_id="vector-a"
                        )
                    ).deleted_count == 6
                    await session.commit()
                async with factory() as session:
                    assert await PgVectorStore(session, index_dim=1536).search(_request()) == []
                    assert (
                        await session.scalar(
                            text(
                                "SELECT count(*) FROM vector_records "
                                "WHERE deleted_at IS NOT NULL AND status='deleted'"
                            )
                        )
                        == 6
                    )
            with tenant_scope("vector-b"):
                async with factory() as session:
                    assert [
                        r.chunk_id
                        for r in await PgVectorStore(session, index_dim=1536).search(
                            _request(tenant_id="vector-b")
                        )
                    ] == ["foreign-perfect"]
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_mixed_dimensions_preserve_data_and_downgrade_fails_closed(
    migrated_fresh_postgres: dict[str, tuple[object, ...]],
) -> None:
    """Exercise configured 8/768/1536 dimensions and the complete downgrade path."""

    def vector(dim: int) -> list[float]:
        return [1.0] + [0.0] * (dim - 1)

    async def seed_and_search() -> None:
        engine = create_async_db_engine(os.environ["AEGISRAG_PILOT_RUNTIME_URL"])
        try:
            for tenant in ("mixed-a", "mixed-b"):
                with tenant_scope(tenant):
                    async with create_session_factory(engine)() as session:
                        doc, version = f"doc-{tenant}", f"ver-{tenant}"
                        await _upload(session, tenant, doc, version)
                        for dim in (8, 768, 1536):
                            base = _record(
                                f"{tenant}-{dim}", tenant=tenant, doc=doc, version=version
                            )
                            record = base.model_copy(
                                update={
                                    "vector": vector(dim),
                                    "embedding_dim": dim,
                                    "embedding_model": f"synthetic-{dim}",
                                    "metadata": {"dimension": dim},
                                }
                            )
                            store = PgVectorStore(session, index_dim=dim)
                            records = [
                                record,
                                record.model_copy(
                                    update={
                                        "chunk_id": f"{tenant}-{dim}-denied",
                                        "acl": {
                                            "visibility": "tenant",
                                            "denied_users": ["reader"],
                                        },
                                    }
                                ),
                                record.model_copy(
                                    update={
                                        "chunk_id": f"{tenant}-{dim}-v2",
                                        "embedding_version": "v2",
                                    }
                                ),
                            ]
                            for item in records:
                                await store.upsert([item])
                        await session.commit()
                    async with create_session_factory(engine)() as session:
                        for dim in (8, 768, 1536):
                            for metric in ("cosine", "l2"):
                                store = PgVectorStore(session, index_dim=dim)
                                request = VectorSearchRequest(
                                    tenant_id=tenant,
                                    query_vector=vector(dim),
                                    embedding_dim=dim,
                                    top_k=1,
                                    score_threshold=0.9,
                                    distance_metric=metric,
                                    embedding_provider="fake",
                                    embedding_model=f"synthetic-{dim}",
                                    embedding_version="v1",
                                    acl_filter=AclFilter(user_id="reader"),
                                    metadata_filters=[MetadataFilter(key="dimension", value=dim)],
                                )
                                result = await store.search(request)
                                assert [row.chunk_id for row in result] == [f"{tenant}-{dim}"]
                                assert result[0].score == pytest.approx(1.0)
                                assert result[0].tenant_id == tenant
                                # No provider/model filter: the operator still safely handles
                                # other dimensions before the query planner applies predicates.
                                result = await store.search(
                                    request.model_copy(
                                        update={
                                            "embedding_model": None,
                                            "embedding_provider": None,
                                            "metadata_filters": [],
                                            "top_k": 10,
                                        }
                                    )
                                )
                                assert [row.chunk_id for row in result] == [f"{tenant}-{dim}"]
                                assert (
                                    await store.search(
                                        request.model_copy(
                                            update={
                                                "tenant_id": "mixed-b"
                                                if tenant == "mixed-a"
                                                else "mixed-a"
                                            }
                                        )
                                    )
                                    == []
                                )
        finally:
            await engine.dispose()

    asyncio.run(seed_and_search())
    # Real DB CHECK enforcement covers both zero and vector/metadata mismatch.
    with connect(os.environ["AEGISRAG_RLS_RUNTIME_DSN"], autocommit=True) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'mixed-a', false)")
        for invalid_dimension in (0, 7, 1536):
            with pytest.raises(CheckViolation):
                connection.execute(
                    "UPDATE vector_records SET embedding_dim=%s WHERE chunk_id='mixed-a-8'",
                    (invalid_dimension,),
                )
    # Soft-delete every incompatible row: downgrade must still reject them.
    with connect(os.environ["AEGISRAG_RLS_ADMIN_DSN"]) as connection:
        connection.execute(
            "UPDATE vector_records SET status='deleted', deleted_at=CURRENT_TIMESTAMP "
            "WHERE tenant_id IN ('mixed-a','mixed-b') AND embedding_dim <> 1536"
        )
    before_refusal = _vector_snapshot()
    for row_id, original in migrated_fresh_postgres.items():
        assert before_refusal[row_id] == original
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("DATABASE_URL", os.environ["AEGISRAG_PILOT_MIGRATION_URL"])
        with pytest.raises(RuntimeError, match="Cannot downgrade vector storage"):
            command.downgrade(config, "20260830_0016")
        assert _vector_snapshot() == before_refusal
        with connect(os.environ["AEGISRAG_RLS_ADMIN_DSN"]) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20261014_0017",
            )
            assert connection.execute(
                "SELECT format_type(atttypid,atttypmod) FROM pg_attribute "
                "WHERE attrelid='vector_records'::regclass AND attname='embedding'"
            ).fetchone() == ("vector",)
            assert connection.execute(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname='ck_vector_records_downgrade_1536'"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid='vector_records'::regclass"
            ).fetchone() == (True, True)
        # Remove exactly this test's synthetic documents using its runtime tenant scopes.
        # All remaining rows are 1536-dimensional, including pre-0017 legacy data.
        with connect(os.environ["AEGISRAG_RLS_RUNTIME_DSN"]) as connection:
            for tenant in ("mixed-a", "mixed-b"):
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
                connection.execute(
                    "DELETE FROM documents WHERE id=%s AND tenant_id=%s",
                    (f"doc-{tenant}", tenant),
                )
        before_downgrade = _vector_snapshot()
        command.downgrade(config, "20260830_0016")
        assert _vector_snapshot() == before_downgrade
        with connect(os.environ["AEGISRAG_RLS_ADMIN_DSN"]) as connection:
            assert connection.execute(
                "SELECT format_type(atttypid,atttypmod) FROM pg_attribute "
                "WHERE attrelid='vector_records'::regclass AND attname='embedding'"
            ).fetchone() == ("vector(1536)",)
        command.upgrade(config, "head")
        assert _vector_snapshot() == before_downgrade


def test_postgres_effective_status_filters_match_review_before_pagination() -> None:
    """Use the runtime role and real SQL for every projection/pagination scenario."""
    async def exercise() -> None:
        engine = create_async_db_engine(os.environ["AEGISRAG_PILOT_RUNTIME_URL"])
        factory = create_session_factory(engine)
        try:
            for tenant, scenario in (
                ("pg-status-matrix", exercise_effective_status_matrix),
                ("pg-status-versions", exercise_latest_version_and_job),
                ("pg-status-pages", exercise_filtered_pagination),
            ):
                with tenant_scope(tenant):
                    async with factory() as session:
                        await scenario(session, tenant)
                        assert await DocumentRepository(session).list_documents(
                            tenant_id="pg-status-other", status="failed_retryable",
                        ) == []
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["before_accept", "after_accept"])
@pytest.mark.parametrize("operator_recovery", [False, True])
def test_postgres_redis_committed_handoff_reaches_ready_once(
    monkeypatch: pytest.MonkeyPatch, failure: str, operator_recovery: bool,
) -> None:
    from tests.integration.storage.queue_handoff_probe import exercise_real_queue_handoff

    redis_url = os.getenv("AEGISRAG_PILOT_REDIS_URL")
    if not redis_url:
        pytest.skip("Set AEGISRAG_PILOT_REDIS_URL for the live RQ handoff gate.")
    exercise_real_queue_handoff(
        os.environ["AEGISRAG_PILOT_RUNTIME_URL"], redis_url, failure, monkeypatch,
        operator_recovery=operator_recovery,
    )
