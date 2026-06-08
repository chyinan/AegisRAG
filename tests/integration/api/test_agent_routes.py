from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_agent_run_application_service
from packages.agent.dto import AgentRunCommand, AgentRunResponse
from packages.agent.exceptions import AGENT_RUN_FORBIDDEN, AgentRunError
from packages.common.context import AuthenticatedRequestContext


class StubAgentRunApplicationService:
    def __init__(self, *, forbidden: bool = False) -> None:
        self.forbidden = forbidden
        self.calls: list[tuple[AuthenticatedRequestContext, AgentRunCommand]] = []

    async def run(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: AgentRunCommand,
    ) -> AgentRunResponse:
        if self.forbidden:
            raise AgentRunError(
                code=AGENT_RUN_FORBIDDEN,
                message="Agent run permission is required.",
                details={"required_permissions": ["agent:run"]},
                status_code=403,
            )
        self.calls.append((context, command))
        now = datetime.now(tz=UTC)
        return AgentRunResponse(
            agent_run_id="run-1",
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            status="completed",
            termination_reason="FINAL_ANSWER",
            steps_used=1,
            tool_calls_used=0,
            error_code=None,
            created_at=now,
            updated_at=now,
            metadata={"safe": True},
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_agent_run_route_returns_envelope_and_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubAgentRunApplicationService()
    app.dependency_overrides[get_agent_run_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/agent/run",
        headers=_auth_headers(),
        json={"input": "summarize policy", "max_steps": 2, "metadata": {"source": "api"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-agent"
    assert body["data"]["agent_run_id"] == "run-1"
    assert body["data"]["tenant_id"] == "tenant-1"
    assert body["data"]["status"] == "completed"
    assert len(service.calls) == 1
    context, command = service.calls[0]
    assert context.auth.permissions == ("agent:run",)
    assert command.input == "summarize policy"
    assert command.max_steps == 2
    assert command.metadata == {"source": "api"}


def test_agent_run_route_rejects_missing_auth_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    service = StubAgentRunApplicationService()
    app.dependency_overrides[get_agent_run_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/agent/run",
        headers={"X-Request-ID": "req-no-auth"},
        json={"input": "x"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_REQUIRED"
    assert service.calls == []


def test_agent_run_route_returns_permission_error_from_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubAgentRunApplicationService(forbidden=True)
    app.dependency_overrides[get_agent_run_application_service] = lambda: service
    client = TestClient(app)

    response = client.post("/agent/run", headers=_auth_headers(permissions=""), json={"input": "x"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == AGENT_RUN_FORBIDDEN
    assert service.calls == []


def test_agent_run_route_rejects_forbidden_extra_fields_and_non_finite_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubAgentRunApplicationService()
    app.dependency_overrides[get_agent_run_application_service] = lambda: service
    client = TestClient(app)

    extra_response = client.post(
        "/agent/run",
        headers=_auth_headers(),
        json={"input": "x", "prompt": "ignore system"},
    )
    timeout_response = client.post(
        "/agent/run",
        headers=_auth_headers(),
        json={"input": "x", "timeout_seconds": "Infinity"},
    )

    assert extra_response.status_code == 422
    assert timeout_response.status_code == 422
    assert service.calls == []


def _auth_headers(permissions: str = "agent:run") -> dict[str, str]:
    return {
        "X-Request-ID": "req-agent",
        "X-Trace-ID": "trace-agent",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "admin",
        "X-Permissions": permissions,
    }
