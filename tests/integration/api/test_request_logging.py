import json
import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from apps.api.dependencies import AuthenticatedRequestContextDep, RequestContextDep
from apps.api.error_handlers import register_error_handlers
from apps.api.middleware import RequestLoggingMiddleware
from packages.common.envelope import ApiResponse, success_response
from packages.common.errors import DomainError
from packages.common.logging import REQUEST_COMPLETED_EVENT, configure_logging


class _ItemPayload(BaseModel):
    name: str


@pytest.fixture(autouse=True)
def _enable_dev_auth_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")


def _build_logging_test_app() -> FastAPI:
    configure_logging()
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    register_error_handlers(app)

    @app.get("/public", response_model=ApiResponse[dict[str, str]])
    def public(context: RequestContextDep) -> ApiResponse[dict[str, str]]:
        return success_response(
            request_id=context.request_id,
            data={"request_id": context.request_id},
        )

    @app.get("/secure", response_model=ApiResponse[dict[str, str]])
    def secure(context: AuthenticatedRequestContextDep) -> ApiResponse[dict[str, str]]:
        return success_response(
            request_id=context.request_id,
            data={"tenant_id": context.auth.tenant_id},
        )

    @app.get("/domain-error")
    def domain_error() -> None:
        raise DomainError(code="EXPECTED_FAILURE", message="Expected failure.")

    @app.get("/unexpected-error")
    def unexpected_error() -> None:
        raise RuntimeError("secret-token should not be returned")

    @app.post("/items")
    def create_item(payload: _ItemPayload) -> dict[str, str]:
        return {"name": payload.name}

    return app


def _request_log_events(caplog: pytest.LogCaptureFixture) -> Iterator[dict[str, object]]:
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except json.JSONDecodeError:
            continue
        if payload.get("event") == REQUEST_COMPLETED_EVENT:
            yield payload


def test_request_logging_records_success_with_context_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="apps.api.request")
    client = TestClient(_build_logging_test_app())

    response = client.get(
        "/secure",
        headers={
            "X-Request-ID": "req-success",
            "X-Trace-ID": "trace-success",
            "X-Session-ID": "session-success",
            "X-User-ID": "user-123",
            "X-Tenant-ID": "tenant-abc",
            "X-Roles": "admin, reviewer",
            "X-Permissions": "document:read, audit:read",
            "X-Api-Key": "secret-token",
        },
    )

    assert response.status_code == 200
    events = list(_request_log_events(caplog))
    assert len(events) == 1
    event = events[0]
    assert event["request_id"] == "req-success"
    assert event["trace_id"] == "trace-success"
    assert event["session_id"] == "session-success"
    assert event["tenant_id"] == "tenant-abc"
    assert event["user_id"] == "user-123"
    assert event["method"] == "GET"
    assert event["path"] == "/secure"
    assert event["status_code"] == 200
    assert event["error_code"] is None
    assert event["role_count"] == 2
    assert event["permission_count"] == 2
    assert isinstance(event["latency_ms"], float)
    assert "secret-token" not in caplog.text


def test_request_logging_records_auth_failure_and_domain_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="apps.api.request")
    client = TestClient(_build_logging_test_app())

    auth_failure = client.get("/secure", headers={"X-Request-ID": "req-auth-fail"})
    expected_failure = client.get(
        "/domain-error",
        headers={"X-Request-ID": "req-domain-fail", "X-Trace-ID": "trace-domain-fail"},
    )

    assert auth_failure.status_code == 401
    assert expected_failure.status_code == 400
    events = list(_request_log_events(caplog))
    assert [event["request_id"] for event in events] == ["req-auth-fail", "req-domain-fail"]
    assert events[0]["tenant_id"] is None
    assert events[0]["user_id"] is None
    assert events[0]["error_code"] == "AUTH_CONTEXT_REQUIRED"
    assert events[1]["trace_id"] == "trace-domain-fail"
    assert events[1]["error_code"] == "EXPECTED_FAILURE"


def test_request_logging_records_unexpected_exception_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="apps.api.request")
    client = TestClient(_build_logging_test_app(), raise_server_exceptions=False)

    response = client.get("/unexpected-error", headers={"X-Request-ID": "req-internal"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-internal"
    events = list(_request_log_events(caplog))
    assert len(events) == 1
    assert events[0]["request_id"] == "req-internal"
    assert events[0]["status_code"] == 500
    assert events[0]["error_code"] == "INTERNAL_ERROR"
    assert "secret-token" not in response.text


def test_request_logging_records_http_and_validation_error_codes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="apps.api.request")
    client = TestClient(_build_logging_test_app())

    not_found = client.get("/missing", headers={"X-Request-ID": "req-missing"})
    validation = client.post(
        "/items",
        headers={"X-Request-ID": "req-validation"},
        json={"name": 123},
    )

    assert not_found.status_code == 404
    assert validation.status_code == 422
    events = list(_request_log_events(caplog))
    assert [event["request_id"] for event in events] == ["req-missing", "req-validation"]
    assert events[0]["error_code"] == "NOT_FOUND"
    assert events[1]["error_code"] == "REQUEST_VALIDATION_ERROR"
