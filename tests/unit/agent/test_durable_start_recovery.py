"""Owner-bound keyed starts: SQLite concurrency and cancellation fault injection."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from packages.agent.durable.dto import (
    DurableResumeCommand,
    DurableSnapshot,
    DurableStartCommand,
    DurableStartup,
    keyed_run_id,
    start_command_fingerprint,
)
from packages.agent.durable.service import DurableAgentService
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import (
    AgentRunModel,
    DurableAgentRunModel,
    DurableToolLedgerModel,
)
from packages.common.errors import DomainError
from tests.unit.agent.test_durable_execution import Clock, Stepper, context, service
from tests.unit.agent.test_durable_execution import database as database


@pytest.fixture(params=("native", "langgraph"))
def backend(request):
    if request.param == "langgraph":
        pytest.importorskip("langgraph")
    return request.param


class CountStepper(Stepper):
    calls = 0

    async def next_step(self, state):
        self.calls += 1
        return await super().next_step(state)


def command(**kwargs: Any) -> DurableStartCommand:
    return DurableStartCommand(input="Calculate", idempotency_key=uuid4(), **kwargs)


async def drain_cleanup(svc: DurableAgentService) -> None:
    # A completed storage task queues its done callback on the next event-loop turn.
    await asyncio.sleep(0)
    while svc._creation_cleanup_tasks:
        await asyncio.gather(*svc._creation_cleanup_tasks)


def test_key_validation_canonical_request_and_owner_namespace():
    key = uuid4()
    first = DurableStartCommand(
        input=" Calculate ",
        idempotency_key=str(key).upper(),
        metadata={"tag": "same", "password": "DROP"},
    )
    second = DurableStartCommand(input="Calculate", idempotency_key=key, metadata={"tag": "same"})
    assert first.idempotency_key == key
    assert start_command_fingerprint(first) == start_command_fingerprint(second)
    assert start_command_fingerprint(first) != start_command_fingerprint(
        second.model_copy(update={"max_steps": 8})
    )
    assert keyed_run_id(tenant_id="a:b", user_id="c", key=key) != keyed_run_id(
        tenant_id="a", user_id="b:c", key=key
    )
    assert keyed_run_id(tenant_id="t", user_id="u", key=key) != keyed_run_id(
        tenant_id="t", user_id="v", key=key
    )
    for value in ("not-a-key", "00000000-0000-0000-0000-000000000000", uuid4().hex[:16]):
        with pytest.raises(ValidationError):
            DurableStartCommand(input="x", idempotency_key=value)


async def test_sequential_duplicate_recovery_and_private_fingerprint(database, backend):
    stepper = CountStepper()
    svc = service(database, backend=backend, stepper=stepper)
    request = command(metadata={"tag": "private-category"})
    original = await svc.start(context=context(), command=request)
    duplicate = await svc.start(context=context(), command=request)
    recovered = await service(database, backend=backend).recover(
        context=context(), idempotency_key=request.idempotency_key
    )
    assert duplicate == recovered == original
    assert stepper.calls == 2
    async with database() as session:
        public = (await session.scalars(select(AgentRunModel))).all()
        private = (await session.scalars(select(DurableAgentRunModel))).all()
        ledger = (await session.scalars(select(DurableToolLedgerModel))).all()
    assert len(public) == len(private) == len(ledger) == 1
    assert public[0].metadata_["keyed_start"] is True
    fingerprint = start_command_fingerprint(request)
    assert private[0].snapshot["startup"]["command_fingerprint"] == fingerprint
    assert str(request.idempotency_key) not in json.dumps(private[0].snapshot)
    public_text = json.dumps(
        [public[0].metadata_, public[0].input_summary, original.model_dump(mode="json")]
    )
    for forbidden in (str(request.idempotency_key), fingerprint, "private-category"):
        assert forbidden not in public_text


async def test_duplicate_approval_is_read_only_before_defaults_or_runtime(database, backend):
    clock = Clock()
    svc = service(database, backend=backend, clock=clock)
    request = command(approval_tools=("calculator",))
    paused = await svc.start(context=context(), command=request)
    clock.value += 3
    fresh = service(database, backend=backend, clock=clock)
    fresh._defaults = (99, 99, 999)
    fresh._backend = "native" if backend == "langgraph" else "langgraph"

    def unavailable_runtime(*args):
        raise AssertionError("a duplicate approval read must not construct runtime")

    fresh._runtime_factory = unavailable_runtime
    assert await fresh.start(context=context(), command=request) == paused
    assert await fresh.recover(context=context(), idempotency_key=request.idempotency_key) == paused
    assert paused.approval is not None
    assert paused.tool_calls_used == 0
    for changes in (
        {"input": "changed"},
        {"max_steps": 8},
        {"metadata": {"tag": "other"}},
        {"approval_tools": ()},
        {"timeout_seconds": 120.0},
    ):
        with pytest.raises(DomainError, match="DURABLE_START_KEY_CONFLICT") as error:
            await fresh.start(context=context(), command=request.model_copy(update=changes))
        assert error.value.status_code == 409


async def test_duplicate_completed_uses_current_auth_and_evidence(database, backend):
    svc = service(database, backend=backend)
    request = command()
    await svc.start(context=context(), command=request)
    with pytest.raises(DomainError, match="TOOL_PERMISSION_DENIED"):
        await svc.start(context=context(allow=False), command=request)
    with pytest.raises(DomainError, match="TOOL_PERMISSION_DENIED"):
        await svc.recover(context=context(allow=False), idempotency_key=request.idempotency_key)

    async def revoked(context):
        raise DomainError(code="AUTH_REVOKED", message="revoked", status_code=403)

    svc._context_revalidator = revoked
    with pytest.raises(DomainError, match="AUTH_REVOKED"):
        await svc.start(context=context(), command=request)
    svc._context_revalidator = None

    class ChangedEvidence:
        async def rehydrate(self, **kwargs):
            raise DomainError(code="DURABLE_EVIDENCE_CHANGED", message="changed", status_code=403)

    svc._evidence = ChangedEvidence()
    with pytest.raises(DomainError, match="DURABLE_EVIDENCE_CHANGED"):
        await svc.recover(context=context(), idempotency_key=request.idempotency_key)


async def test_unknown_owner_isolation_and_retained_tombstone(database, backend):
    clock = Clock()
    svc = service(database, backend=backend, clock=clock)
    request = command(approval_tools=("calculator",))
    for actor in (context(), context(user="other"), context(tenant="other")):
        with pytest.raises(DomainError) as error:
            await svc.recover(context=actor, idempotency_key=request.idempotency_key)
        assert error.value.status_code == 404
    first = await svc.start(context=context(), command=request)
    for actor in (context(user="other"), context(tenant="other")):
        with pytest.raises(DomainError) as error:
            await svc.recover(context=actor, idempotency_key=request.idempotency_key)
        assert error.value.status_code == 404
        other = await svc.start(context=actor, command=request)
        assert other.run_id != first.run_id
    clock.value += 86401
    for operation in (
        lambda: svc.recover(context=context(), idempotency_key=request.idempotency_key),
        lambda: svc.start(context=context(), command=request),
        lambda: svc.start(context=context(), command=request.model_copy(update={"input": "other"})),
        lambda: svc.get(context=context(), run_id=first.run_id),
    ):
        with pytest.raises(DomainError) as error:
            await operation()
        assert error.value.status_code == 410
    async with database() as session:
        assert await session.get(DurableAgentRunModel, first.run_id) is None
        tombstone = await session.get(AgentRunModel, first.run_id)
        assert tombstone.metadata_["keyed_start"] is True


class RacingStore(SqlAlchemyDurableStore):
    def __init__(self, sessions):
        super().__init__(sessions)
        self.arrived = 0
        self.both = asyncio.Event()

    async def lookup_keyed(self, **kwargs):
        result = await super().lookup_keyed(**kwargs)
        self.arrived += 1
        if self.arrived <= 2:
            if self.arrived == 2:
                self.both.set()
            await self.both.wait()
        return result


async def test_concurrent_duplicate_creates_one_run_and_one_dispatch(database, backend):
    store = RacingStore(database)
    stepper = CountStepper()
    svc = service(database, backend=backend, store=store, stepper=stepper)
    request = command()
    first, second = await asyncio.gather(
        svc.start(context=context(), command=request),
        svc.start(context=context(), command=request),
    )
    assert first.run_id == second.run_id
    recovered = await svc.recover(context=context(), idempotency_key=request.idempotency_key)
    assert recovered.status == "completed"
    assert recovered.steps_used == 2 and recovered.tool_calls_used == 1
    assert stepper.calls == 2
    async with database() as session:
        assert len((await session.scalars(select(AgentRunModel))).all()) == 1
        assert len((await session.scalars(select(DurableToolLedgerModel))).all()) == 1


@pytest.mark.parametrize(
    "stage", ["before_insert", "conflict_read", "after_duplicate", "late_commit"]
)
async def test_cancelled_loser_cannot_cancel_winner(database, backend, stage):
    winner_inserted = asyncio.Event()
    release_winner = asyncio.Event()
    loser_entered = asyncio.Event()
    release_loser = asyncio.Event()
    loser_finished = asyncio.Event()

    class WinnerStore(SqlAlchemyDurableStore):
        async def create_keyed(self, snapshot, **kwargs):
            result = await super().create_keyed(snapshot, **kwargs)
            assert result.inserted
            winner_inserted.set()
            await release_winner.wait()
            return result

    class LoserStore(SqlAlchemyDurableStore):
        initial_lookup = True

        async def lookup_keyed(self, **kwargs):
            if self.initial_lookup:
                # Reproduce a lookup begun before the other insert became visible.
                self.initial_lookup = False
                return None
            if stage == "conflict_read":
                loser_entered.set()
                await release_loser.wait()
            return await super().lookup_keyed(**kwargs)

        async def create_keyed(self, snapshot, **kwargs):
            try:
                if stage in ("before_insert", "late_commit"):
                    loser_entered.set()
                    try:
                        await release_loser.wait()
                    except asyncio.CancelledError:
                        if stage != "late_commit":
                            raise
                        await release_loser.wait()
                result = await super().create_keyed(snapshot, **kwargs)
                assert not result.inserted
                if stage == "after_duplicate":
                    loser_entered.set()
                    await release_loser.wait()
                return result
            finally:
                loser_finished.set()

        async def cancel(self, previous, **kwargs):
            raise AssertionError("a losing token must never reach cancellation of the winner")

    winner_service = service(database, backend=backend, store=WinnerStore(database))
    loser_service = service(database, backend=backend, store=LoserStore(database))
    request = command(approval_tools=("calculator",))
    winner = asyncio.create_task(winner_service.start(context=context(), command=request))
    await asyncio.wait_for(winner_inserted.wait(), 3)
    loser = asyncio.create_task(loser_service.start(context=context(), command=request))
    await asyncio.wait_for(loser_entered.wait(), 3)
    loser.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loser
    initial = await winner_service.recover(
        context=context(), idempotency_key=request.idempotency_key
    )
    assert initial.status == "running" and initial.revision == 0
    release_winner.set()
    completed_winner = await winner
    assert completed_winner.status == "awaiting_approval"
    release_loser.set()
    await asyncio.wait_for(loser_finished.wait(), 3)
    await drain_cleanup(loser_service)
    latest = await winner_service.recover(
        context=context(), idempotency_key=request.idempotency_key
    )
    assert latest == completed_winner


@pytest.mark.parametrize("failure", ["cancel", "timeout", "lost_ack"])
@pytest.mark.parametrize("commit_stage", ["before", "after"])
async def test_ambiguous_commit_cleanup_cannot_launch_tools(
    database, backend, failure, commit_stage
):
    entered = asyncio.Event()
    release = asyncio.Event()
    storage_finished = asyncio.Event()
    cancelled_fence = asyncio.Event()

    class Store(SqlAlchemyDurableStore):
        async def create_keyed(self, snapshot, **kwargs):
            result = None
            if commit_stage == "after":
                result = await super().create_keyed(snapshot, **kwargs)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Simulate a storage client that does not abort COMMIT promptly.
                await release.wait()
            if result is None:
                result = await super().create_keyed(snapshot, **kwargs)
            storage_finished.set()
            if failure == "lost_ack":
                raise DomainError(
                    code="DURABLE_STORAGE_UNAVAILABLE", message="uncertain", status_code=503
                )
            return result

        async def cancel(self, previous, **kwargs):
            assert kwargs["ownership_token"]
            assert kwargs["allow_unclaimed"] is False
            result = await super().cancel(previous, **kwargs)
            cancelled_fence.set()
            return result

    stepper = CountStepper()
    svc = service(database, backend=backend, store=Store(database), stepper=stepper)
    request = command(timeout_seconds=1.0 if failure == "timeout" else 120)
    pending = asyncio.create_task(svc.start(context=context(), command=request))
    await asyncio.wait_for(entered.wait(), 3)
    if failure == "cancel":
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    elif failure == "timeout":
        with pytest.raises(DomainError, match="DURABLE_STORAGE_TIMEOUT"):
            await asyncio.wait_for(pending, 3)
    else:
        release.set()
        with pytest.raises(DomainError, match="DURABLE_STORAGE_UNAVAILABLE"):
            await pending
    release.set()
    await asyncio.wait_for(storage_finished.wait(), 3)
    await asyncio.wait_for(cancelled_fence.wait(), 3)
    await drain_cleanup(svc)
    assert stepper.calls == 0
    recovered = await svc.recover(context=context(), idempotency_key=request.idempotency_key)
    assert recovered.status == "cancelled"
    assert recovered.steps_used == recovered.tool_calls_used == 0


async def test_expired_initial_lease_never_reacquires_or_resets_deadline(database, backend):
    clock = Clock()

    class Store(SqlAlchemyDurableStore):
        async def create_keyed(self, snapshot, **kwargs):
            result = await super().create_keyed(snapshot, **kwargs)
            clock.value += 31
            return result

    stepper = CountStepper()
    svc = service(database, backend=backend, clock=clock, store=Store(database), stepper=stepper)
    request = command()
    original_time = clock.value
    with pytest.raises(DomainError, match="DURABLE_LEASE_LOST"):
        await svc.start(context=context(), command=request)
    current = await svc.start(context=context(), command=request)
    recovered = await svc.recover(context=context(), idempotency_key=request.idempotency_key)
    assert current == recovered
    assert current.status == "running" and current.revision == 0
    assert stepper.calls == 0
    assert current.deadline_at == original_time + 120
    result = await svc.resume(
        context=context(), run_id=current.run_id, command=DurableResumeCommand(expected_revision=0)
    )
    assert result.status == "completed" and result.deadline_at == current.deadline_at


async def test_read_create_purge_race_retains_key_and_never_recreates(database, backend):
    class PurgingStore(SqlAlchemyDurableStore):
        async def create_keyed(self, snapshot, **kwargs):
            # Another owner of this request committed and expired the private row
            # after the initial lookup, before our insert reached the database.
            await super().create_keyed(snapshot, **kwargs)
            await super().purge_expired(tenant_id=snapshot.tenant_id, now=snapshot.expires_at + 1)
            return await super().create_keyed(snapshot, **kwargs)

    stepper = CountStepper()
    svc = service(database, backend=backend, store=PurgingStore(database), stepper=stepper)
    request = command()
    with pytest.raises(DomainError) as error:
        await svc.start(context=context(), command=request)
    assert error.value.status_code == 410
    assert stepper.calls == 0
    async with database() as session:
        assert len((await session.scalars(select(AgentRunModel))).all()) == 1
        assert not (await session.scalars(select(DurableAgentRunModel))).all()


async def test_legacy_collision_and_unrelated_integrity_errors_fail_closed(database, backend):
    request = command()
    svc = service(database, backend=backend)
    paused = await svc.start(
        context=context(),
        command=DurableStartCommand(input="Calculate", approval_tools=("calculator",)),
    )
    store = SqlAlchemyDurableStore(database)
    original = await store.load(tenant_id="tenant-a", user_id="user-a", run_id=paused.run_id)
    occupied = original.model_copy(
        update={
            "run_id": keyed_run_id(
                tenant_id="tenant-a", user_id="user-a", key=request.idempotency_key
            )
        }
    )
    await store.create(occupied)
    with pytest.raises(DomainError, match="DURABLE_START_KEY_COLLISION"):
        await svc.start(context=context(), command=request)

    class BrokenStore(SqlAlchemyDurableStore):
        @staticmethod
        def _add_new(session, snapshot, **kwargs):
            SqlAlchemyDurableStore._add_new(session, snapshot, **kwargs)
            for row in session.new:
                if isinstance(row, AgentRunModel):
                    row.request_id = None  # Real NOT NULL failure, not a duplicate.

    with pytest.raises(DomainError, match="DURABLE_STORAGE_UNAVAILABLE") as error:
        await service(database, backend=backend, store=BrokenStore(database)).start(
            context=context(), command=command()
        )
    assert error.value.status_code == 503
    assert "INSERT" not in str(error.value)


def test_snapshot_optional_startup_extension_is_versioned_and_unkeyed_shape_stable():
    from packages.agent.runtime import AgentRunConfig

    snapshot = DurableSnapshot(
        run_id=str(uuid4()),
        tenant_id="t",
        user_id="u",
        request_id="r",
        trace_id="x",
        backend="native",
        config=AgentRunConfig(
            max_steps=8, max_tool_calls=6, timeout_seconds=120, repeated_action_threshold=3
        ),
        started_at=0,
        deadline_at=120,
        last_observed_at=0,
        expires_at=86400,
    )
    assert "startup" not in snapshot.persisted()
    assert DurableSnapshot.model_validate(snapshot.persisted()).startup is None
    keyed = snapshot.model_copy(update={"startup": DurableStartup(command_fingerprint="a" * 64)})
    payload = keyed.persisted()
    payload["startup"]["protocol_version"] = 2
    with pytest.raises(ValidationError):
        DurableSnapshot.model_validate(payload)


async def test_cross_owner_primary_key_collision_does_not_return_other_snapshot(database, backend):
    request = command()
    svc = service(database, backend=backend)
    paused = await svc.start(
        context=context(user="other"),
        command=DurableStartCommand(input="Other owner", approval_tools=("calculator",)),
    )
    store = SqlAlchemyDurableStore(database)
    original = await store.load(tenant_id="tenant-a", user_id="other", run_id=paused.run_id)
    occupied = original.model_copy(
        update={
            "run_id": keyed_run_id(
                tenant_id="tenant-a", user_id="user-a", key=request.idempotency_key
            ),
            "startup": DurableStartup(command_fingerprint=start_command_fingerprint(request)),
        }
    )
    await store.create(occupied)
    with pytest.raises(DomainError, match="DURABLE_STORAGE_UNAVAILABLE") as error:
        await svc.start(context=context(), command=request)
    assert error.value.status_code == 503
    assert "other" not in str(error.value)


async def test_keyed_lookup_rejects_unsafe_sql_logging_without_private_output(database, caplog):
    request = command()
    svc = service(database)
    await svc.start(context=context(), command=request)
    engine = database.kw["bind"]
    engine.echo = True
    try:
        with pytest.raises(DomainError, match="DURABLE_STORAGE_LOGGING_UNSAFE"):
            await svc.start(context=context(), command=request)
        with pytest.raises(DomainError, match="DURABLE_STORAGE_LOGGING_UNSAFE"):
            await svc.recover(context=context(), idempotency_key=request.idempotency_key)
    finally:
        engine.echo = False
    assert str(request.idempotency_key) not in caplog.text
    assert start_command_fingerprint(request) not in caplog.text


@pytest.mark.parametrize("stage", ["created_audit", "before_verify", "after_verify", "model"])
async def test_keyed_creator_cancellation_always_requires_its_token(database, backend, stage):
    entered = asyncio.Event()
    release_cancel = asyncio.Event()
    entered_cancel = asyncio.Event()

    class Store(SqlAlchemyDurableStore):
        async def renew(self, snapshot, **kwargs):
            if stage == "before_verify":
                entered.set()
                await asyncio.Event().wait()
            result = await super().renew(snapshot, **kwargs)
            if stage == "after_verify":
                entered.set()
                await asyncio.Event().wait()
            return result

        async def cancel(self, previous, **kwargs):
            assert kwargs["allow_unclaimed"] is False
            assert await self.owns(previous, token=kwargs["ownership_token"], allow_unclaimed=False)
            entered_cancel.set()
            await release_cancel.wait()
            return await super().cancel(previous, **kwargs)

    class Audit:
        async def record(self, event):
            if stage == "created_audit" and event.action == "agent.durable.created":
                entered.set()
                await asyncio.Event().wait()

    class WaitingStepper(CountStepper):
        async def next_step(self, state):
            if stage == "model":
                entered.set()
                await asyncio.Event().wait()
            return await super().next_step(state)

    svc = service(database, backend=backend, store=Store(database), stepper=WaitingStepper())
    svc._audit = Audit()
    request = command()
    pending = asyncio.create_task(svc.start(context=context(), command=request))
    await asyncio.wait_for(entered.wait(), 3)
    pending.cancel()
    await asyncio.wait_for(entered_cancel.wait(), 3)
    pending.cancel()  # Repeated request cancellation cannot interrupt the fence.
    release_cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await drain_cleanup(svc)
    result = await svc.recover(context=context(), idempotency_key=request.idempotency_key)
    assert result.status == "cancelled" and result.tool_calls_used == 0


async def test_duplicate_cannot_refresh_expired_approval_or_execution_budget(database, backend):
    clock = Clock()
    svc = service(database, backend=backend, clock=clock)
    request = command(approval_tools=("calculator",))
    paused = await svc.start(context=context(), command=request)
    clock.value += 121
    assert await svc.start(context=context(), command=request) == paused
    assert await svc.recover(context=context(), idempotency_key=request.idempotency_key) == paused
    assert paused.approval is not None
    with pytest.raises(DomainError, match="DURABLE_APPROVAL_STALE"):
        await svc.resume(
            context=context(),
            run_id=paused.run_id,
            command=DurableResumeCommand(
                expected_revision=paused.revision,
                approval_id=paused.approval.approval_id,
                approve=True,
            ),
        )
