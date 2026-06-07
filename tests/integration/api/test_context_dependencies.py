from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt import encode

from apps.api.dependencies import (
    AuthenticatedRequestContextDep,
    RequestContextDep,
    get_auth_context,
)
from apps.api.error_handlers import register_auth_error_handlers
from apps.api.middleware import RequestLoggingMiddleware
from packages.auth.context import AuthContext
from packages.auth.parsers import parse_auth_fixture
from packages.common.context import AuthenticatedRequestContext

TEST_JWT_SECRET = "test-secret-with-at-least-32-bytes"


def _build_context_test_app(
    service: Callable[[AuthenticatedRequestContext], dict[str, object]] | None = None,
    *,
    with_error_handlers: bool = False,
    with_request_logging: bool = False,
) -> FastAPI:
    app = FastAPI()
    if with_request_logging:
        app.add_middleware(RequestLoggingMiddleware)
    if with_error_handlers:
        register_auth_error_handlers(app)

    @app.get("/public-context")
    def public_context(context: RequestContextDep) -> dict[str, object]:
        return context.model_dump()

    @app.get("/secure-context")
    def secure_context(context: AuthenticatedRequestContextDep) -> dict[str, object]:
        if service is None:
            return context.auth.model_dump()
        return service(context)

    return app


def test_request_context_dependency_uses_headers_and_generates_missing_ids() -> None:
    client = TestClient(_build_context_test_app())

    explicit = client.get(
        "/public-context",
        headers={
            "X-Request-ID": "req-123",
            "X-Trace-ID": "trace-123",
            "X-Session-ID": "session-123",
        },
    )
    generated = client.get("/public-context")

    assert explicit.json() == {
        "request_id": "req-123",
        "trace_id": "trace-123",
        "session_id": "session-123",
    }
    UUID(generated.json()["request_id"])
    UUID(generated.json()["trace_id"])
    assert generated.json()["session_id"] is None


def test_request_context_dependency_generates_ids_for_blank_headers() -> None:
    client = TestClient(_build_context_test_app())

    response = client.get(
        "/public-context",
        headers={
            "X-Request-ID": "   ",
            "X-Trace-ID": "   ",
        },
    )

    assert response.status_code == 200
    body = response.json()
    UUID(body["request_id"])
    UUID(body["trace_id"])


def test_authenticated_context_dependency_accepts_enabled_dev_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    client = TestClient(_build_context_test_app())

    response = client.get(
        "/secure-context",
        headers={
            "X-Request-ID": "req-123",
            "X-Trace-ID": "trace-123",
            "X-User-ID": "user-123",
            "X-Tenant-ID": "tenant-abc",
            "X-Roles": "admin, knowledge_manager",
            "X-Department": "HR",
            "X-Permissions": "document:read, retrieval:query",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user-123",
        "tenant_id": "tenant-abc",
        "roles": ["admin", "knowledge_manager"],
        "department": "HR",
        "permissions": ["document:read", "retrieval:query"],
    }


def test_authenticated_context_dependency_rejects_dev_headers_when_environment_is_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    client = TestClient(_build_context_test_app(with_error_handlers=True))

    response = client.get(
        "/secure-context",
        headers={
            "X-Request-ID": "req-prod",
            "X-User-ID": "user-123",
            "X-Tenant-ID": "tenant-abc",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_REQUIRED"


def test_authenticated_context_dependency_accepts_verified_jwt_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    client = TestClient(_build_context_test_app())
    token = encode(
        {
            "sub": "user-123",
            "tenant_id": "tenant-abc",
            "roles": ["admin"],
            "permissions": ["document:read"],
            "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        },
        TEST_JWT_SECRET,
        "HS256",
    )

    response = client.get(
        "/secure-context",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user-123",
        "tenant_id": "tenant-abc",
        "roles": ["admin"],
        "department": None,
        "permissions": ["document:read"],
    }


def test_authenticated_context_dependency_allows_test_fixture_override() -> None:
    app = _build_context_test_app()

    def override_auth_context() -> AuthContext:
        return parse_auth_fixture(
            {
                "user_id": "fixture-user",
                "tenant_id": "fixture-tenant",
                "roles": ["tester"],
                "permissions": ["fixture:use"],
            }
        )

    app.dependency_overrides[get_auth_context] = override_auth_context
    client = TestClient(app)

    response = client.get("/secure-context")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "fixture-user",
        "tenant_id": "fixture-tenant",
        "roles": ["tester"],
        "department": None,
        "permissions": ["fixture:use"],
    }


def test_missing_auth_context_returns_envelope_and_does_not_call_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    calls: list[AuthenticatedRequestContext] = []

    def service(context: AuthenticatedRequestContext) -> dict[str, object]:
        calls.append(context)
        return {"called": True}

    client = TestClient(_build_context_test_app(service, with_error_handlers=True))

    response = client.get("/secure-context", headers={"X-Request-ID": "req-missing"})

    assert response.status_code == 401
    assert response.json() == {
        "request_id": "req-missing",
        "data": None,
        "error": {
            "code": "AUTH_CONTEXT_REQUIRED",
            "message": "Authentication context is required.",
            "details": {"missing": ["auth_context"]},
        },
        "metadata": {"latency_ms": None},
    }
    assert calls == []


def test_partial_dev_header_context_returns_required_error_and_does_not_call_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    calls: list[AuthenticatedRequestContext] = []

    def service(context: AuthenticatedRequestContext) -> dict[str, object]:
        calls.append(context)
        return {"called": True}

    client = TestClient(_build_context_test_app(service, with_error_handlers=True))

    response = client.get(
        "/secure-context",
        headers={
            "X-Request-ID": "req-partial",
            "X-User-ID": "user-123",
        },
    )

    assert response.status_code == 401
    body = response.json()
    assert body["request_id"] == "req-partial"
    assert body["data"] is None
    assert body["error"]["code"] == "AUTH_CONTEXT_REQUIRED"
    assert body["error"]["message"] == "Authentication context is required."
    assert body["error"]["details"] == {"missing": ["tenant_id"]}
    assert body["metadata"] == {"latency_ms": None}
    assert calls == []


def test_authenticated_context_dependency_passes_complete_context_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    calls: list[AuthenticatedRequestContext] = []

    def service(context: AuthenticatedRequestContext) -> dict[str, object]:
        calls.append(context)
        return {"request_id": context.request_id, "tenant_id": context.auth.tenant_id}

    client = TestClient(_build_context_test_app(service))

    response = client.get(
        "/secure-context",
        headers={
            "X-Request-ID": "req-service",
            "X-Trace-ID": "trace-service",
            "X-Session-ID": "session-service",
            "X-User-ID": "user-123",
            "X-Tenant-ID": "tenant-abc",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"request_id": "req-service", "tenant_id": "tenant-abc"}
    assert len(calls) == 1
    context = calls[0]
    assert context.request_id == "req-service"
    assert context.trace_id == "trace-service"
    assert context.session_id == "session-service"
    assert context.auth == AuthContext(user_id="user-123", tenant_id="tenant-abc")


def test_request_logging_middleware_and_dependency_reuse_request_context() -> None:
    client = TestClient(_build_context_test_app(with_request_logging=True))

    response = client.get("/public-context")

    assert response.status_code == 200
    body = response.json()
    UUID(body["request_id"])
    assert response.headers["X-Request-ID"] == body["request_id"]
