from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from apps.api.error_handlers import register_error_handlers
from packages.common.errors import DomainError


class _ItemPayload(BaseModel):
    name: str


def _build_error_test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/domain-error")
    def domain_error() -> None:
        raise DomainError(
            code="DOCUMENT_NOT_FOUND",
            message="Document was not found.",
            details={
                "document_id": "doc-123",
                "Authorization": "Bearer secret-token",
            },
        )

    @app.get("/unexpected-error")
    def unexpected_error() -> None:
        raise RuntimeError("secret-token should not be returned")

    @app.post("/items")
    def create_item(payload: _ItemPayload) -> dict[str, str]:
        return {"name": payload.name}

    return app


def test_domain_error_returns_stable_error_envelope_without_sensitive_leakage() -> None:
    client = TestClient(_build_error_test_app())

    response = client.get("/domain-error", headers={"X-Request-ID": "req-error"})

    assert response.status_code == 400
    body = response.json()
    assert body == {
        "request_id": "req-error",
        "data": None,
        "error": {
            "code": "DOCUMENT_NOT_FOUND",
            "message": "Document was not found.",
            "details": {
                "document_id": "doc-123",
                "Authorization": "[REDACTED]",
            },
        },
        "metadata": {"latency_ms": None},
    }
    assert "DomainError" not in response.text
    assert "Traceback" not in response.text
    assert "secret-token" not in response.text


def test_unexpected_error_returns_generic_internal_error_envelope() -> None:
    client = TestClient(_build_error_test_app(), raise_server_exceptions=False)

    response = client.get("/unexpected-error", headers={"X-Request-ID": "req-internal"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-internal"
    body = response.json()
    assert body == {
        "request_id": "req-internal",
        "data": None,
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "Internal server error.",
            "details": {},
        },
        "metadata": {"latency_ms": None},
    }
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
    assert "secret-token" not in response.text


def test_http_errors_return_shared_envelope() -> None:
    client = TestClient(_build_error_test_app())

    not_found = client.get("/missing", headers={"X-Request-ID": "req-missing"})
    method_not_allowed = client.get("/items", headers={"X-Request-ID": "req-method"})

    assert not_found.status_code == 404
    assert not_found.headers["X-Request-ID"] == "req-missing"
    assert not_found.json() == {
        "request_id": "req-missing",
        "data": None,
        "error": {
            "code": "NOT_FOUND",
            "message": "Not Found.",
            "details": {"status_code": 404},
        },
        "metadata": {"latency_ms": None},
    }
    assert method_not_allowed.status_code == 405
    assert method_not_allowed.json()["error"] == {
        "code": "METHOD_NOT_ALLOWED",
        "message": "Method Not Allowed.",
        "details": {"status_code": 405},
    }


def test_validation_error_returns_shared_envelope_without_raw_input() -> None:
    client = TestClient(_build_error_test_app())

    response = client.post(
        "/items",
        headers={"X-Request-ID": "req-validation"},
        json={"name": 123},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["request_id"] == "req-validation"
    assert body["data"] is None
    assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert body["error"]["message"] == "Request validation failed."
    assert body["error"]["details"]["errors"] == [
        {
            "loc": ["body", "name"],
            "msg": "Input should be a valid string",
            "type": "string_type",
        }
    ]
    assert "input" not in response.text
