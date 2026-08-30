"""FastAPI authentication binds tenant scope before RLS-backed validation."""

from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from apps.api import dependencies
from packages.auth.context import AuthContext
from packages.common.tenant_context import current_tenant_id


class _SessionFactoryProbe:
    def __call__(self) -> "_SessionFactoryProbe":
        return self

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/secure",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("test", 0),
            "server": ("test", 80),
            "root_path": "",
        }
    )


@pytest.mark.asyncio
async def test_local_auth_validation_runs_inside_tenant_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str | None] = []
    auth = AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
        identity_provider="local",
        auth_version=1,
    )

    class ProbeValidator:
        def __init__(self, session: object) -> None:
            del session

        async def validate(self, candidate: AuthContext) -> None:
            observed.append(current_tenant_id())
            assert candidate == auth

    monkeypatch.setattr(dependencies, "decode_jwt_token", lambda token, settings: auth)
    monkeypatch.setattr(
        dependencies,
        "ServiceTokenSettings",
        SimpleNamespace(from_environment=lambda: SimpleNamespace(has_records=lambda: False)),
    )
    monkeypatch.setattr(
        dependencies,
        "load_settings",
        lambda: SimpleNamespace(database_url="sqlite+aiosqlite://"),
    )
    monkeypatch.setattr(dependencies, "_cached_session_factory", lambda url: _SessionFactoryProbe())
    monkeypatch.setattr(dependencies, "LocalAuthSessionValidator", ProbeValidator)

    result = await dependencies.get_auth_context(
        _request(),
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"),
    )

    assert result == auth
    assert observed == ["tenant-a"]
    assert current_tenant_id() is None


@pytest.mark.asyncio
async def test_revoked_local_access_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
        identity_provider="local",
        auth_version=1,
        jti="revoked-jti",
    )

    class RevokedStore:
        async def is_revoked(self, jti: str) -> bool:
            assert jti == "revoked-jti"
            return True

    monkeypatch.setattr(dependencies, "decode_jwt_token", lambda token, settings: auth)
    monkeypatch.setattr(
        dependencies,
        "ServiceTokenSettings",
        SimpleNamespace(from_environment=lambda: SimpleNamespace(has_records=lambda: False)),
    )
    monkeypatch.setattr(dependencies, "get_token_revocation_store", lambda: RevokedStore())

    with pytest.raises(dependencies.AuthContextInvalidError) as exc_info:
        await dependencies.get_auth_context(
            _request(),
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"),
        )

    assert exc_info.value.details == {"reason": "token_revoked"}


@pytest.mark.asyncio
async def test_access_token_in_revoked_session_family_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
        identity_provider="local",
        auth_version=1,
        jti="active-jti",
        session_id="revoked-session",
    )

    class FamilyStore:
        async def is_revoked(self, jti: str) -> bool:
            assert jti == "active-jti"
            return False

        async def is_family_revoked(self, session_id: str) -> bool:
            assert session_id == "revoked-session"
            return True

    monkeypatch.setattr(dependencies, "decode_jwt_token", lambda token, settings: auth)
    monkeypatch.setattr(
        dependencies,
        "ServiceTokenSettings",
        SimpleNamespace(from_environment=lambda: SimpleNamespace(has_records=lambda: False)),
    )
    monkeypatch.setattr(dependencies, "get_token_revocation_store", lambda: FamilyStore())

    with pytest.raises(dependencies.AuthContextInvalidError) as exc_info:
        await dependencies.get_auth_context(
            _request(),
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"),
        )

    assert exc_info.value.details == {"reason": "token_revoked"}


@pytest.mark.asyncio
async def test_oidc_jwks_verifier_is_used_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    class FakeVerifier:
        async def verify(self, token: str) -> dict[str, object]:
            observed.append(token)
            return {
                "sub": "user-oidc",
                "tenant_id": "tenant-oidc",
                "exp": 4_000_000_000,
            }

    monkeypatch.setenv("JWT_JWKS_URL", "https://issuer.example/jwks")
    monkeypatch.setenv("JWT_REQUIRE_LOCAL_SESSION", "false")
    monkeypatch.setattr(dependencies, "_cached_oidc_verifier", lambda settings: FakeVerifier())
    monkeypatch.setattr(
        dependencies,
        "ServiceTokenSettings",
        SimpleNamespace(from_environment=lambda: SimpleNamespace(has_records=lambda: False)),
    )

    result = await dependencies.get_auth_context(
        _request(),
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="oidc-token"),
    )

    assert result.user_id == "user-oidc"
    assert result.tenant_id == "tenant-oidc"
    assert observed == ["oidc-token"]
