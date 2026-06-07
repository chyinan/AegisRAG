from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FOUNDATIONAL_TABLES = {"tenants", "users", "roles", "user_roles", "audit_logs"}
DOCUMENT_TABLES = {
    "documents",
    "document_versions",
    "ingestion_jobs",
    "chunks",
    "embedding_jobs",
    "vector_records",
}
RETRIEVAL_TABLES = {"retrieval_logs"}
MEMORY_TABLES = {"chat_sessions", "chat_messages"}
BASE_COLUMNS = {"id", "created_at", "updated_at"}


def _sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _sqlite_sync_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_sqlite_common_ddl_smoke_alembic_upgrade_creates_foundational_governance_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SQLite covers portable DDL only; PostgreSQL runtime verification is deferred to Story 1.6.
    database_path = tmp_path / "governance.db"
    monkeypatch.setenv("DATABASE_URL", _sqlite_async_url(database_path))
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    command.upgrade(config, "head")

    engine = create_engine(_sqlite_sync_url(database_path))
    try:
        inspector = inspect(engine)
        expected_tables = FOUNDATIONAL_TABLES | DOCUMENT_TABLES | RETRIEVAL_TABLES | MEMORY_TABLES
        assert expected_tables <= set(inspector.get_table_names())
        for table_name in expected_tables:
            assert BASE_COLUMNS <= {column["name"] for column in inspector.get_columns(table_name)}

        assert {
            "tenant_id",
            "user_id",
            "request_id",
            "trace_id",
            "action",
        } <= {column["name"] for column in inspector.get_columns("audit_logs")}
        assert "ck_users_external_id_or_email" in {
            constraint["name"] for constraint in inspector.get_check_constraints("users")
        }
        assert frozenset({"tenant_id", "id"}) in {
            frozenset(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("users")
        }
        assert frozenset({"tenant_id", "id"}) in {
            frozenset(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("roles")
        }
        user_role_foreign_keys = {
            (
                tuple(foreign_key["constrained_columns"]),
                foreign_key["referred_table"],
                tuple(foreign_key["referred_columns"]),
            )
            for foreign_key in inspector.get_foreign_keys("user_roles")
        }
        assert (("tenant_id", "user_id"), "users", ("tenant_id", "id")) in user_role_foreign_keys
        assert (("tenant_id", "role_id"), "roles", ("tenant_id", "id")) in user_role_foreign_keys
        assert ("tenant_id",) in {
            tuple(index["column_names"]) for index in inspector.get_indexes("audit_logs")
        }
        assert ("request_id",) in {
            tuple(index["column_names"]) for index in inspector.get_indexes("audit_logs")
        }
        assert ("trace_id",) in {
            tuple(index["column_names"]) for index in inspector.get_indexes("audit_logs")
        }
        assert ("tenant_id",) in {
            tuple(index["column_names"]) for index in inspector.get_indexes("user_roles")
        }
        assert {
            "tenant_id",
            "created_by",
            "status",
            "source_type",
            "source_uri",
            "acl",
            "checksum",
        } <= {column["name"] for column in inspector.get_columns("documents")}
        assert {
            "tenant_id",
            "document_id",
            "object_key",
            "filename",
            "content_type",
            "byte_size",
            "deleted_at",
        } <= {column["name"] for column in inspector.get_columns("document_versions")}
        assert {
            "tenant_id",
            "document_id",
            "version_id",
            "queue_name",
            "queue_job_id",
            "attempt_count",
            "error_code",
        } <= {column["name"] for column in inspector.get_columns("ingestion_jobs")}
        assert ("tenant_id", "status") in {
            tuple(index["column_names"]) for index in inspector.get_indexes("documents")
        }
        assert ("tenant_id", "document_id") in {
            tuple(index["column_names"]) for index in inspector.get_indexes("document_versions")
        }
        assert ("tenant_id", "status") in {
            tuple(index["column_names"]) for index in inspector.get_indexes("ingestion_jobs")
        }
        assert {
            "tenant_id",
            "document_id",
            "version_id",
            "chunk_id",
            "created_by",
            "status",
            "source_type",
            "source_uri",
            "title_path",
            "content",
            "page_start",
            "page_end",
            "token_count",
            "acl",
            "checksum",
            "section_ids",
            "metadata",
            "deleted_at",
        } <= {column["name"] for column in inspector.get_columns("chunks")}
        chunk_indexes = {tuple(index["column_names"]) for index in inspector.get_indexes("chunks")}
        assert ("tenant_id", "status") in chunk_indexes
        assert ("tenant_id", "document_id", "version_id") in chunk_indexes
        assert ("tenant_id", "chunk_id") in chunk_indexes
        assert ("tenant_id", "document_id", "version_id", "chunk_id") in chunk_indexes
        assert ("document_id",) in chunk_indexes
        assert ("version_id",) in chunk_indexes
        assert frozenset({"tenant_id", "document_id", "version_id", "chunk_id"}) in {
            frozenset(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("chunks")
        }
        assert frozenset({"tenant_id", "chunk_id"}) in {
            frozenset(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("chunks")
        }
        assert {
            "tenant_id",
            "created_by",
            "status",
            "document_id",
            "version_id",
            "provider",
            "model",
            "version",
            "dim",
            "chunk_count",
            "attempt_count",
            "last_attempt_at",
            "next_retry_at",
            "error_code",
            "metadata",
        } <= {column["name"] for column in inspector.get_columns("embedding_jobs")}
        embedding_indexes = {
            tuple(index["column_names"]) for index in inspector.get_indexes("embedding_jobs")
        }
        assert ("tenant_id", "status") in embedding_indexes
        assert ("tenant_id", "document_id", "version_id") in embedding_indexes
        assert ("tenant_id", "id") in embedding_indexes
        assert {
            "tenant_id",
            "created_by",
            "status",
            "document_id",
            "version_id",
            "chunk_id",
            "source_type",
            "source_uri",
            "title_path",
            "page_start",
            "page_end",
            "token_count",
            "acl",
            "checksum",
            "embedding_provider",
            "embedding_model",
            "embedding_version",
            "embedding_dim",
            "embedding",
            "metadata",
            "deleted_at",
        } <= {column["name"] for column in inspector.get_columns("vector_records")}
        vector_indexes = {
            tuple(index["column_names"]) for index in inspector.get_indexes("vector_records")
        }
        assert ("tenant_id", "status") in vector_indexes
        assert ("tenant_id", "document_id", "version_id") in vector_indexes
        assert ("tenant_id", "chunk_id") in vector_indexes
        assert ("tenant_id", "status", "deleted_at") in vector_indexes
        assert frozenset(
            {
                "tenant_id",
                "document_id",
                "version_id",
                "chunk_id",
                "embedding_model",
                "embedding_version",
            }
        ) in {
            frozenset(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("vector_records")
        }
        assert {
            "request_id",
            "trace_id",
            "tenant_id",
            "user_id",
            "created_by",
            "status",
            "latency_ms",
            "top_k",
            "result_count",
            "rerank_score",
            "error_code",
            "query_summary",
            "metadata",
        } <= {column["name"] for column in inspector.get_columns("retrieval_logs")}
        retrieval_log_indexes = {
            tuple(index["column_names"]) for index in inspector.get_indexes("retrieval_logs")
        }
        assert ("request_id",) in retrieval_log_indexes
        assert ("trace_id",) in retrieval_log_indexes
        assert ("tenant_id",) in retrieval_log_indexes
        assert ("created_at",) in retrieval_log_indexes
        assert ("tenant_id", "request_id") in retrieval_log_indexes
        assert ("tenant_id", "created_at") in retrieval_log_indexes
        assert "ck_retrieval_logs_status" in {
            constraint["name"] for constraint in inspector.get_check_constraints("retrieval_logs")
        }
        assert {
            "request_id",
            "trace_id",
            "tenant_id",
            "user_id",
            "created_by",
            "status",
            "title",
            "last_message_at",
            "message_count",
            "metadata",
        } <= {column["name"] for column in inspector.get_columns("chat_sessions")}
        chat_session_indexes = {
            tuple(index["column_names"]) for index in inspector.get_indexes("chat_sessions")
        }
        assert ("tenant_id", "user_id", "id") in chat_session_indexes
        assert ("tenant_id", "user_id", "status") in chat_session_indexes
        assert "ck_chat_sessions_status" in {
            constraint["name"] for constraint in inspector.get_check_constraints("chat_sessions")
        }
        assert {
            "session_id",
            "request_id",
            "trace_id",
            "tenant_id",
            "user_id",
            "created_by",
            "status",
            "role",
            "content",
            "content_summary",
            "token_count",
            "sequence_no",
            "metadata",
        } <= {column["name"] for column in inspector.get_columns("chat_messages")}
        chat_message_indexes = {
            tuple(index["column_names"]) for index in inspector.get_indexes("chat_messages")
        }
        assert ("tenant_id", "session_id", "sequence_no") in chat_message_indexes
        assert ("tenant_id", "session_id", "created_at") in chat_message_indexes
        assert ("tenant_id", "user_id", "session_id") in chat_message_indexes
        assert "ck_chat_messages_status" in {
            constraint["name"] for constraint in inspector.get_check_constraints("chat_messages")
        }
        assert "ck_chat_messages_role" in {
            constraint["name"] for constraint in inspector.get_check_constraints("chat_messages")
        }
        chat_message_foreign_keys = {
            (
                tuple(foreign_key["constrained_columns"]),
                foreign_key["referred_table"],
                tuple(foreign_key["referred_columns"]),
            )
            for foreign_key in inspector.get_foreign_keys("chat_messages")
        }
        assert (("session_id",), "chat_sessions", ("id",)) in chat_message_foreign_keys
    finally:
        engine.dispose()
