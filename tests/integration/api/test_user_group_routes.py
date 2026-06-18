"""Integration tests for CRUD /groups and /users endpoints."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.auth.context import AuthContext


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _admin_auth_context() -> AuthContext:
    return AuthContext(
        user_id="admin-test",
        tenant_id="default",
        roles=("admin",),
        permissions=("admin:settings",),
    )


def _override_auth():
    from apps.api.dependencies import get_auth_context
    auth_ctx = _admin_auth_context()
    app.dependency_overrides[get_auth_context] = lambda: auth_ctx


# ── Groups ────────────────────────────────────────────────────

def test_list_groups_returns_all_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.groups import get_group_service

    class StubGroupService:
        async def list_groups(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "g1",
                    "name": "Engineering",
                    "description": "Dev team",
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": "2024-01-01T00:00:00",
                },
                {
                    "id": "g2",
                    "name": "Marketing",
                    "description": None,
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": "2024-01-01T00:00:00",
                },
            ]

    app.dependency_overrides[get_group_service] = lambda: StubGroupService()
    _override_auth()

    client = TestClient(app)

    response = client.get(
        "/groups",
        headers={"X-Request-ID": "req-groups-list"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-groups-list"
    assert body["error"] is None
    assert len(body["data"]) == 2
    assert body["data"][0]["name"] == "Engineering"


def test_create_group_returns_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.groups import get_group_service

    class StubGroupService:
        async def create_group(self, *, name: str, description: str | None) -> dict[str, object]:
            return {
                "id": "g-new",
                "name": name,
                "description": description,
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }

    app.dependency_overrides[get_group_service] = lambda: StubGroupService()
    _override_auth()

    client = TestClient(app)

    response = client.post(
        "/groups",
        headers={"X-Request-ID": "req-groups-create"},
        json={"name": "Design", "description": "Design team"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["name"] == "Design"
    assert body["data"]["id"] == "g-new"


def test_get_group_returns_single(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.groups import get_group_service

    class StubGroupService:
        async def get_group(self, *, group_id: str) -> dict[str, object]:
            return {
                "id": group_id,
                "name": "Engineering",
                "description": "Dev team",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }

    app.dependency_overrides[get_group_service] = lambda: StubGroupService()
    _override_auth()

    client = TestClient(app)

    response = client.get(
        "/groups/g1",
        headers={"X-Request-ID": "req-groups-get"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["id"] == "g1"
    assert body["data"]["name"] == "Engineering"


def test_update_group_returns_updated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.groups import get_group_service

    class StubGroupService:
        async def update_group(
            self, *, group_id: str, name: str | None, description: str | None
        ) -> dict[str, object]:
            return {
                "id": group_id,
                "name": name or "Old",
                "description": description,
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }

    app.dependency_overrides[get_group_service] = lambda: StubGroupService()
    _override_auth()

    client = TestClient(app)

    response = client.put(
        "/groups/g1",
        headers={"X-Request-ID": "req-groups-update"},
        json={"name": "Platform"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["name"] == "Platform"


def test_delete_group_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.groups import get_group_service

    class StubGroupService:
        async def delete_group(self, *, group_id: str) -> None:
            pass

    app.dependency_overrides[get_group_service] = lambda: StubGroupService()
    _override_auth()

    client = TestClient(app)

    response = client.delete(
        "/groups/g1",
        headers={"X-Request-ID": "req-groups-delete"},
    )

    assert response.status_code == 204


# ── Users ─────────────────────────────────────────────────────

def test_list_users_returns_all_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.users import get_user_service

    class StubUserService:
        async def list_users(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "u1",
                    "username": "alice",
                    "email": "alice@example.com",
                    "display_name": "Alice",
                    "is_active": True,
                    "group_id": "g1",
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": "2024-01-01T00:00:00",
                },
            ]

    app.dependency_overrides[get_user_service] = lambda: StubUserService()
    _override_auth()

    client = TestClient(app)

    response = client.get(
        "/users",
        headers={"X-Request-ID": "req-users-list"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-users-list"
    assert body["error"] is None
    assert len(body["data"]) == 1
    assert body["data"][0]["username"] == "alice"


def test_create_user_hashes_password_and_returns_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.users import get_user_service

    class StubUserService:
        async def create_user(
            self,
            *,
            username: str,
            password: str,
            email: str | None,
            display_name: str,
            group_id: str | None,
        ) -> dict[str, object]:
            return {
                "id": "u-new",
                "username": username,
                "email": email,
                "display_name": display_name,
                "is_active": True,
                "group_id": group_id,
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }

    app.dependency_overrides[get_user_service] = lambda: StubUserService()
    _override_auth()

    client = TestClient(app)

    response = client.post(
        "/users",
        headers={"X-Request-ID": "req-users-create"},
        json={
            "username": "bob",
            "password": "Secret123!",
            "email": "bob@example.com",
            "display_name": "Bob",
            "group_id": "g1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["username"] == "bob"
    assert "password" not in str(body["data"])


def test_create_user_rejects_duplicate_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routes.users import get_user_service
    from packages.common.errors import DomainError

    class FailingUserService:
        async def create_user(self, **kwargs: object) -> dict[str, object]:
            raise DomainError(
                code="AUTH_USERNAME_EXISTS",
                message="Username is not available.",
                status_code=409,
            )

    app.dependency_overrides[get_user_service] = lambda: FailingUserService()
    _override_auth()

    client = TestClient(app)

    response = client.post(
        "/users",
        headers={"X-Request-ID": "req-users-dup"},
        json={
            "username": "alice",
            "password": "Secret123!",
            "display_name": "Alice",
        },
    )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "AUTH_USERNAME_EXISTS"
