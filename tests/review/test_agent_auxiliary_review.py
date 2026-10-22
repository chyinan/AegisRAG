"""Independent adversarial checks for Agent telemetry and cancellation ownership."""

from __future__ import annotations

import asyncio

import pytest

from apps.api.factories.agent import create_agent_runtime
from packages.agent.runtime import AgentRunConfig, AgentStepDecision, _await_with_timeout
from packages.common.audit import InMemoryAuditPort
from tests.unit.agent.test_auxiliary_io import BlockingTelemetry
from tests.unit.agent.test_runtime import FakeStepper, HandlerProbe, _registry
from tests.unit.agent.test_streaming_service import _allowed_context, _final


async def test_external_cancel_during_runtime_timeout_cleanup_is_not_timeout() -> None:
    blocked = BlockingTelemetry(stubborn=True)
    caller = asyncio.create_task(_await_with_timeout(blocked.wait(), timeout_seconds=0.01))
    try:
        await asyncio.wait_for(blocked.cancelled.wait(), 2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    finally:
        blocked.release.set()
        await asyncio.wait_for(blocked.finished.wait(), 1)
        await asyncio.gather(caller, return_exceptions=True)
        await asyncio.sleep(0)


@pytest.mark.parametrize("backend", ("native", "langgraph"))
async def test_disconnect_during_registry_audit_timeout_cleanup_stays_cancelled(
    backend: str,
) -> None:
    blocked = BlockingTelemetry(stubborn=True)
    registry = _registry(handler=HandlerProbe())
    registry._audit = blocked
    registry._auxiliary_io_timeout_seconds = 5.0
    runtime = create_agent_runtime(
        backend=backend,
        registry=registry,
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "x"}), _final()]),
        audit=InMemoryAuditPort(),
        config=AgentRunConfig(
            max_steps=3, max_tool_calls=2, timeout_seconds=1.0,
            repeated_action_threshold=2, auxiliary_io_timeout_seconds=0.05,
        ),
    )
    caller = asyncio.create_task(runtime.run(context=_allowed_context()))
    try:
        await asyncio.wait_for(blocked.cancelled.wait(), 2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    finally:
        blocked.release.set()
        if blocked.entered.is_set():
            await asyncio.wait_for(blocked.finished.wait(), 1)
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        await asyncio.sleep(0.06)


@pytest.mark.parametrize("backend", ("native", "langgraph"))
@pytest.mark.parametrize(
    "failure_stage", ("update_error", "commit_error", "update_cancel", "commit_cancel"),
)
async def test_self_cancel_does_not_claim_persisted_cancel_when_storage_failed(
    backend: str, failure_stage: str,
) -> None:
    from packages.agent.dto import AgentRunCommand, AgentRunRecord, AgentRunUpdate
    from packages.agent.exceptions import AGENT_RUN_STORAGE_FAILED, agent_run_storage_failed
    from packages.agent.runtime import AgentRuntimeState
    from tests.unit.agent.test_agent_run_service import FakeAgentRunRepository
    from tests.unit.agent.test_streaming_service import _service

    class SelfCancellingStepper:
        async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
            raise asyncio.CancelledError("private provider operation")

    class FailedTerminalRepository(FakeAgentRunRepository):
        async def update_run_result(
            self, *, tenant_id: str, user_id: str, run_id: str, update: AgentRunUpdate,
        ) -> AgentRunRecord:
            if failure_stage == "update_cancel":
                raise asyncio.CancelledError("private storage operation")
            return await super().update_run_result(
                tenant_id=tenant_id, user_id=user_id, run_id=run_id, update=update,
            )

        async def commit(self) -> None:
            if self.updated is not None:
                if failure_stage == "commit_error":
                    raise agent_run_storage_failed(reason="private storage operation")
                if failure_stage == "commit_cancel":
                    raise asyncio.CancelledError("private storage operation")
            await super().commit()

    service, repository = _service(
        backend, SelfCancellingStepper(),
        FailedTerminalRepository(fail_update=failure_stage == "update_error"),
    )
    events = [
        event async for event in service.stream(
            context=_allowed_context(), command=AgentRunCommand(input="x"),
        )
    ]
    assert repository.events.count("commit") == 1  # Only initial running state exists.
    assert events[-1].event == "error"
    assert events[-1].data["code"] == AGENT_RUN_STORAGE_FAILED


async def test_service_token_bridge_denial_is_not_held_open_by_auxiliary_audit() -> None:
    from packages.agent.service_token_bridge import (
        SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN,
        ServiceTokenToolBridge,
        ServiceTokenToolBridgeCandidate,
        ServiceTokenToolChoice,
    )
    from packages.common.errors import DomainError
    from tests.unit.agent.test_service_token_bridge import (
        FakeAgentRunRepository,
        FakeToolCallRepository,
        _context,
    )

    blocked = BlockingTelemetry()
    bridge = ServiceTokenToolBridge(
        registry=_registry(handler=HandlerProbe()),
        agent_runs=FakeAgentRunRepository(),
        tool_calls=FakeToolCallRepository(),
        audit=blocked,
    )
    with pytest.raises(DomainError) as failure:
        await asyncio.wait_for(
            bridge.execute(
                context=_context(permissions=()),
                latest_user_message="x",
                session_id=None,
                candidates=(ServiceTokenToolBridgeCandidate(
                    name="demo_tool", description="test", schema_summary={},
                    declaration_type="modern",
                ),),
                tool_choice=ServiceTokenToolChoice(), requested_model="fake",
            ),
            timeout=1.25,
        )
    assert failure.value.code == SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN
    assert blocked.cancelled.is_set()


async def test_repeated_external_cancellation_retains_then_reaps_private_hook(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import packages.agent.auxiliary_io as auxiliary
    import packages.common.auxiliary_io as shared_auxiliary
    from tests.unit.agent.test_auxiliary_io import CANARY

    blocked = BlockingTelemetry(stubborn=True)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unexpected_errors: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, details: unexpected_errors.append(details))
    # Cleanup ownership moved to the shared implementation; keep exercising
    # the public Agent wrapper and observe the same retained/reaped task state.
    prior_pending = set(shared_auxiliary._pending_cleanup)
    caller = asyncio.create_task(auxiliary.await_auxiliary_io(blocked.wait(), timeout_seconds=5))
    try:
        await asyncio.wait_for(blocked.entered.wait(), 1)
        caller.cancel()
        await asyncio.wait_for(blocked.cancelled.wait(), 1)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert len(shared_auxiliary._pending_cleanup - prior_pending) == 1
        blocked.release.set()
        await asyncio.wait_for(blocked.finished.wait(), 1)
        await asyncio.sleep(0)
        assert shared_auxiliary._pending_cleanup == prior_pending
        assert unexpected_errors == []
        assert CANARY not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)
    finally:
        blocked.release.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        loop.set_exception_handler(previous_handler)


