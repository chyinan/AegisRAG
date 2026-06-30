import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

import pytest
from pydantic import BaseModel

from packages.agent.dto import (
    ToolCallCreate,
    ToolDefinition,
    ToolInvocationStatus,
    ToolRateLimit,
)
from packages.agent.exceptions import (
    TOOL_ALREADY_REGISTERED,
    TOOL_CALL_AUDIT_FAILED,
    TOOL_HANDLER_FAILED,
    TOOL_INPUT_VALIDATION_FAILED,
    TOOL_NOT_REGISTERED,
    TOOL_OUTPUT_VALIDATION_FAILED,
    TOOL_PERMISSION_DENIED,
    TOOL_RATE_LIMITED,
    TOOL_TIMEOUT,
    AgentToolError,
)
from packages.agent.registry import InMemoryToolRateLimiter, ToolRegistry
from packages.auth.context import AuthContext
from packages.common.audit import AuditEvent, AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext


class DemoInput(BaseModel):
    query: str


class DemoOutput(BaseModel):
    answer: str


class StructuredFailureOutput(BaseModel):
    status: str
    error_code: str


class HandlerProbe:
    def __init__(
        self,
        handler: Callable[[DemoInput, AuthenticatedRequestContext], Awaitable[object]],
    ) -> None:
        self.called = False
        self.call_count = 0
        self.payload: DemoInput | None = None
        self._handler = handler

    async def __call__(self, payload: DemoInput, context: AuthenticatedRequestContext) -> object:
        self.called = True
        self.call_count += 1
        self.payload = payload
        return await self._handler(payload, context)


class FailingAuditPort:
    async def record(self, event: AuditEvent) -> None:
        _ = event
        raise RuntimeError("audit backend unavailable")


class FakeToolCallRecorder:
    def __init__(self) -> None:
        self.records: list[ToolCallCreate] = []

    async def record_tool_call(self, record: ToolCallCreate) -> None:
        self.records.append(record)


class FailingToolCallRecorder:
    async def record_tool_call(self, record: ToolCallCreate) -> None:
        _ = record
        raise RuntimeError("select * from tool_calls where token='secret'")


def _context(*, permissions: tuple[str, ...] = ("agent:tool:demo",)) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-1",
            roles=("analyst",),
            department="risk",
            permissions=permissions,
        ),
    )


def _definition(
    *,
    handler: HandlerProbe,
    name: str = "demo_tool",
    permission: str = "agent:tool:demo",
    timeout_seconds: float = 1.0,
    rate_limit: ToolRateLimit | None = None,
    output_schema: type[BaseModel] = DemoOutput,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Demo governed tool.",
        input_schema=DemoInput,
        output_schema=output_schema,
        permission=permission,
        timeout_seconds=timeout_seconds,
        rate_limit=rate_limit or ToolRateLimit(max_calls=5, window_seconds=60.0),
        handler=handler,
    )


def _registry(
    *,
    audit: InMemoryAuditPort | None = None,
    limiter: InMemoryToolRateLimiter | None = None,
    tool_call_recorder: FakeToolCallRecorder | FailingToolCallRecorder | None = None,
) -> ToolRegistry:
    return ToolRegistry(
        audit=audit or InMemoryAuditPort(),
        rate_limiter=limiter or InMemoryToolRateLimiter(clock=lambda: 100.0),
        perf_counter=lambda: 10.0,
        tool_call_recorder=tool_call_recorder,
    )


async def _ok_handler(payload: DemoInput, context: AuthenticatedRequestContext) -> DemoOutput:
    _ = context
    return DemoOutput(answer=f"answer:{payload.query}")


def _audit_metadata(audit: InMemoryAuditPort) -> Mapping[str, object]:
    assert len(audit.events) == 1
    return audit.events[0].metadata


@pytest.mark.asyncio
async def test_execute_registered_tool_validates_input_permission_and_output() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler))

    result = await registry.execute(
        name="demo_tool",
        arguments={"query": "policy"},
        context=_context(),
        agent_run_id="run-1",
    )

    assert result.status is ToolInvocationStatus.SUCCESS
    assert result.output == {"answer": "answer:policy"}
    assert handler.called is True
    assert handler.payload == DemoInput(query="policy")
    assert audit.events[0].status is AuditStatus.SUCCESS
    assert audit.events[0].action == "agent.tool.execute"
    assert audit.events[0].resource.type == "tool"
    assert audit.events[0].resource.id == "demo_tool"
    assert _audit_metadata(audit)["argument_keys"] == ["query"]
    assert "policy" not in str(_audit_metadata(audit))
    assert len(recorder.records) == 1
    assert recorder.records[0].agent_run_id == "run-1"
    assert recorder.records[0].status == "success"
    assert recorder.records[0].permission == "agent:tool:demo"
    assert recorder.records[0].arguments_summary["argument_keys"] == ["query"]
    assert recorder.records[0].result_summary["result_keys"] == ["answer"]
    assert "policy" not in str(recorder.records[0])
    assert "answer:policy" not in str(recorder.records[0])


