from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import apps.api.main as api_main
from apps.api.factories.agent import create_agent_runtime
from apps.api.main import create_app
from apps.api.service_dependencies import get_durable_agent_service
from packages.agent.adapters.document_evidence import DocumentEvidenceRevalidator
from packages.agent.dto import ToolRateLimit
from packages.agent.durable.service import DurableAgentService
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import (
    AgentRunConfig,
    AgentRuntime,
    AgentRuntimeState,
    AgentStepDecision,
)
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.tools.calculator import build_calculator_tool
from packages.common.audit import InMemoryAuditPort
from packages.data.storage.base import Base
from packages.data.storage.repositories import DocumentRepository


class ArithmeticStepper:
    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        if state.tool_calls_used == 0:
            return AgentStepDecision.tool_call("calculator", {"expression": "2+2"})
        return AgentStepDecision(action="final_answer", final_answer="The result is 4.")


@pytest.fixture
async def durable_app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    """Each case owns its middleware, limiter state and dependency overrides."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    # Keep pytest's logging capture and the process-wide tracing setup intact.
    # Request logging middleware and every route/security assertion still run.
    monkeypatch.setattr(api_main, "configure_logging", lambda: None)
    monkeypatch.setattr(api_main, "setup_tracing", lambda **kwargs: False)
    monkeypatch.setattr(api_main, "instrument_app", lambda *args, **kwargs: None)
    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture(params=("native", "langgraph"))
async def durable_client(
    request: pytest.FixtureRequest,
    durable_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    backend = request.param
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'durable-api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    audit = InMemoryAuditPort()
    registry = ToolRegistry(audit=audit)
    registry.register(
        build_calculator_tool(
            timeout_seconds=2,
            rate_limit=ToolRateLimit(max_calls=20, window_seconds=60),
        )
    )

    def runtime(config: AgentRunConfig, run_id: str) -> AgentRuntime:
        return create_agent_runtime(
            backend=backend,
            registry=registry,
            stepper=ArithmeticStepper(),
            audit=audit,
            config=config,
            agent_run_id=run_id,
        )

    async def service() -> AsyncIterator[DurableAgentService]:
        async with sessions() as session:
            yield DurableAgentService(
                store=SqlAlchemyDurableStore(sessions),
                runtime_factory=runtime,
                evidence_revalidator=DocumentEvidenceRevalidator(
                    repository=DocumentRepository(session),
                ),
                backend=backend,
            )

    durable_app.dependency_overrides[get_durable_agent_service] = service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=durable_app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        durable_app.dependency_overrides.pop(get_durable_agent_service, None)
        await engine.dispose()


def _headers(permissions: str = "agent:run,agent:tool:calculator") -> dict[str, str]:
    return {
        "X-Tenant-ID": "tenant-1",
        "X-User-ID": "user-1",
        "X-Request-ID": "request-1",
        "X-Trace-ID": "trace-1",
        "X-Roles": "analyst",
        "X-Permissions": permissions,
    }


async def test_durable_api_approval_resume_and_read(durable_client: httpx.AsyncClient) -> None:
    started = await durable_client.post(
        "/agent/durable",
        headers=_headers(),
        json={
            "input": "Calculate two plus two",
            "approval_tools": ["calculator"],
        },
    )
    assert started.status_code == 200, started.text
    paused = started.json()["data"]
    assert paused["status"] == "awaiting_approval"
    assert paused["final_answer"] is None
    assert paused["approval"]["arguments"] == {"expression": "2+2"}
    resumed = await durable_client.post(
        f"/agent/durable/{paused['run_id']}/resume",
        headers=_headers(),
        json={
            "expected_revision": paused["revision"],
            "approval_id": paused["approval"]["approval_id"],
            "approve": True,
        },
    )
    assert resumed.status_code == 200, resumed.text
    result = resumed.json()["data"]
    assert result["status"] == "completed"
    assert result["final_answer"] == "The result is 4."
    assert result["tool_calls_used"] == 1
    fetched = await durable_client.get(f"/agent/durable/{paused['run_id']}", headers=_headers())
    assert fetched.status_code == 200
    assert fetched.json()["data"] == result
    stale = await durable_client.post(
        f"/agent/durable/{paused['run_id']}/resume",
        headers=_headers(),
        json={"expected_revision": result["revision"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "DURABLE_RUN_TERMINAL"


async def test_durable_api_stale_revision_cancel_and_no_resurrection(
    durable_client: httpx.AsyncClient,
) -> None:
    started = await durable_client.post(
        "/agent/durable",
        headers=_headers(),
        json={
            "input": "Calculate",
            "approval_tools": ["calculator"],
        },
    )
    paused = started.json()["data"]
    prefix = f"/agent/durable/{paused['run_id']}"
    stale = await durable_client.post(
        f"{prefix}/resume",
        headers=_headers(),
        json={
            "expected_revision": paused["revision"] - 1,
        },
    )
    assert stale.status_code == 409
    cancelled = await durable_client.post(
        f"{prefix}/cancel",
        headers=_headers(),
        json={
            "expected_revision": paused["revision"],
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    result = cancelled.json()["data"]
    assert result["status"] == "cancelled"
    assert result["tool_calls_used"] == 0
    retried = await durable_client.post(
        f"{prefix}/resume",
        headers=_headers(),
        json={
            "expected_revision": result["revision"],
            "approval_id": paused["approval"]["approval_id"],
            "approve": True,
        },
    )
    assert retried.status_code == 409
    assert (await durable_client.get(prefix, headers=_headers())).json()["data"] == result


async def test_durable_api_rejects_missing_permission_before_start(
    durable_client: httpx.AsyncClient,
) -> None:
    denied = await durable_client.post("/agent/durable", headers=_headers(""), json={"input": "x"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "AGENT_RUN_FORBIDDEN"


async def test_durable_feature_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, durable_app: FastAPI,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    monkeypatch.setenv("AGENT_DURABLE_ENABLED", "false")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=durable_app),
        base_url="http://test",
    ) as client:
        result = await client.post("/agent/durable", headers=_headers(), json={"input": "x"})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "DURABLE_AGENT_DISABLED"
