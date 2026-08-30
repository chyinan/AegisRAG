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
