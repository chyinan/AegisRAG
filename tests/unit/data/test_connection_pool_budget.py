from __future__ import annotations

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from packages.common.config import AppSettings
from packages.data.storage import session


@pytest.mark.parametrize("url", ["postgresql+asyncpg://unused/db", "postgresql+psycopg://unused/db"])
def test_postgres_engine_receives_explicit_pool_budget(
    monkeypatch: pytest.MonkeyPatch, url: str,
) -> None:
    create = Mock()
    monkeypatch.setattr(session, "create_async_engine", create)
    monkeypatch.setattr(session, "load_settings", lambda: AppSettings(
        DATABASE_POOL_SIZE=3, DATABASE_MAX_OVERFLOW=2, DATABASE_POOL_TIMEOUT_SECONDS=0.5,
    ))
    session.create_async_db_engine(url, hide_parameters=True)
    create.assert_called_once_with(
        url, pool_pre_ping=True, pool_size=3, max_overflow=2, pool_timeout=0.5,
        hide_parameters=True,
    )


def test_explicit_engine_overrides_win_and_sqlite_has_no_queuepool_knobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = Mock()
    monkeypatch.setattr(session, "create_async_engine", create)
    session.create_async_db_engine("postgresql+asyncpg://unused/db", pool_size=7)
    assert create.call_args.kwargs["pool_size"] == 7
    create.reset_mock()
    session.create_async_db_engine("sqlite+aiosqlite:///:memory:")
    create.assert_called_once_with("sqlite+aiosqlite:///:memory:", pool_pre_ping=True)


@pytest.mark.parametrize("field,value", [
    ("DATABASE_POOL_SIZE", 0), ("DATABASE_MAX_OVERFLOW", -1),
    ("DATABASE_POOL_TIMEOUT_SECONDS", float("nan")), ("LLM_MAX_IN_FLIGHT", 0),
    ("LLM_MAX_WAITERS", -1), ("LLM_TOTAL_TIMEOUT_SECONDS", float("inf")),
    ("BGE_MAX_CONCURRENCY", 0), ("BGE_MAX_QUEUE_SIZE", -1),
])
def test_capacity_settings_reject_invalid_budgets(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**{field: value})


@pytest.mark.parametrize("key,default", [
    ("DATABASE_POOL_SIZE", "5"), ("DATABASE_MAX_OVERFLOW", "10"),
    ("DATABASE_POOL_TIMEOUT_SECONDS", "30"), ("LLM_MAX_IN_FLIGHT", "8"),
    ("LLM_MAX_WAITERS", "32"), ("LLM_QUEUE_TIMEOUT_SECONDS", "2"),
    ("LLM_TOTAL_TIMEOUT_SECONDS", "45"), ("EMBEDDING_MAX_IN_FLIGHT", "8"),
    ("EMBEDDING_MAX_WAITERS", "32"), ("EMBEDDING_QUEUE_TIMEOUT_SECONDS", "2"),
    ("EMBEDDING_TOTAL_TIMEOUT_SECONDS", "45"), ("PROVIDER_RETRY_BACKOFF_SECONDS", "0.1"),
    ("PROVIDER_RETRY_MAX_BACKOFF_SECONDS", "2"), ("BGE_MAX_CONCURRENCY", "1"),
    ("BGE_MAX_QUEUE_SIZE", "32"),
])
def test_budgets_pass_through_env_compose_and_helm(key: str, default: str) -> None:
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[3]
    env = (root / ".env.example").read_text()
    compose = yaml.safe_load((root / "docker/compose.yaml").read_text())
    helm = yaml.safe_load((root / "helm/aegisrag/values.yaml").read_text())
    assert f"{key}={default}" in env.splitlines()
    assert compose["x-api-environment"][key] == "${" + key + ":-" + default + "}"
    assert helm["api"]["env"][key] == default
