"""Separate pre-dispatch lease loss from the post-dispatch cancel contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from packages.agent.dto import ToolRateLimit
from packages.agent.durable.dto import DurableResumeCommand, DurableStartCommand
from packages.agent.tools.calculator import build_calculator_tool
from packages.common.errors import DomainError
from tests.review.durable_probe import ObservedDurableStore, await_handler_started
from tests.unit.agent.test_durable_execution import context, database, service

__all__ = ["database"]


@pytest.mark.parametrize("state", ["domain_error", "unexpected_error", "returned"])
async def test_entry_probe_exposes_early_resume_without_private_content(state):
    secret = "private prompt and SQL parameter must never be rendered"

    async def resume():
        if state == "domain_error":
            raise DomainError(code="DURABLE_STORAGE_TIMEOUT", message=secret, status_code=503)
        if state == "unexpected_error":
            raise RuntimeError(secret)
        return SimpleNamespace(status="failed", error_code="DURABLE_STORAGE_TIMEOUT", secret=secret)

    task = asyncio.create_task(resume())
    with pytest.raises(AssertionError, match="handler_not_started") as failed:
        await await_handler_started(task, asyncio.Event(), timeout=5, lease_seconds=3)
    assert task.done()
    assert secret not in str(failed.value)
    assert "pending" not in str(failed.value)
    if state != "unexpected_error":
        assert "DURABLE_STORAGE_TIMEOUT" in str(failed.value)


async def test_entry_probe_reports_pending_and_cancellation_keeps_resume_ownership():
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    try:
        with pytest.raises(AssertionError, match="'task': 'pending'"):
            await await_handler_started(task, asyncio.Event(), timeout=0.01, lease_seconds=3)
        assert not task.done()
        waiting = asyncio.create_task(
            await_handler_started(task, asyncio.Event(), timeout=5, lease_seconds=3)
        )
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not task.done()
    finally:
        release.set()
        await task


async def test_entry_probe_drops_unrecognized_error_codes():
    secret_code = "UNRECOGNIZED_PRIVATE_VALUE"

    async def resume():
        raise DomainError(code=secret_code, message="private details", status_code=503)

    task = asyncio.create_task(resume())
    with pytest.raises(AssertionError, match="UNCLASSIFIED") as failed:
        await await_handler_started(task, asyncio.Event(), timeout=5, lease_seconds=3)
    assert secret_code not in str(failed.value)
    assert "private details" not in str(failed.value)


@pytest.mark.parametrize("release_checkpoint", [False, True])
async def test_pre_dispatch_renewal_stall_is_classified_before_handler_entry(
    database, release_checkpoint
):
    """Gate graph persistence before SQL I/O to isolate renewal from driver teardown."""
    pytest.importorskip("langgraph.graph")
    paused = await service(database, backend="langgraph").start(
        context=context(),
        command=DurableStartCommand(input="Calculate", approval_tools=("calculator",)),
    )
    checkpoint_gated, unlock, renewal_entered, handler_entered = (
        asyncio.Event() for _ in range(4)
    )
    base = build_calculator_tool(
        timeout_seconds=10, rate_limit=ToolRateLimit(max_calls=100, window_seconds=60)
    )

    async def handler(payload, ctx):
        handler_entered.set()
        return await base.handler(payload, ctx)

    class GatedStore(ObservedDurableStore):
        held_once = False

        async def graph_put(self, snapshot, **kwargs):
            if not self.held_once:
                self.held_once = True
                checkpoint_gated.set()
                await unlock.wait()
            return await super().graph_put(snapshot, **kwargs)

        async def renew(self, snapshot, **kwargs):
            if not checkpoint_gated.is_set():
                # Arm only at the selected pre-dispatch checkpoint boundary,
                # independently of graph import/compile/scheduler startup cost.
                return None
            renewal_entered.set()
            if not release_checkpoint:
                # Isolate the heartbeat timeout, without holding an artificial
                # SQLite write transaction through driver force-close teardown.
                # Real graph persistence runs after the healthy gate releases.
                return await self._observed("renew", unlock.wait(), snapshot)
            return await super().renew(snapshot, **kwargs)

    store = GatedStore(database)
    running = service(
        database,
        backend="langgraph",
        definition=base.model_copy(update={"handler": handler}),
        store=store,
    )
    # The short lease is deliberate fault injection, not a deployment setting.
    # The healthy path uses the supported three-second operator minimum.
    running._lease_seconds = 3.0 if release_checkpoint else 0.3
    task = asyncio.create_task(
        running.resume(
            context=context(),
            run_id=paused.run_id,
            command=DurableResumeCommand(
                expected_revision=paused.revision,
                approval_id=paused.approval.approval_id,
                approve=True,
            ),
        )
    )
    try:
        await asyncio.wait_for(checkpoint_gated.wait(), 5)
        await asyncio.wait_for(renewal_entered.wait(), 5)
        if release_checkpoint:
            unlock.set()
            await await_handler_started(
                task,
                handler_entered,
                timeout=5,
                lease_seconds=running._lease_seconds,
                store=store,
            )
            result = await asyncio.wait_for(asyncio.shield(task), 5)
            assert result.status == "completed"
        else:
            with pytest.raises(DomainError, match="DURABLE_STORAGE_TIMEOUT"):
                await asyncio.wait_for(asyncio.shield(task), 5)
            assert not handler_entered.is_set()
            assert any(
                event["operation"] == "renew" and event["outcome"] == "cancelled"
                for event in store.observations
            )
            with pytest.raises(AssertionError, match="DURABLE_STORAGE_TIMEOUT"):
                await await_handler_started(
                    task,
                    handler_entered,
                    timeout=5,
                    lease_seconds=running._lease_seconds,
                    store=store,
                )
    finally:
        unlock.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
