"""Integration tests for POST /auth/login."""

from collections.abc import Iterator

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
        async def login(self, *, username: str, password: str) -> LoginResult:
            return LoginResult(
                access_token="stub-jwt-token",
                refresh_token="stub-refresh-token",
                expires_in=3600,
                user_id="u-stub",
                display_name="Stub User",
                tenant_id="default",
                roles=["admin"],
                permissions=["admin:settings", "retrieval:query"],
            )

    service = StubLoginService()
    app.dependency_overrides[get_login_service] = lambda: service

    client = TestClient(app)

    response = client.post(
        "/auth/login",
        headers={"X-Request-ID": "req-login"},
        json={"username": "alice", "password": "secret123"},
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
    assert body["data"]["tenant_id"] == "default"
    assert body["data"]["roles"] == ["admin"]
    assert body["data"]["permissions"] == ["admin:settings", "retrieval:query"]


def test_login_returns_401_on_invalid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.auth import get_login_service
    from packages.auth.login_service import LoginResult
    from packages.common.errors import DomainError

    class FailingLoginService:
        async def login(self, *, username: str, password: str) -> LoginResult:
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
        json={"username": "evil", "password": "wrong"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


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
