from __future__ import annotations

from collections.abc import AsyncIterator
from importlib import import_module
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy import Connection, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import apps.api.main as api_main
from apps.api.factories.agent import create_agent_runtime
from apps.api.factories.domain import get_durable_agent_service
from apps.api.factories.support_tickets import get_support_ticket_service
from apps.api.main import create_app
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
from packages.agent.storage.session_scoped import SessionFactoryEvidenceRevalidator
from packages.agent.tools.support_ticket import build_support_ticket_tool
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.base import Base
from packages.support_ticket.dto import CreateSupportTicketInput, SupportTicketStatusUpdate
from packages.support_ticket.models import SupportTicketModel
from packages.support_ticket.service import SupportTicketService

REFERENCE = "47f9901f-753e-4ac8-a258-bf77c13a9c42"
ARGUMENTS = {
    "client_reference": REFERENCE,
    "title": "Workstation issue",
    "description": "Please investigate the workstation.",
    "priority": "normal",
}
OWNER_PERMISSIONS = "agent:run,agent:tool:create_support_ticket,support:ticket:read"


def headers(*, manager: bool = False) -> dict[str, str]:
    return {
        "X-Tenant-ID": "tenant-1",
        "X-User-ID": "manager-1" if manager else "user-1",
        "X-Request-ID": "request-support",
        "X-Trace-ID": "trace-support",
        "X-Permissions": "support:ticket:manage" if manager else OWNER_PERMISSIONS,
    }


def owner_context(*, manager: bool = False) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="request-support",
        trace_id="trace-support",
        auth=AuthContext(
            tenant_id="tenant-1",
            user_id="manager-1" if manager else "user-1",
            permissions=("support:ticket:manage",)
            if manager
            else tuple(OWNER_PERMISSIONS.split(",")),
        ),
    )


def create_migrated_tables(connection: Connection) -> None:
    Base.metadata.create_all(
        connection,
        tables=[table for table in Base.metadata.sorted_tables if table.name != "support_tickets"],
    )
    migration = import_module("migrations.versions.20261016_0021_support_tickets")
    assert migration.down_revision == "20261016_0020"
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    indexes = {index["name"] for index in inspect(connection).get_indexes("support_tickets")}
    assert "uq_support_ticket_owner_reference" in indexes


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'support.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(create_migrated_tables)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_persisted_reference_idempotency_and_complete_status_lifecycle(database) -> None:
    service = SupportTicketService(database)
    command = CreateSupportTicketInput.model_validate(ARGUMENTS)
    first = await service.create(context=owner_context(), command=command)
    second = await SupportTicketService(database).create(context=owner_context(), command=command)
    assert first == second
    assert first.status == "open" and first.revision == 1 and first.tool_version == "1"
    assert first.user_id == first.created_by == "user-1"
    in_progress = await service.update_status(
        context=owner_context(manager=True),
        ticket_id=first.ticket_id,
        command=SupportTicketStatusUpdate(expected_revision=1, status="in_progress"),
    )
    resolved = await service.update_status(
        context=owner_context(manager=True),
        ticket_id=first.ticket_id,
        command=SupportTicketStatusUpdate(expected_revision=2, status="resolved"),
    )
    assert in_progress.revision == 2 and resolved.revision == 3
    assert resolved.status == "resolved"
    repeated = await service.create(context=owner_context(), command=command)
    assert repeated.ticket_id == first.ticket_id and repeated.status == "resolved"
    assert (await service.list_owned(context=owner_context())).items == (resolved,)
    assert await service.get_owned(context=owner_context(), ticket_id=first.ticket_id) == resolved
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(SupportTicketModel)) == 1
        events = list(await session.scalars(select(AuditLogModel)))
        assert [event.action for event in events] == [
            "support_ticket.created",
            "support_ticket.status_changed",
            "support_ticket.status_changed",
        ]
        assert [event.user_id for event in events] == ["user-1", "manager-1", "manager-1"]
        for event in events:
            assert set(event.metadata_) == {"ticket_status", "revision"}


class SupportStepper:
    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        if not state.observations:
            return AgentStepDecision.tool_call("create_support_ticket", ARGUMENTS)
        projection = state.observations[-1].prompt_context
        assert set(projection) == {"ticket_id", "status"}
        UUID(str(projection["ticket_id"]))
        assert projection["status"] == "open"
        return AgentStepDecision(action="final_answer", final_answer="Your support ticket is open.")