@pytest.mark.parametrize("backend", ("native", "langgraph"))
async def test_real_disconnect_still_propagates_when_cancel_persistence_fails(
    backend: str, caplog: pytest.LogCaptureFixture,
) -> None:
    from packages.agent.dto import AgentRunCommand
    from tests.unit.agent.test_agent_run_service import FakeAgentRunRepository
    from tests.unit.agent.test_streaming_service import WaitingStepper, _service

    stepper = WaitingStepper()
    service, repository = _service(backend, stepper, FakeAgentRunRepository(fail_update=True))
    caller = asyncio.create_task(
        service.run(context=_allowed_context(), command=AgentRunCommand(input="x")),
    )
    await asyncio.wait_for(stepper.entered.wait(), 2)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(caller, 2)
    assert repository.events.count("commit") == 1
    assert "agent.run.cancel_persistence_failed" in caplog.text
    assert "select *" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


async def test_service_token_audit_preserves_disconnect_and_redacts_failure_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from packages.agent.service_token_bridge import ServiceTokenToolBridge
    from packages.common.audit import AuditStatus
    from tests.unit.agent.test_auxiliary_io import CANARY, FailingTelemetry
    from tests.unit.agent.test_service_token_bridge import (
        FakeAgentRunRepository,
        FakeToolCallRepository,
    )

    blocked = BlockingTelemetry()
    bridge = ServiceTokenToolBridge(
        registry=_registry(handler=HandlerProbe()),
        agent_runs=FakeAgentRunRepository(),
        tool_calls=FakeToolCallRepository(),
        audit=blocked,
    )
    caller = asyncio.create_task(bridge._record_bridge_audit(
        context=_allowed_context(), status=AuditStatus.DENIED,
        error_code="DENIED", metadata={},
    ))
    await asyncio.wait_for(blocked.entered.wait(), 1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert blocked.finished.is_set()
    assert blocked.cancelled.is_set()

    bridge._audit = FailingTelemetry()
    await bridge._record_bridge_audit(
        context=_allowed_context(), status=AuditStatus.DENIED,
        error_code="DENIED", metadata={},
    )
    assert "agent.service_token_bridge.audit_failed" in caplog.text
    assert CANARY not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
