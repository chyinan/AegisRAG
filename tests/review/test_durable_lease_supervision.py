"""Independent cooperative cancellation probes for an observed lost durable lease."""

from __future__ import annotations

import asyncio
import sys

import pytest

from packages.agent.dto import ToolRateLimit
from packages.agent.durable.dto import DurableResumeCommand, DurableStartCommand
from packages.agent.tools.calculator import build_calculator_tool
from packages.common.config import AppSettings
from packages.common.errors import DomainError
from tests.review.durable_probe import ObservedDurableStore, await_handler_started
from tests.unit.agent.test_durable_execution import context, database, service

__all__ = ["database"]


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_observed_cancel_fence_cancels_cooperative_inflight_handler(database, backend):
    paused = await service(database, backend=backend).start(
        context=context(),
        command=DurableStartCommand(input="Calculate", approval_tools=("calculator",)),
    )
    entered, cancelled, release, lost = (asyncio.Event() for _ in range(4))
    base = build_calculator_tool(
        timeout_seconds=10, rate_limit=ToolRateLimit(max_calls=100, window_seconds=60)
    )

    async def handler(payload, ctx):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return await base.handler(payload, ctx)

    class RecordingStore(ObservedDurableStore):
        async def renew(self, *args, **kwargs):
            # This probe tests an observed cancellation after dispatch. Keep
            # startup checkpoint I/O out of its fault-injection phase; separate
            # pre-dispatch probes exercise real renewal failure before entry.
            if not entered.is_set():
                return None
            try:
                return await super().renew(*args, **kwargs)
            except DomainError as exc:
                if exc.code == "DURABLE_LEASE_LOST":
                    lost.set()
                raise

    store = RecordingStore(database)
    running = service(
        database,
        backend=backend,
        definition=base.model_copy(update={"handler": handler}),
        store=store,
    )
    # Match the minimum operator-supported lease, not an unconfigurable 100ms
    # renewal deadline that races unrelated SQLite checkpoint startup.
    running._lease_seconds = AppSettings(AGENT_DURABLE_LEASE_SECONDS=3).agent_durable_lease_seconds
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
        await await_handler_started(
            task, entered, timeout=5, lease_seconds=running._lease_seconds, store=store
        )
        current = await service(database).get(context=context(), run_id=paused.run_id)
        receipt = await service(database).cancel(
            context=context(), run_id=paused.run_id, expected_revision=current.revision
        )
        assert receipt.status == "cancelled"
        await asyncio.wait_for(lost.wait(), 5)
        # Cancellation is cooperative, but a known lost lease must not leave an
        # interruptible handler running until its unrelated ten-second timeout.
        await asyncio.wait_for(cancelled.wait(), 0.5)
        terminal = await service(database).get(context=context(), run_id=paused.run_id)
        assert terminal.status == "cancelled" and terminal.final_answer is None
    finally:
        active_failure = sys.exception()
        release.set()
        if not task.done():
            task.cancel()
        (result,) = await asyncio.gather(task, return_exceptions=True)
        if (
            active_failure is None
            and isinstance(result, BaseException)
            and not isinstance(result, (DomainError, asyncio.CancelledError))
        ):
            raise AssertionError("resume failed with an unexpected exception type") from None
