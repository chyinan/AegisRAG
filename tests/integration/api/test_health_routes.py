from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.common.health import DependencyStatus, ReadinessData


def _route_for_path(path: str) -> APIRoute:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route
    raise AssertionError(f"Route not registered: {path}")


def test_health_route_returns_success_envelope_and_echoes_request_id() -> None:
    client = TestClient(app)

    response = client.get("/health", headers={"X-Request-ID": "req-health"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"request_id", "data", "error", "metadata"}
    assert body["request_id"] == "req-health"
    assert body["error"] is None
    assert body["metadata"] == {"latency_ms": None}
    assert body["data"] == {
        "status": "ok",
        "service": "api",
        "version": "0.1.0",
    }


def test_health_route_generates_request_id_when_header_is_absent() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    UUID(response.json()["request_id"])


def test_ready_route_returns_structured_dependency_summary() -> None:
    client = TestClient(app)

    response = client.get("/ready", headers={"X-Request-ID": "req-ready"})

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-ready"
    assert body["error"] is None
    assert body["metadata"] == {"latency_ms": None}
    assert body["data"]["ready"] is True

    dependencies = body["data"]["dependencies"]
    assert {dependency["name"] for dependency in dependencies} == {
        "database",
        "redis",
        "minio",
        "vector_store",
    }
    for dependency in dependencies:
        assert dependency["status"] == "not_configured"
        assert dependency["required"] is False
        assert dependency["blocking"] is False
        assert dependency["message"]
        assert isinstance(dependency["details"], dict)


def test_ready_route_returns_dependency_failure_without_secret_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect_readiness(*, context: object) -> ReadinessData:
        return ReadinessData(
            ready=False,
            dependencies=[
                DependencyStatus(
                    name="redis",
                    status="unavailable",
                    required=True,
                    blocking=True,
                    message="redis dependency is unavailable.",
                    details={
                        "configured": True,
                        "latency_ms": 1.2,
                        "error_code": "redis_ping_failed",
                    },
                )
            ],
        )

    monkeypatch.setattr("apps.api.routes.health.collect_readiness", fake_collect_readiness)
    client = TestClient(app)

    response = client.get("/ready", headers={"X-Request-ID": "req-ready-failed"})

    assert response.status_code == 503
    body = response.json()
    assert body["request_id"] == "req-ready-failed"
    assert body["data"]["ready"] is False
    assert body["data"]["dependencies"][0]["details"]["error_code"] == "redis_ping_failed"
    assert "redis://user:password@redis:6379/0" not in response.text
    assert "password" not in response.text.lower()


def test_health_and_ready_routes_do_not_require_auth_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    client = TestClient(app)

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 200


def test_health_routes_are_registered_with_response_models() -> None:
    health_route = _route_for_path("/health")
    ready_route = _route_for_path("/ready")

    assert health_route.response_model is not None
    assert ready_route.response_model is not None

    paths = app.openapi()["paths"]
    assert "/health" in paths
    assert "/ready" in paths
