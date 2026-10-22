"""Independent adversarial checks for keyed-start recovery boundaries."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from apps.api.error_handlers import register_error_handlers
from apps.api.middleware import RequestLoggingMiddleware
from packages.agent.durable.dto import (
    DurableResumeCommand,
    DurableStartCommand,
    keyed_run_id,
    start_command_fingerprint,
)
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import DurableAgentRunModel, DurableGraphCheckpointModel
from tests.unit.agent.test_durable_execution import Clock, Stepper, context, service
from tests.unit.agent.test_durable_execution import database as database


def test_start_protocol_v1_matches_frozen_compatibility_vector() -> None:
    key = UUID("587629a6-65a2-4db2-8e18-c17d8190c0ac")
    command = DurableStartCommand(
        input=" Calculate 2 + 2 ",
        idempotency_key=key,
        max_steps=4,
        max_tool_calls=0,
        timeout_seconds=120,
        approval_tools=("calculator",),
        metadata={"tag": "fixture", "password": "removed"},
    )
    # These values are protocol fixtures, not expressions derived from the
    # production namespace/canonicalizer. Altering them requires a version and
    # compatibility strategy; deployment defaults cannot change an old key.
    assert keyed_run_id(tenant_id="tenant-a", user_id="user-a", key=key) == (
        "d846a435-3bf1-5567-9ef3-e44b430865f6"
    )
    assert start_command_fingerprint(command) == (
        "d91911b8cc5b3c223ba2cc25ecaf3dbf32227c0912fb4555d6226a3197b2fb82"
    )
    assert start_command_fingerprint(command.model_copy(update={"idempotency_key": uuid4()})) == (
        start_command_fingerprint(command)
    )


@pytest.mark.parametrize("path", ["/agent/durable", "/v1/agent/durable/recover"])
async def test_unexpected_durable_failure_envelope_is_also_noncacheable(path: str) -> None:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    register_error_handlers(app)

    @app.post(path)
    async def broken() -> None:
        raise RuntimeError("private failure detail must not escape")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://review",
    ) as client:
        response = await client.post(path)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "private failure detail" not in response.text
    assert response.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_duplicate_and_recover_leave_live_lease_and_private_state_unchanged(
    database, backend: str
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class WaitingStepper(Stepper):
        async def next_step(self, state):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            return await super().next_step(state)

    svc = service(database, backend=backend, stepper=WaitingStepper())
    command = DurableStartCommand(input="PRIVATE KEYED QUESTION", idempotency_key=uuid4())
    task = asyncio.create_task(svc.start(context=context(), command=command))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        async with database() as session:
            before = await session.scalar(select(DurableAgentRunModel))
            assert before is not None and before.lease_token is not None
            initial = (before.lease_token, before.lease_until, before.snapshot)
        fresh = service(database, backend=backend)
        fresh._defaults = (99, 99, 999)
        fresh._backend = "native" if backend == "langgraph" else "langgraph"
        first = await fresh.start(context=context(), command=command)
        second = await fresh.recover(context=context(), idempotency_key=command.idempotency_key)
        assert first == second and first.status == "running"
        async with database() as session:
            after = await session.scalar(select(DurableAgentRunModel))
            assert after is not None
            assert (after.lease_token, after.lease_until, after.snapshot) == initial
            graphs = (await session.scalars(select(DurableGraphCheckpointModel))).all()
        assert calls == 1
        assert bool(graphs) == (backend == "langgraph")
        for graph in graphs:
            serialized = json.dumps([graph.checkpoint, graph.metadata_, graph.writes])
            assert str(command.idempotency_key) not in serialized
            assert "command_fingerprint" not in serialized
            assert initial[2]["startup"]["command_fingerprint"] not in serialized
            assert "PRIVATE KEYED QUESTION" not in serialized
    finally:
        release.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_late_creation_cleanup_cannot_cancel_new_explicit_resume_owner(
    database, backend: str
) -> None:
    clock = Clock()
    inserted = asyncio.Event()
    release_ack = asyncio.Event()
    late_finished = asyncio.Event()
    late_cleanup_checked = asyncio.Event()
    resumed_entered = asyncio.Event()
    release_resume = asyncio.Event()
    original_token = None

    class SlowAckStore(SqlAlchemyDurableStore):
        async def create_keyed(self, snapshot, **kwargs):
            nonlocal original_token
            original_token = kwargs["token"]
            result = await super().create_keyed(snapshot, **kwargs)
            inserted.set()
            try:
                await release_ack.wait()
            except asyncio.CancelledError:
                await release_ack.wait()
            late_finished.set()
            return result

        async def owns(self, snapshot, **kwargs):
            # Simulate unavailable best-effort immediate cleanup. It must not
            # broaden ownership when the late acknowledgment eventually arrives.
            if not release_ack.is_set():
                return False
            result = await super().owns(snapshot, **kwargs)
            late_cleanup_checked.set()
            return result

    class WaitingStepper(Stepper):
        async def next_step(self, state):
            if not state.observations:
                resumed_entered.set()
                await release_resume.wait()
            return await super().next_step(state)

    original = service(database, backend=backend, store=SlowAckStore(database), clock=clock)
    command = DurableStartCommand(input="Calculate", idempotency_key=uuid4())
    pending = asyncio.create_task(original.start(context=context(), command=command))
    await asyncio.wait_for(inserted.wait(), 3)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    clock.value += 31
    fresh = service(database, backend=backend, clock=clock, stepper=WaitingStepper())
    recovered = await fresh.recover(context=context(), idempotency_key=command.idempotency_key)
    resumed = asyncio.create_task(
        fresh.resume(
            context=context(),
            run_id=recovered.run_id,
            command=DurableResumeCommand(expected_revision=recovered.revision),
        )
    )
    try:
        await asyncio.wait_for(resumed_entered.wait(), 3)
        release_ack.set()
        await asyncio.wait_for(late_finished.wait(), 3)
        await asyncio.wait_for(late_cleanup_checked.wait(), 3)
        while original._creation_cleanup_tasks:
            await asyncio.gather(*original._creation_cleanup_tasks)
        async with database() as session:
            row = await session.get(DurableAgentRunModel, recovered.run_id)
            assert row is not None and row.status == "running"
            assert row.lease_token is not None and row.lease_token != original_token
        latest = await fresh.recover(context=context(), idempotency_key=command.idempotency_key)
        assert latest.status == "running"
    finally:
        release_ack.set()
        release_resume.set()
        result = await asyncio.wait_for(resumed, 5)
    assert result.status == "completed" and result.tool_calls_used == 1
