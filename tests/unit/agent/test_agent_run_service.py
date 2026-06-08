from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

import pytest

from packages.agent.dto import AgentRunCommand, AgentRunCreate, AgentRunRecord, AgentRunUpdate
from packages.agent.exceptions import (
    AGENT_RUN_FAILED,
    AGENT_RUN_FORBIDDEN,
    AGENT_RUN_STORAGE_FAILED,
    AgentRunError,
)
from packages.agent.runtime import AgentRunResult, AgentRunStatus, AgentTerminationReason
from packages.agent.service import AgentRunApplicationService
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext


@pytest.mark.asyncio
async def test_agent_run_service_persists_running_before_runtime_then_completes() -> None:
    repository = FakeAgentRunRepository()
    runtime = FakeRuntime(
        AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            termination_reason=AgentTerminationReason.FINAL_ANSWER,
            steps_used=1,
            tool_calls_used=0,
            final_answer="done",
            error_code=None,
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            latency_ms=12.5,
            metadata={
                "steps_used": 1,
                "prompt": "must redact",
                "safe_label": "ok",
                "path_value": "C:\\sensitive\\document.txt",
                "long_value": "x" * 201,
                "file_content": "must redact",
            },
        )
    )
    audit = InMemoryAuditPort()
    service = AgentRunApplicationService(
        repository=repository,
        runtime_factory=lambda _config, _agent_run_id: runtime,
        audit=audit,
        default_max_steps=8,
        default_max_tool_calls=5,
        default_timeout_seconds=30.0,
        repeated_action_threshold=3,
        perf_counter=PerfCounter([1.0, 1.1]),
    )

    response = await service.run(context=_context(), command=AgentRunCommand(input="hello world"))

    assert response.agent_run_id == "run-1"
    assert response.status == "completed"
    assert response.final_answer == "done"
    assert response.termination_reason == "FINAL_ANSWER"
    assert response.steps_used == 1
    assert repository.events == [
        "create:running",
        "commit",
        "runtime",
        "update:completed",
        "commit",
    ]
    assert repository.created is not None
    assert repository.created.input_summary == {
        "length": 11,
        "sha256": repository.created.input_summary["sha256"],
    }
    assert "hello world" not in str(repository.created.metadata)
    assert repository.updated is not None
    assert "done" not in str(repository.updated.metadata)
    assert repository.updated.metadata["safe_counts"] == {
        "observation_count": 0,
        "metadata_count": 6,
    }
    assert repository.updated.metadata["safe_label"] == "ok"
    assert "prompt" not in repository.updated.metadata
    assert "path_value" not in repository.updated.metadata
    assert "long_value" not in repository.updated.metadata
    assert "file_content" not in repository.updated.metadata
    assert [event.action for event in audit.events] == [
        "agent.run.started",
        "agent.run.completed",
    ]
    assert audit.events[-1].resource.id == "run-1"
    assert audit.events[-1].metadata["agent_run_id"] == "run-1"


@pytest.mark.asyncio
async def test_agent_run_service_maps_limit_result_to_stopped() -> None:
    repository = FakeAgentRunRepository()
    runtime = FakeRuntime(
        AgentRunResult(
            status=AgentRunStatus.STOPPED,
            termination_reason=AgentTerminationReason.MAX_STEPS_REACHED,
            steps_used=2,
            tool_calls_used=1,
            error_code="MAX_STEPS_REACHED",
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            latency_ms=5.0,
            metadata={"steps_used": 2},
        )
    )
    service = AgentRunApplicationService(
        repository=repository,
        runtime_factory=lambda _config, _agent_run_id: runtime,
        audit=InMemoryAuditPort(),
        default_max_steps=2,
        default_max_tool_calls=5,
        default_timeout_seconds=30.0,
        repeated_action_threshold=3,
    )

    response = await service.run(context=_context(), command=AgentRunCommand(input="x"))

    assert response.status == "stopped"
    assert response.error_code == "MAX_STEPS_REACHED"
    assert repository.updated is not None
    assert repository.updated.status == "stopped"