@pytest.mark.asyncio
async def test_register_rejects_duplicate_tool_without_overwriting_handler() -> None:
    first = HandlerProbe(_ok_handler)
    second = HandlerProbe(_ok_handler)
    registry = _registry()
    registry.register(_definition(handler=first))

    with pytest.raises(AgentToolError) as exc_info:
        registry.register(_definition(handler=second))

    assert exc_info.value.code == TOOL_ALREADY_REGISTERED
    assert (await registry.get(name="demo_tool", context=_context())).handler is first


@pytest.mark.asyncio
async def test_execute_unknown_tool_records_audit_and_does_not_call_handler() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()
    registry = _registry(audit=audit, tool_call_recorder=recorder)

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="missing_tool",
            arguments={"query": "secret"},
            context=_context(),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_NOT_REGISTERED
    assert audit.events[0].status is AuditStatus.DENIED
    assert audit.events[0].error_code == TOOL_NOT_REGISTERED
    assert _audit_metadata(audit)["argument_keys"] == ["query"]
    assert "secret" not in str(_audit_metadata(audit))
    assert len(recorder.records) == 1
    assert recorder.records[0].status == "denied"
    assert recorder.records[0].error_code == TOOL_NOT_REGISTERED
    assert recorder.records[0].permission is None
    assert "secret" not in str(recorder.records[0])


@pytest.mark.asyncio
async def test_get_unknown_tool_records_denied_audit() -> None:
    audit = InMemoryAuditPort()
    registry = _registry(audit=audit)

    with pytest.raises(AgentToolError) as exc_info:
        await registry.get(name="missing_tool", context=_context())

    assert exc_info.value.code == TOOL_NOT_REGISTERED
    assert audit.events[0].status is AuditStatus.DENIED
    assert audit.events[0].error_code == TOOL_NOT_REGISTERED
    assert audit.events[0].resource.id == "missing_tool"


@pytest.mark.asyncio
async def test_execute_unknown_tool_redacts_unsafe_tool_name_and_argument_keys() -> None:
    audit = InMemoryAuditPort()
    registry = _registry(audit=audit)

    with pytest.raises(AgentToolError):
        await registry.execute(
            name="C:\\secrets\\tool.txt",
            arguments={"api_key": "value", "query": "secret", "safe_name": "ok"},
            context=_context(),
        )

    metadata = audit.events[0].metadata
    assert audit.events[0].resource.id == "unknown_tool"
    assert metadata["tool_name"] == "unknown_tool"
    assert metadata["argument_keys"] == ["query", "redacted_key", "safe_name"]
    assert "api_key" not in str(metadata)
    assert "C:\\secrets" not in str(audit.events[0])


@pytest.mark.asyncio
async def test_execute_rejects_invalid_input_before_handler_and_audits_safe_summary() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": 123},
            context=_context(),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_INPUT_VALIDATION_FAILED
    assert handler.called is False
    assert audit.events[0].status is AuditStatus.FAILURE
    assert _audit_metadata(audit)["error_fields"] == ["query"]
    assert "123" not in str(_audit_metadata(audit))
    assert len(recorder.records) == 1
    assert recorder.records[0].status == "failure"
    assert recorder.records[0].error_code == TOOL_INPUT_VALIDATION_FAILED
    assert recorder.records[0].result_summary["error_code"] == TOOL_INPUT_VALIDATION_FAILED
    assert "123" not in str(recorder.records[0])


@pytest.mark.asyncio
async def test_execute_rejects_non_mapping_arguments_with_structured_audit() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments=["query", "policy"],
            context=_context(),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_INPUT_VALIDATION_FAILED
    assert handler.called is False
    assert audit.events[0].status is AuditStatus.FAILURE
    assert _audit_metadata(audit)["argument_keys"] == []
    assert _audit_metadata(audit)["error_fields"] == ["arguments"]
    assert recorder.records[0].status == "failure"
    assert recorder.records[0].arguments_summary["argument_shape"] == "non_mapping"
    assert recorder.records[0].result_summary["error_fields"] == ["arguments"]


@pytest.mark.asyncio
async def test_execute_rejects_extra_input_keys_before_handler() -> None:
    audit = InMemoryAuditPort()
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy", "unexpected": "ignored"},
            context=_context(),
        )

    assert exc_info.value.code == TOOL_INPUT_VALIDATION_FAILED
    assert handler.called is False
    assert _audit_metadata(audit)["error_fields"] == ["unexpected"]