@pytest.fixture(params=("native", "langgraph"))
async def workflow_client(
    request: pytest.FixtureRequest,
    database: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    backend: Literal["native", "langgraph"] = request.param
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    monkeypatch.setattr(api_main, "configure_logging", lambda: None)
    monkeypatch.setattr(api_main, "setup_tracing", lambda **kwargs: False)
    monkeypatch.setattr(api_main, "instrument_app", lambda *args, **kwargs: None)
    application: FastAPI = create_app()
    audit = InMemoryAuditPort()
    tickets = SupportTicketService(database)
    registry = ToolRegistry(audit=audit)
    registry.register(
        build_support_ticket_tool(
            service=tickets,
            timeout_seconds=3,
            rate_limit=ToolRateLimit(max_calls=20, window_seconds=60),
        )
    )

    def runtime(config: AgentRunConfig, run_id: str) -> AgentRuntime:
        return create_agent_runtime(
            backend=backend,
            registry=registry,
            stepper=SupportStepper(),
            audit=audit,
            config=config,
            agent_run_id=run_id,
        )

    def durable_service() -> DurableAgentService:
        # A fresh service per HTTP operation exercises persisted approval state.
        return DurableAgentService(
            store=SqlAlchemyDurableStore(database),
            runtime_factory=runtime,
            evidence_revalidator=SessionFactoryEvidenceRevalidator(database),
            backend=backend,
        )

    application.dependency_overrides[get_durable_agent_service] = durable_service
    application.dependency_overrides[get_support_ticket_service] = lambda: tickets
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            yield client


async def test_approval_create_read_idempotent_create_and_manager_resolve(workflow_client) -> None:
    client = workflow_client

    async def approve_one_request(expected_existing: int) -> dict[str, object]:
        started = await client.post(
            "/agent/durable",
            headers=headers(),
            json={"input": "Create an internal support ticket"},
        )
        assert started.status_code == 200, started.text
        paused = started.json()["data"]
        assert paused["status"] == "awaiting_approval"
        assert paused["tool_calls_used"] == 0
        assert paused["approval"]["tool_name"] == "create_support_ticket"
        assert paused["approval"]["arguments"] == ARGUMENTS
        pending_list = await client.get("/support-tickets", headers=headers())
        assert pending_list.status_code == 200
        assert len(pending_list.json()["data"]["items"]) == expected_existing
        resumed = await client.post(
            f"/agent/durable/{paused['run_id']}/resume",
            headers=headers(),
            json={
                "expected_revision": paused["revision"],
                "approval_id": paused["approval"]["approval_id"],
                "approve": True,
            },
        )
        assert resumed.status_code == 200, resumed.text
        result = resumed.json()["data"]
        assert result["status"] == "completed", result
        assert result["tool_calls_used"] == 1
        fetched = await client.get(f"/agent/durable/{paused['run_id']}", headers=headers())
        assert fetched.status_code == 200 and fetched.json()["data"] == result
        return result

    before = await client.get("/support-tickets", headers=headers())
    assert before.status_code == 200 and before.json()["data"]["items"] == []
    await approve_one_request(expected_existing=0)
    after = await client.get("/support-tickets", headers=headers())
    items = after.json()["data"]["items"]
    assert len(items) == 1 and items[0]["status"] == "open"
    ticket = items[0]
    fetched = await client.get(f"/support-tickets/{ticket['ticket_id']}", headers=headers())
    assert fetched.status_code == 200 and fetched.json()["data"] == ticket
    await approve_one_request(expected_existing=1)
    repeated = await client.get("/support-tickets", headers=headers())
    assert repeated.json()["data"]["items"] == items
    managed = await client.get(
        "/v1/support-tickets/manage",
        headers=headers(manager=True),
        params={"status": "open", "ticket_id": ticket["ticket_id"]},
    )
    assert managed.status_code == 200, managed.text
    assert managed.json()["data"]["items"] == items
    for revision, status in ((1, "in_progress"), (2, "resolved")):
        changed = await client.patch(
            f"/support-tickets/{ticket['ticket_id']}/status",
            headers=headers(manager=True),
            json={"expected_revision": revision, "status": status},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["status"] == status
        assert changed.json()["data"]["revision"] == revision + 1
    final = await client.get(f"/support-tickets/{ticket['ticket_id']}", headers=headers())
    assert final.status_code == 200 and final.json()["data"]["status"] == "resolved"


async def test_manager_can_read_current_work_queue_for_resolution(database) -> None:
    service = SupportTicketService(database)
    ticket = await service.create(
        context=owner_context(),
        command=CreateSupportTicketInput.model_validate(ARGUMENTS),
    )
    managed = await service.list_managed(context=owner_context(manager=True), status="open")
    assert managed.items == (ticket,)
    assert managed.items[0].revision == 1
    updated = await service.update_status(
        context=owner_context(manager=True),
        ticket_id=ticket.ticket_id,
        command=SupportTicketStatusUpdate(
            expected_revision=managed.items[0].revision, status="in_progress"
        ),
    )
    current = await service.list_managed(
        context=owner_context(manager=True),
        ticket_id=ticket.ticket_id,
    )
    assert current.items == (updated,)