@pytest.mark.asyncio
async def test_agent_run_service_maps_tool_failure_to_failed() -> None:
    repository = FakeAgentRunRepository()
    runtime = FakeRuntime(
        AgentRunResult(
            status=AgentRunStatus.FAILED,
            termination_reason=AgentTerminationReason.AGENT_TOOL_FAILED,
            steps_used=1,
            tool_calls_used=1,
            error_code="AGENT_TOOL_FAILED",
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            latency_ms=7.0,
            metadata={"tool_name": "calculator", "argument_keys": ["expression"]},
        )
    )
    service = AgentRunApplicationService(
        repository=repository,
        runtime_factory=lambda _config, _agent_run_id: runtime,
        audit=InMemoryAuditPort(),
        default_max_steps=8,
        default_max_tool_calls=5,
        default_timeout_seconds=30.0,
        repeated_action_threshold=3,
    )

    response = await service.run(context=_context(), command=AgentRunCommand(input="x"))

    assert response.status == "failed"
    assert response.error_code == "AGENT_TOOL_FAILED"
    assert repository.updated is not None
    assert repository.updated.status == "failed"


@pytest.mark.asyncio
async def test_agent_run_service_denies_missing_permission_before_persistence() -> None:
    repository = FakeAgentRunRepository()
    runtime = FakeRuntime()
    service = AgentRunApplicationService(
        repository=repository,
        runtime_factory=lambda _config, _agent_run_id: runtime,
        audit=InMemoryAuditPort(),
        default_max_steps=8,
        default_max_tool_calls=5,
        default_timeout_seconds=30.0,
        repeated_action_threshold=3,
    )

    with pytest.raises(AgentRunError) as exc_info:
        await service.run(
            context=_context(permissions=("document:read",)),
            command=AgentRunCommand(input="x"),
        )

    assert exc_info.value.code == AGENT_RUN_FORBIDDEN
    assert repository.events == []
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_agent_run_service_returns_storage_error_when_result_update_fails() -> None:
    repository = FakeAgentRunRepository(fail_update=True)
    runtime = FakeRuntime(
        AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            termination_reason=AgentTerminationReason.FINAL_ANSWER,
            steps_used=1,
            tool_calls_used=0,
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            latency_ms=1.0,
        )
    )
    service = AgentRunApplicationService(
        repository=repository,
        runtime_factory=lambda _config, _agent_run_id: runtime,
        audit=InMemoryAuditPort(),
        default_max_steps=8,
        default_max_tool_calls=5,
        default_timeout_seconds=30.0,
        repeated_action_threshold=3,
    )

    with pytest.raises(AgentRunError) as exc_info:
        await service.run(context=_context(), command=AgentRunCommand(input="x"))

    assert exc_info.value.code == AGENT_RUN_STORAGE_FAILED
    assert "select *" not in str(exc_info.value.details).lower()
    assert repository.events == [
        "create:running",
        "commit",
        "runtime",
        "update:completed",
        "rollback",
    ]


@pytest.mark.asyncio
async def test_agent_run_service_marks_run_failed_when_runtime_raises() -> None:
    repository = FakeAgentRunRepository()
    runtime = FakeRuntime(error=RuntimeError("stepper exploded with token='secret'"))
    audit = InMemoryAuditPort()
    service = AgentRunApplicationService(
        repository=repository,
        runtime_factory=lambda _config, _agent_run_id: runtime,
        audit=audit,
        default_max_steps=8,
        default_max_tool_calls=5,
        default_timeout_seconds=30.0,
        repeated_action_threshold=3,
        perf_counter=PerfCounter([1.0, 1.1, 1.2]),
    )

    with pytest.raises(AgentRunError) as exc_info:
        await service.run(context=_context(), command=AgentRunCommand(input="x"))

    assert exc_info.value.code == AGENT_RUN_FAILED
    assert "secret" not in str(exc_info.value.details).lower()
    assert repository.events == [
        "create:running",
        "commit",
        "runtime",
        "update:failed",
        "commit",
        "rollback",
    ]
    assert repository.updated is not None
    assert repository.updated.status == "failed"
    assert repository.updated.error_code == AGENT_RUN_FAILED
    assert audit.events[-1].action == "agent.run.failed"
    assert audit.events[-1].resource.id == "run-1"