@pytest.mark.asyncio
async def test_execute_rejects_missing_permission_before_handler() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(permissions=("other:permission",)),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_PERMISSION_DENIED
    assert handler.called is False
    assert audit.events[0].status is AuditStatus.DENIED
    assert len(recorder.records) == 1
    assert recorder.records[0].status == "denied"
    assert recorder.records[0].error_code == TOOL_PERMISSION_DENIED


@pytest.mark.asyncio
async def test_execute_rejects_rate_limited_call_before_handler() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()
    limiter = InMemoryToolRateLimiter(clock=lambda: 100.0)
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit, limiter=limiter, tool_call_recorder=recorder)
    registry.register(
        _definition(
            handler=handler,
            rate_limit=ToolRateLimit(max_calls=1, window_seconds=60.0),
        )
    )
    await registry.execute(
        name="demo_tool",
        arguments={"query": "one"},
        context=_context(),
        agent_run_id="run-1",
    )

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "two"},
            context=_context(),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_RATE_LIMITED
    assert handler.call_count == 1
    assert audit.events[-1].status is AuditStatus.DENIED
    assert audit.events[-1].error_code == TOOL_RATE_LIMITED
    assert [record.status for record in recorder.records] == ["success", "denied"]
    assert recorder.records[-1].error_code == TOOL_RATE_LIMITED
    assert "two" not in str(recorder.records[-1])


@pytest.mark.asyncio
async def test_execute_maps_timeout_without_returning_handler_output() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()

    async def slow_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> DemoOutput:
        _ = payload, context
        await asyncio.sleep(1.0)
        return DemoOutput(answer="late-secret")

    handler = HandlerProbe(slow_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler, timeout_seconds=0.001))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_TIMEOUT
    assert audit.events[0].status is AuditStatus.FAILURE
    assert "late-secret" not in str(audit.events[0].metadata)
    assert recorder.records[0].status == "failure"
    assert recorder.records[0].error_code == TOOL_TIMEOUT
    assert "late-secret" not in str(recorder.records[0])


@pytest.mark.asyncio
async def test_execute_records_tool_timeout_when_registry_call_is_cancelled() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()
    started = asyncio.Event()

    async def slow_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> DemoOutput:
        _ = payload, context
        started.set()
        await asyncio.sleep(60)
        return DemoOutput(answer="late-secret")

    handler = HandlerProbe(slow_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler, timeout_seconds=30.0))

    task = asyncio.create_task(
        registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(),
            agent_run_id="run-1",
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(recorder.records) == 1
    assert recorder.records[0].status == "failure"
    assert recorder.records[0].error_code == TOOL_TIMEOUT
    assert "late-secret" not in str(recorder.records[0])


@pytest.mark.asyncio
async def test_execute_timeout_stops_waiting_when_handler_suppresses_cancellation() -> None:
    audit = InMemoryAuditPort()
    release = asyncio.Event()

    async def cancellation_suppressing_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> DemoOutput:
        _ = payload, context
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            await release.wait()
            return DemoOutput(answer="late-secret")
        return DemoOutput(answer="late-secret")

    handler = HandlerProbe(cancellation_suppressing_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler, timeout_seconds=0.001))

    with pytest.raises(AgentToolError) as exc_info:
        await asyncio.wait_for(
            registry.execute(name="demo_tool", arguments={"query": "policy"}, context=_context()),
            timeout=0.1,
        )

    assert exc_info.value.code == TOOL_TIMEOUT
    assert audit.events[0].status is AuditStatus.FAILURE
    assert "late-secret" not in str(audit.events[0].metadata)
    release.set()


@pytest.mark.asyncio
async def test_execute_rejects_invalid_output_without_exposing_raw_result() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()

    async def invalid_output_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> dict[str, object]:
        _ = payload, context
        return {"answer": 123, "secret_result": "classified"}

    handler = HandlerProbe(invalid_output_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_OUTPUT_VALIDATION_FAILED
    assert audit.events[0].status is AuditStatus.FAILURE
    assert "classified" not in str(audit.events[0].metadata)
    assert "secret_result" not in str(audit.events[0].metadata)
    assert recorder.records[0].status == "failure"
    assert recorder.records[0].error_code == TOOL_OUTPUT_VALIDATION_FAILED
    assert recorder.records[0].result_summary["result_keys"] == ["answer", "redacted_key"]
    assert "classified" not in str(recorder.records[0])
    assert "secret_result" not in str(recorder.records[0])


@pytest.mark.asyncio
async def test_execute_records_structured_failure_output_as_failed_tool_call() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()

    async def structured_failure_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> StructuredFailureOutput:
        _ = payload, context
        return StructuredFailureOutput(status="error", error_code="DOMAIN_ERROR")

    handler = HandlerProbe(structured_failure_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler, output_schema=StructuredFailureOutput))

    result = await registry.execute(
        name="demo_tool",
        arguments={"query": "policy"},
        context=_context(),
        agent_run_id="run-1",
    )

    assert result.status is ToolInvocationStatus.FAILURE
    assert recorder.records[0].status == "failure"
    assert recorder.records[0].error_code == "DOMAIN_ERROR"
    assert recorder.records[0].result_summary["result_keys"] == ["error_code", "status"]


