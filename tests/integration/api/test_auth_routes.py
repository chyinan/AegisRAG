"""Integration tests for POST /auth/login."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_login_requires_username_and_password() -> None:
    # Override login service to avoid DATABASE_URL dependency
    from apps.api.routes.auth import get_login_service

    class StubLoginService:
        async def login(self, *, username: str, password: str):
            raise AssertionError("should not be called")

    app.dependency_overrides[get_login_service] = lambda: StubLoginService()

    client = TestClient(app)

    response = client.post(
        "/auth/login",
        headers={"X-Request-ID": "req-login"},
        json={},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_login_returns_jwt_on_valid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.auth import get_login_service
    from packages.auth.login_service import LoginResult

    class StubLoginService:
        async def login(self, *, tenant_id: str, username: str, password: str) -> LoginResult:
            assert tenant_id == "tenant-a"
            return LoginResult(
                access_token="stub-jwt-token",
                refresh_token="stub-refresh-token",
                expires_in=3600,
                user_id="u-stub",
                display_name="Stub User",
                tenant_id=tenant_id,
                roles=["admin"],
                permissions=["admin:settings", "retrieval:query"],
            )

    service = StubLoginService()
    app.dependency_overrides[get_login_service] = lambda: service

    client = TestClient(app)

    response = client.post(
        "/auth/login",
        headers={"X-Request-ID": "req-login"},
        json={"tenant_id": "tenant-a", "username": "alice", "password": "secret123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-login"
    assert body["error"] is None
    assert body["data"]["access_token"] == "stub-jwt-token"
    assert body["data"]["refresh_token"] == "stub-refresh-token"
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["expires_in"] == 3600
    assert body["data"]["user_id"] == "u-stub"
    assert body["data"]["display_name"] == "Stub User"
    assert body["data"]["tenant_id"] == "tenant-a"
    assert body["data"]["roles"] == ["admin"]
    assert body["data"]["permissions"] == ["admin:settings", "retrieval:query"]


def test_login_returns_401_on_invalid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.auth import get_login_service
    from packages.auth.login_service import LoginResult
    from packages.common.errors import DomainError

    class FailingLoginService:
        async def login(self, *, tenant_id: str, username: str, password: str) -> LoginResult:
            assert tenant_id == "tenant-a"
            raise DomainError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid username or password.",
                status_code=401,
            )

    service = FailingLoginService()
    app.dependency_overrides[get_login_service] = lambda: service

    client = TestClient(app)

    response = client.post(
        "/auth/login",
        headers={"X-Request-ID": "req-login-fail"},
        json={"tenant_id": "tenant-a", "username": "evil", "password": "wrong"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_login_requires_explicit_tenant_id() -> None:
    from apps.api.routes.auth import get_login_service

    class StubLoginService:
        async def login(self, *, tenant_id: str, username: str, password: str):
            raise AssertionError("login service must not be called")

    app.dependency_overrides[get_login_service] = lambda: StubLoginService()

    response = TestClient(app).post(
        "/auth/login",
        headers={"X-Request-ID": "req-login-tenant-required"},
        json={"username": "alice", "password": "secret123"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


# ── /auth/refresh tests ──


def test_refresh_returns_new_access_token() -> None:
    from apps.api.routes.auth import get_login_service
    from packages.auth.login_service import LoginResult

    class StubLoginService:
        async def refresh(self, *, refresh_token: str) -> LoginResult:
            return LoginResult(
                access_token="new-access-token",
                refresh_token="new-refresh-token",
                expires_in=3600,
                user_id="u-stub",
                display_name="Stub User",
                tenant_id="default",
                roles=["admin"],
                permissions=["admin:settings", "retrieval:query"],
            )

    app.dependency_overrides[get_login_service] = lambda: StubLoginService()

    client = TestClient(app)

    response = client.post(
        "/auth/refresh",
        headers={"X-Request-ID": "req-refresh"},
        json={"refresh_token": "valid-refresh-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-refresh"
    assert body["error"] is None
    assert body["data"]["access_token"] == "new-access-token"
    assert body["data"]["refresh_token"] == "new-refresh-token"
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["expires_in"] == 3600
    assert body["data"]["user_id"] == "u-stub"
    assert body["data"]["display_name"] == "Stub User"
    assert body["data"]["tenant_id"] == "default"
    assert body["data"]["roles"] == ["admin"]
    assert body["data"]["permissions"] == ["admin:settings", "retrieval:query"]


def test_refresh_rejects_invalid_token() -> None:
    from apps.api.routes.auth import get_login_service
    from packages.auth.login_service import LoginResult
    from packages.common.errors import DomainError

    class FailingLoginService:
        async def refresh(self, *, refresh_token: str) -> LoginResult:
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Invalid or expired refresh token.",
                status_code=401,
            )

    app.dependency_overrides[get_login_service] = lambda: FailingLoginService()

    client = TestClient(app)

    response = client.post(
        "/auth/refresh",
        headers={"X-Request-ID": "req-refresh-invalid"},
        json={"refresh_token": "invalid-token"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_refresh_rejects_access_token_as_refresh() -> None:
    from apps.api.routes.auth import get_login_service
    from packages.auth.login_service import LoginResult
    from packages.common.errors import DomainError

    class FailingLoginService:
        async def refresh(self, *, refresh_token: str) -> LoginResult:
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Token is not a refresh token.",
                status_code=401,
            )

    app.dependency_overrides[get_login_service] = lambda: FailingLoginService()

    client = TestClient(app)

    response = client.post(
        "/auth/refresh",
        headers={"X-Request-ID": "req-refresh-access"},
        json={"refresh_token": "access-token-not-refresh"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_refresh_rejects_expired_token() -> None:
    from apps.api.routes.auth import get_login_service
    from packages.auth.login_service import LoginResult
    from packages.common.errors import DomainError

    class FailingLoginService:
        async def refresh(self, *, refresh_token: str) -> LoginResult:
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Invalid or expired refresh token.",
                status_code=401,
            )

    app.dependency_overrides[get_login_service] = lambda: FailingLoginService()

    client = TestClient(app)

    response = client.post(
        "/auth/refresh",
        headers={"X-Request-ID": "req-refresh-expired"},
        json={"refresh_token": "expired-refresh-token"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_refresh_rejects_inactive_user() -> None:
    from apps.api.routes.auth import get_login_service
    from packages.auth.login_service import LoginResult
    from packages.common.errors import DomainError

    class FailingLoginService:
        async def refresh(self, *, refresh_token: str) -> LoginResult:
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="User no longer exists or is inactive.",
                status_code=401,
            )

    app.dependency_overrides[get_login_service] = lambda: FailingLoginService()

    client = TestClient(app)

    response = client.post(
        "/auth/refresh",
        headers={"X-Request-ID": "req-refresh-inactive"},
        json={"refresh_token": "token-for-inactive-user"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_refresh_requires_refresh_token_field() -> None:
    from apps.api.routes.auth import get_login_service

    class StubLoginService:
        async def refresh(self, *, refresh_token: str):
            raise AssertionError("should not be called")

    app.dependency_overrides[get_login_service] = lambda: StubLoginService()

    client = TestClient(app)

    response = client.post(
        "/auth/refresh",
        headers={"X-Request-ID": "req-refresh-validation"},
        json={},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_logout_revokes_current_access_token() -> None:
    from apps.api.dependencies import get_auth_context, get_token_revocation_store
    from packages.auth.context import AuthContext

    class FakeStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, datetime]] = []
            self.family_calls: list[tuple[str, datetime]] = []

        async def revoke(self, jti: str, expires_at: datetime) -> None:
            self.calls.append((jti, expires_at))

        async def revoke_family(self, session_id: str, expires_at: datetime) -> None:
            self.family_calls.append((session_id, expires_at))

    store = FakeStore()
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
        identity_provider="local",
        auth_version=1,
        jti="access-jti-a",
        session_id="session-a",
        session_expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        token_expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    app.dependency_overrides[get_token_revocation_store] = lambda: store  # type: ignore[assignment]

    response = TestClient(app).post(
        "/auth/logout",
        headers={"X-Request-ID": "req-logout", "Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"revoked": True}
    assert len(store.calls) == 1
    assert store.calls[0][0] == "access-jti-a"
    assert len(store.family_calls) == 1
    assert store.family_calls[0][0] == "session-a"


def test_logout_requires_revocable_token() -> None:
    from apps.api.dependencies import get_auth_context
    from packages.auth.context import AuthContext

    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
    )

    response = TestClient(app).post(
        "/auth/logout",
        headers={"X-Request-ID": "req-logout-no-jti", "Authorization": "Bearer token"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "AUTH_TOKEN_NOT_REVOCABLE"


def test_logout_fails_closed_after_family_revocation_when_access_write_fails() -> None:
    from apps.api.dependencies import get_auth_context, get_token_revocation_store
    from packages.auth.context import AuthContext
    from packages.auth.revocation import TokenRevocationUnavailableError

    class PartialStore:
        family_revoked = False

        async def revoke_family(self, session_id: str, expires_at: datetime) -> None:
            del session_id, expires_at
            self.family_revoked = True

        async def revoke(self, jti: str, expires_at: datetime) -> None:
            del jti, expires_at
            raise TokenRevocationUnavailableError

    store = PartialStore()
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
        identity_provider="local",
        auth_version=1,
        jti="access-jti-a",
        session_id="session-a",
        session_expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        token_expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    app.dependency_overrides[get_token_revocation_store] = lambda: store  # type: ignore[assignment]

    response = TestClient(app).post(
        "/auth/logout",
        headers={"X-Request-ID": "req-logout-partial", "Authorization": "Bearer token"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_REVOCATION_UNAVAILABLE"
    assert store.family_revoked is True