@pytest.mark.asyncio
async def test_agent_run_service_passes_created_run_id_into_runtime_factory() -> None:
    repository = FakeAgentRunRepository()
    runtime = FakeRuntime(
        AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            termination_reason=AgentTerminationReason.FINAL_ANSWER,
            steps_used=1,
            tool_calls_used=0,
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            latency_ms=1.0,
        )
    )
    received_run_ids: list[str] = []

    def runtime_factory(_config: object, agent_run_id: str) -> FakeRuntime:
        received_run_ids.append(agent_run_id)
        return runtime

    service = AgentRunApplicationService(
        repository=repository,
        runtime_factory=runtime_factory,
        audit=InMemoryAuditPort(),
        default_max_steps=8,
        default_max_tool_calls=5,
        default_timeout_seconds=30.0,
        repeated_action_threshold=3,
    )

    await service.run(context=_context(), command=AgentRunCommand(input="x"))

    assert received_run_ids == ["run-1"]
    assert repository.events == [
        "create:running",
        "commit",
        "runtime",
        "update:completed",
        "commit",
    ]


class FakeRuntime:
    def __init__(
        self,
        result: AgentRunResult | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def run(self, *, context: AuthenticatedRequestContext) -> AgentRunResult:
        self.calls += 1
        if isinstance(context, AuthenticatedRequestContext):
            FakeAgentRunRepository.current.events.append("runtime")
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("runtime should not be called")
        return self.result


class FakeAgentRunRepository:
    current: FakeAgentRunRepository

    def __init__(self, *, fail_update: bool = False) -> None:
        FakeAgentRunRepository.current = self
        self.fail_update = fail_update
        self.events: list[str] = []
        self.created: AgentRunCreate | None = None
        self.updated: AgentRunUpdate | None = None

    async def create_run(self, record: AgentRunCreate) -> AgentRunRecord:
        self.created = record
        self.events.append(f"create:{record.status}")
        return _record(
            status=record.status,
            max_steps=record.max_steps,
            max_tool_calls=record.max_tool_calls,
            timeout_seconds=record.timeout_seconds,
            metadata=record.metadata,
        )

    async def update_run_result(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        update: AgentRunUpdate,
    ) -> AgentRunRecord:
        _ = (tenant_id, user_id, run_id)
        self.updated = update
        self.events.append(f"update:{update.status}")
        if self.fail_update:
            raise AgentRunError(
                code=AGENT_RUN_STORAGE_FAILED,
                message="Agent run storage operation failed.",
                details={"reason": "select * from agent_runs where token='secret'"},
                status_code=500,
            )
        return _record(
            status=update.status,
            max_steps=8,
            max_tool_calls=5,
            timeout_seconds=30.0,
            steps_used=update.steps_used,
            tool_calls_used=update.tool_calls_used,
            termination_reason=update.termination_reason,
            error_code=update.error_code,
            latency_ms=update.latency_ms,
            metadata=update.metadata,
        )

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


class PerfCounter:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def __call__(self) -> float:
        return self._values.pop(0)


def _context(permissions: tuple[str, ...] = ("agent:run",)) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            tenant_id="tenant-1",
            user_id="user-1",
            roles=("admin",),
            permissions=permissions,
        ),
    )


def _record(
    *,
    status: Literal["running", "completed", "stopped", "failed"],
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: float,
    steps_used: int = 0,
    tool_calls_used: int = 0,
    termination_reason: str | None = None,
    error_code: str | None = None,
    latency_ms: float | None = None,
    metadata: Mapping[str, object] | None = None,
) -> AgentRunRecord:
    now = datetime.now(tz=UTC)
    return AgentRunRecord(
        id="run-1",
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        created_by="user-1",
        status=status,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
        steps_used=steps_used,
        tool_calls_used=tool_calls_used,
        termination_reason=termination_reason,
        error_code=error_code,
        latency_ms=latency_ms,
        input_summary={"length": 1, "sha256": "abc"},
        metadata=dict(metadata or {}),
        created_at=now,
        updated_at=now,
    )