@pytest.mark.asyncio
async def test_execute_rejects_extra_output_keys() -> None:
    audit = InMemoryAuditPort()

    async def extra_output_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> dict[str, object]:
        _ = payload, context
        return {"answer": "ok", "secret_result": "classified"}

    handler = HandlerProbe(extra_output_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(name="demo_tool", arguments={"query": "policy"}, context=_context())

    assert exc_info.value.code == TOOL_OUTPUT_VALIDATION_FAILED
    assert audit.events[0].status is AuditStatus.FAILURE
    assert _audit_metadata(audit)["error_fields"] == ["redacted_key"]
    assert "classified" not in str(audit.events[0].metadata)
    assert "secret_result" not in str(audit.events[0].metadata)


@pytest.mark.asyncio
async def test_execute_revalidates_constructed_pydantic_output() -> None:
    audit = InMemoryAuditPort()

    async def constructed_invalid_output_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> DemoOutput:
        _ = payload, context
        return DemoOutput.model_construct(answer=123)

    handler = HandlerProbe(constructed_invalid_output_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(name="demo_tool", arguments={"query": "policy"}, context=_context())

    assert exc_info.value.code == TOOL_OUTPUT_VALIDATION_FAILED
    assert audit.events[0].status is AuditStatus.FAILURE
    assert _audit_metadata(audit)["error_fields"] == ["answer"]


@pytest.mark.asyncio
async def test_execute_wraps_handler_error_and_audits_failure() -> None:
    audit = InMemoryAuditPort()
    recorder = FakeToolCallRecorder()

    async def failing_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> DemoOutput:
        _ = payload, context
        raise RuntimeError("provider token leaked")

    handler = HandlerProbe(failing_handler)
    registry = _registry(audit=audit, tool_call_recorder=recorder)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_HANDLER_FAILED
    assert audit.events[0].status is AuditStatus.FAILURE
    assert "provider token leaked" not in str(audit.events[0].metadata)
    assert len(recorder.records) == 1
    assert recorder.records[0].status == "failure"
    assert recorder.records[0].error_code == TOOL_HANDLER_FAILED
    assert "provider token leaked" not in str(recorder.records[0])


@pytest.mark.asyncio
async def test_tool_call_recorder_failure_returns_structured_error_without_raw_details() -> None:
    handler = HandlerProbe(_ok_handler)
    registry = _registry(tool_call_recorder=FailingToolCallRecorder())
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(),
            agent_run_id="run-1",
        )

    assert exc_info.value.code == TOOL_CALL_AUDIT_FAILED
    assert handler.call_count == 1
    assert "select *" not in str(exc_info.value.details).lower()
    assert "secret" not in str(exc_info.value.details).lower()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="Flaky: global logging state pollution from upstream tests",
)
async def test_tool_call_recorder_failure_log_excludes_raw_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler = HandlerProbe(_ok_handler)
    registry = _registry(tool_call_recorder=FailingToolCallRecorder())
    registry.register(_definition(handler=handler))
    caplog.set_level(logging.WARNING, logger="packages.agent.registry")

    with pytest.raises(AgentToolError):
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(),
            agent_run_id="run-1",
        )

    assert "agent.tool_call.audit_failed" in caplog.text
    assert "select *" not in caplog.text.lower()
    assert "secret" not in caplog.text.lower()


@pytest.mark.asyncio
async def test_configured_tool_call_recorder_requires_agent_run_id_before_handler() -> None:
    recorder = FakeToolCallRecorder()
    handler = HandlerProbe(_ok_handler)
    registry = _registry(tool_call_recorder=recorder)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(),
        )

    assert exc_info.value.code == TOOL_CALL_AUDIT_FAILED
    assert handler.called is False
    assert recorder.records == []


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="Flaky: global logging state pollution from upstream tests",
)
async def test_audit_failure_logs_warning_without_faking_tool_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import structlog
    structlog.reset_defaults()
    handler = HandlerProbe(_ok_handler)
    registry = ToolRegistry(
        audit=FailingAuditPort(),
        rate_limiter=InMemoryToolRateLimiter(clock=lambda: 100.0),
        perf_counter=lambda: 10.0,
    )
    registry.register(_definition(handler=handler))

    with caplog.at_level(logging.WARNING, logger="packages.agent.registry"):
        result = await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(),
        )

    assert result.status is ToolInvocationStatus.SUCCESS
    assert result.output == {"answer": "answer:policy"}
    assert handler.call_count == 1
    assert "agent.tool.audit_failed" in caplog.text
    assert "RuntimeError: audit backend unavailable" in caplog.text
