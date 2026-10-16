from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from apps.api import dependencies
from packages.auth.exceptions import AuthContextInvalidError
from packages.auth.parsers import parse_jwt_claims
from tests.unit.agent.test_agent_run_service import _context


async def test_durable_phase_rechecks_session_and_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revocation, session = AsyncMock(), AsyncMock()
    monkeypatch.setattr(dependencies, "_validate_token_revocation", revocation)
    monkeypatch.setattr(dependencies, "_validate_local_auth_session", session)
    context = _context()
    assert await dependencies.revalidate_authenticated_context(context) is context
    revocation.assert_awaited_once_with(context.auth)
    session.assert_awaited_once_with(context.auth)


@pytest.mark.parametrize("field", ["token_expires_at", "session_expires_at"])
async def test_durable_phase_rejects_expired_auth_before_io(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    revocation = AsyncMock()
    monkeypatch.setattr(dependencies, "_validate_token_revocation", revocation)
    context = _context()
    context = context.model_copy(
        update={
            "auth": context.auth.model_copy(
                update={
                    field: datetime.now(UTC) - timedelta(seconds=1),
                }
            )
        }
    )
    with pytest.raises(AuthContextInvalidError):
        await dependencies.revalidate_authenticated_context(context)
    revocation.assert_not_awaited()


async def test_durable_phase_propagates_live_revocation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dependencies,
        "_validate_token_revocation",
        AsyncMock(
            side_effect=AuthContextInvalidError(details={"reason": "token_revoked"}),
        ),
    )
    with pytest.raises(AuthContextInvalidError):
        await dependencies.revalidate_authenticated_context(_context())


def test_external_jwt_keeps_private_expiry_for_long_running_validation() -> None:
    expires = datetime.now(UTC) + timedelta(minutes=1)
    auth = parse_jwt_claims(
        {
            "type": "access",
            "sub": "user",
            "tenant_id": "tenant",
            "exp": expires.timestamp(),
            "identity_provider": "oidc",
            "permissions": ["agent:run"],
        }
    )
    assert auth.token_expires_at is not None
    assert abs(auth.token_expires_at.timestamp() - expires.timestamp()) < 1
    assert "token_expires_at" not in auth.model_dump()
    assert "token_expires_at" not in repr(auth)


def test_api_session_factory_hides_sql_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from apps.api.factories import common

    engine, sessions = object(), object()
    create_engine = Mock(return_value=engine)
    monkeypatch.setattr(common, "create_async_db_engine", create_engine)
    monkeypatch.setattr(common, "_db_create_session_factory", lambda value: sessions)
    common.create_session_factory.cache_clear()
    try:
        assert common.create_session_factory("sqlite+aiosqlite://") is sessions
        create_engine.assert_called_once_with("sqlite+aiosqlite://", hide_parameters=True)
    finally:
        common.create_session_factory.cache_clear()
