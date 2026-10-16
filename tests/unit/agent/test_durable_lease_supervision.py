"""Fail-closed supervision of the one durable business execution and its lease."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest
from sqlalchemy import select, update

from packages.agent.dto import ToolRateLimit
from packages.agent.durable.dto import DurableResumeCommand, DurableStartCommand, durable_error
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import DurableAgentRunModel, DurableToolLedgerModel
from packages.agent.tools.calculator import build_calculator_tool
from packages.common.errors import DomainError
from tests.unit.agent.test_durable_execution import context, database, service

__all__ = ["database"]


async def pause(database, backend):
    return await service(database, backend=backend).start(
        context=context(),
        command=DurableStartCommand(input="Calculate", approval_tools=("calculator",)),
    )


def resume(svc, paused):
    # Use the minimum supported deployment lease; sub-second SQLite write
    # deadlines can fail before fault injection under ordinary CI contention.
    svc._lease_seconds = 3.0
    return asyncio.create_task(
        svc.resume(
            context=context(),
            run_id=paused.run_id,
            command=DurableResumeCommand(
                expected_revision=paused.revision,
                approval_id=paused.approval.approval_id,
                approve=True,
            ),
        )
    )


def calculator():
    return build_calculator_tool(
        timeout_seconds=30, rate_limit=ToolRateLimit(max_calls=100, window_seconds=60)
    )


def tool(handler):
    return calculator().model_copy(update={"handler": handler})


async def wait_resisting_cancellation(release, cancelled):
    while not release.is_set():
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()


@pytest.mark.parametrize("backend", ["native", "langgraph"])
@pytest.mark.parametrize("failure", ["error", "raw_error", "hang", "resistant_hang"])
async def test_renewal_failure_stops_work_without_inventing_terminal_state(
    database,
    backend,
    failure,
):
    paused = await pause(database, backend)
    entered, cancelled, renewal_cancelled, release = (asyncio.Event() for _ in range(4))

    async def handler(payload, ctx):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return await calculator().handler(payload, ctx)

    class BrokenRenewal(SqlAlchemyDurableStore):
        async def renew(self, *args, **kwargs):
            if not entered.is_set():
                return None
            if failure == "error":
                raise durable_error("DURABLE_STORAGE_UNAVAILABLE", status_code=503)
            if failure == "raw_error":
                raise RuntimeError("private storage diagnostic")
            if failure == "resistant_hang":
                await wait_resisting_cancellation(release, renewal_cancelled)
                return await super().renew(*args, **kwargs)
            try:
                await release.wait()
            except asyncio.CancelledError:
                renewal_cancelled.set()
                raise

    svc = service(
        database, backend=backend, definition=tool(handler), store=BrokenRenewal(database)
    )
    task = resume(svc, paused)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        expected = (
            "DURABLE_STORAGE_UNAVAILABLE"
            if failure in ("error", "raw_error")
            else "DURABLE_STORAGE_TIMEOUT"
        )
        with pytest.raises(DomainError, match=expected):
            await asyncio.wait_for(asyncio.shield(task), 10)
        assert cancelled.is_set()
        if failure not in ("error", "raw_error"):
            assert renewal_cancelled.is_set()
        persisted = await service(database).get(context=context(), run_id=paused.run_id)
        assert persisted.status == "running" and persisted.final_answer is None
        assert persisted.deadline_at == paused.deadline_at
        assert persisted.steps_used == 1 and persisted.tool_calls_used == 1
        async with database() as session:
            ledger = await session.scalar(select(DurableToolLedgerModel))
            assert ledger.status == "started"
    finally:
        release.set()
        with suppress(DomainError, asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_resistant_handler_is_detached_without_committing_late_result(database, backend):
    paused = await pause(database, backend)
    entered, cancelled, release, exited = (asyncio.Event() for _ in range(4))

    async def handler(payload, ctx):
        entered.set()
        await wait_resisting_cancellation(release, cancelled)
        exited.set()
        return await calculator().handler(payload, ctx)

    class BrokenRenewal(SqlAlchemyDurableStore):
        async def renew(self, *args, **kwargs):
            if not entered.is_set():
                return None
            raise durable_error("DURABLE_LEASE_LOST")

    svc = service(
        database, backend=backend, definition=tool(handler), store=BrokenRenewal(database)
    )
    task = resume(svc, paused)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(DomainError, match="DURABLE_LEASE_LOST"):
            await asyncio.wait_for(asyncio.shield(task), 10)
        assert cancelled.is_set() and not exited.is_set()
        before = await service(database).get(context=context(), run_id=paused.run_id)
        release.set()
        await asyncio.wait_for(exited.wait(), 1)
        await asyncio.sleep(0.1)
        after = await service(database).get(context=context(), run_id=paused.run_id)
        assert after == before
        assert after.status == "running" and after.final_answer is None
        async with database() as session:
            ledger = await session.scalar(select(DurableToolLedgerModel))
            assert ledger.status == "started"
    finally:
        release.set()
        with suppress(DomainError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_lease_loss_during_resistant_predispatch_save_never_enters_handler(database, backend):
    paused = await pause(database, backend)
    entered, cancelled, release, exited = (asyncio.Event() for _ in range(4))
    calls = 0

    async def handler(payload, ctx):
        nonlocal calls
        calls += 1
        return await calculator().handler(payload, ctx)

    class BlockDispatch(SqlAlchemyDurableStore):
        async def save(self, *args, **kwargs):
            saved = await super().save(*args, **kwargs)
            ledger = kwargs.get("ledger")
            if ledger is not None and ledger.status == "started":
                entered.set()
                await wait_resisting_cancellation(release, cancelled)
                exited.set()
            return saved

        async def renew(self, *args, **kwargs):
            if not entered.is_set():
                return None
            raise durable_error("DURABLE_LEASE_LOST")

    svc = service(
        database, backend=backend, definition=tool(handler), store=BlockDispatch(database)
    )
    task = resume(svc, paused)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(DomainError, match="DURABLE_LEASE_LOST"):
            await asyncio.wait_for(asyncio.shield(task), 10)
        assert cancelled.is_set() and calls == 0
        release.set()
        await asyncio.wait_for(exited.wait(), 1)
        await asyncio.sleep(0.1)
        assert calls == 0
        persisted = await service(database).get(context=context(), run_id=paused.run_id)
        assert persisted.status == "running" and persisted.final_answer is None
    finally:
        release.set()
        with suppress(DomainError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("backend", ["native", "langgraph"])
@pytest.mark.parametrize("outcome", ["uncommitted", "committed", "denied"])
async def test_terminal_race_returns_only_a_committed_revalidated_answer(
    database, backend, outcome
):
    committed = outcome != "uncommitted"
    paused = await pause(database, backend)
    terminal, release = asyncio.Event(), asyncio.Event()

    class TerminalRace(SqlAlchemyDurableStore):
        async def save(self, previous, snapshot, **kwargs):
            if snapshot.status != "completed":
                return await super().save(previous, snapshot, **kwargs)
            if committed:
                await super().save(previous, snapshot, **kwargs)
            terminal.set()
            await release.wait()
            raise AssertionError("A blocked save acknowledgement must be cancelled")

        async def renew(self, *args, **kwargs):
            if not terminal.is_set():
                return None
            raise durable_error("DURABLE_STORAGE_UNAVAILABLE", status_code=503)

    svc = service(database, backend=backend, store=TerminalRace(database))

    async def current_context(ctx):
        return context(allow=False) if outcome == "denied" and terminal.is_set() else ctx

    svc._context_revalidator = current_context
    validations = 0
    original = svc._revalidate

    async def revalidate(*args):
        nonlocal validations
        if args[1].status == "completed":
            validations += 1
        return await original(*args)

    svc._revalidate = revalidate
    task = resume(svc, paused)
    try:
        await asyncio.wait_for(terminal.wait(), 5)
        if outcome == "denied":
            with pytest.raises(DomainError, match="TOOL_PERMISSION_DENIED") as denied:
                await asyncio.wait_for(asyncio.shield(task), 10)
            assert denied.value.status_code == 403 and validations == 1
            persisted = await SqlAlchemyDurableStore(database).load(
                tenant_id=context().auth.tenant_id,
                user_id=context().auth.user_id,
                run_id=paused.run_id,
            )
            assert persisted.status == "completed" and persisted.result is not None
        elif committed:
            result = await asyncio.wait_for(asyncio.shield(task), 10)
            assert result.status == "completed" and result.final_answer == "The result is 42"
            assert validations == 1
        else:
            with pytest.raises(DomainError, match="DURABLE_STORAGE_UNAVAILABLE"):
                await asyncio.wait_for(asyncio.shield(task), 10)
            persisted = await service(database).get(context=context(), run_id=paused.run_id)
            assert persisted.status == "running" and persisted.final_answer is None
            assert validations == 0
    finally:
        release.set()
        with suppress(DomainError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_external_cancel_during_cleanup_cannot_cancel_new_owner(database, backend):
    paused = await pause(database, backend)
    entered, handler_release, releasing, cleanup_release = (asyncio.Event() for _ in range(4))

    async def handler(payload, ctx):
        entered.set()
        await handler_release.wait()
        return await calculator().handler(payload, ctx)

    class NewOwner(SqlAlchemyDurableStore):
        async def renew(self, snapshot, **kwargs):
            if not entered.is_set():
                return None
            async with database.begin() as session:
                await session.execute(
                    update(DurableAgentRunModel)
                    .where(DurableAgentRunModel.id == snapshot.run_id)
                    .values(lease_token="new-owner", lease_until=svc._clock() + 30)
                )
            raise durable_error("DURABLE_LEASE_LOST")

        async def release(self, *args, **kwargs):
            releasing.set()
            await cleanup_release.wait()
            return await super().release(*args, **kwargs)

    svc = service(database, backend=backend, definition=tool(handler), store=NewOwner(database))
    task = resume(svc, paused)
    try:
        await asyncio.wait_for(releasing.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), 10)
        async with database() as session:
            run = await session.scalar(select(DurableAgentRunModel))
            assert run.lease_token == "new-owner" and run.status == "running"
            assert run.snapshot["result"] is None
    finally:
        handler_release.set()
        cleanup_release.set()
        with suppress(DomainError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_disconnect_stays_cancelled_when_its_terminal_write_fails(database, backend):
    paused = await pause(database, backend)
    entered, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def handler(payload, ctx):
        entered.set()
        await release.wait()
        return await calculator().handler(payload, ctx)

    class FailedCancellation(SqlAlchemyDurableStore):
        async def cancel(self, *args, **kwargs):
            attempted.set()
            raise durable_error("DURABLE_STORAGE_UNAVAILABLE", status_code=503)

    svc = service(
        database, backend=backend, definition=tool(handler), store=FailedCancellation(database)
    )
    task = resume(svc, paused)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), 10)
        assert attempted.is_set()
        persisted = await service(database).get(context=context(), run_id=paused.run_id)
        assert persisted.status == "running" and persisted.final_answer is None
    finally:
        release.set()
        with suppress(DomainError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_lease_failure_cleanup_does_not_wait_forever_for_release(database, backend):
    paused = await pause(database, backend)
    entered, release, release_cancelled = (asyncio.Event() for _ in range(3))

    async def handler(payload, ctx):
        entered.set()
        await release.wait()
        return await calculator().handler(payload, ctx)

    class HangingRelease(SqlAlchemyDurableStore):
        async def renew(self, *args, **kwargs):
            if not entered.is_set():
                return None
            raise durable_error("DURABLE_LEASE_LOST")

        async def release(self, *args, **kwargs):
            try:
                await release.wait()
            except asyncio.CancelledError:
                release_cancelled.set()
                raise

    svc = service(
        database, backend=backend, definition=tool(handler), store=HangingRelease(database)
    )
    task = resume(svc, paused)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(DomainError, match="DURABLE_LEASE_LOST"):
            await asyncio.wait_for(asyncio.shield(task), 10)
        assert release_cancelled.is_set()
        async with database() as session:
            run = await session.scalar(select(DurableAgentRunModel))
            assert run.status == "running" and run.lease_token is not None
    finally:
        release.set()
        with suppress(DomainError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_committed_release_ends_creator_authority_even_at_revision_zero(database, backend):
    released, release = asyncio.Event(), asyncio.Event()
    cancellation_writes = 0

    class CommittedRelease(SqlAlchemyDurableStore):
        async def release(self, *args, **kwargs):
            await super().release(*args, **kwargs)
            released.set()
            await release.wait()

        async def cancel(self, *args, **kwargs):
            nonlocal cancellation_writes
            cancellation_writes += 1
            return await super().cancel(*args, **kwargs)

    async def unavailable_context(ctx):
        # Fail before the first budget reservation, leaving revision zero. The
        # creation fallback must not authorize a write after a confirmed lease
        # acquisition followed by a committed (but unacknowledged) release.
        raise RuntimeError("Test-only context dependency failure")

    svc = service(database, backend=backend, store=CommittedRelease(database))
    svc._context_revalidator = unavailable_context
    task = asyncio.create_task(
        svc.start(context=context(), command=DurableStartCommand(input="Calculate"))
    )
    try:
        await asyncio.wait_for(released.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), 10)
        assert cancellation_writes == 0
        async with database() as session:
            run = await session.scalar(select(DurableAgentRunModel))
            assert run.status == "running" and run.revision == 0 and run.lease_token is None
    finally:
        release.set()
        with suppress(DomainError, RuntimeError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_ordinary_completion_revalidates_after_commit_without_heartbeat_failure(
    database, backend
):
    completed = asyncio.Event()

    class CommittedResult(SqlAlchemyDurableStore):
        async def save(self, previous, snapshot, **kwargs):
            saved = await super().save(previous, snapshot, **kwargs)
            if saved.status == "completed":
                completed.set()
            return saved

    async def current_context(ctx):
        return context(allow=False) if completed.is_set() else ctx

    svc = service(database, backend=backend, store=CommittedResult(database))
    svc._context_revalidator = current_context
    with pytest.raises(DomainError, match="TOOL_PERMISSION_DENIED") as denied:
        await svc.start(context=context(), command=DurableStartCommand(input="Calculate"))
    assert denied.value.status_code == 403 and completed.is_set()
    async with database() as session:
        run = await session.scalar(select(DurableAgentRunModel))
        assert run.status == "completed" and run.snapshot["result"] is not None
