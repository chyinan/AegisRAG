import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

import pytest
from pydantic import BaseModel

from packages.agent.dto import ToolDefinition, ToolInvocationStatus, ToolRateLimit
from packages.agent.exceptions import (
    TOOL_ALREADY_REGISTERED,
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
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Demo governed tool.",
        input_schema=DemoInput,
        output_schema=DemoOutput,
        permission=permission,
        timeout_seconds=timeout_seconds,
        rate_limit=rate_limit or ToolRateLimit(max_calls=5, window_seconds=60.0),
        handler=handler,
    )


def _registry(
    *,
    audit: InMemoryAuditPort | None = None,
    limiter: InMemoryToolRateLimiter | None = None,
) -> ToolRegistry:
    return ToolRegistry(
        audit=audit or InMemoryAuditPort(),
        rate_limiter=limiter or InMemoryToolRateLimiter(clock=lambda: 100.0),
        perf_counter=lambda: 10.0,
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
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler))

    result = await registry.execute(
        name="demo_tool",
        arguments={"query": "policy"},
        context=_context(),
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


def test_register_rejects_duplicate_tool_without_overwriting_handler() -> None:
    first = HandlerProbe(_ok_handler)
    second = HandlerProbe(_ok_handler)
    registry = _registry()
    registry.register(_definition(handler=first))

    with pytest.raises(AgentToolError) as exc_info:
        registry.register(_definition(handler=second))

    assert exc_info.value.code == TOOL_ALREADY_REGISTERED
    assert registry.get("demo_tool").handler is first


@pytest.mark.asyncio
async def test_execute_unknown_tool_records_audit_and_does_not_call_handler() -> None:
    audit = InMemoryAuditPort()
    registry = _registry(audit=audit)

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="missing_tool",
            arguments={"query": "secret"},
            context=_context(),
        )

    assert exc_info.value.code == TOOL_NOT_REGISTERED
    assert audit.events[0].status is AuditStatus.DENIED
    assert audit.events[0].error_code == TOOL_NOT_REGISTERED
    assert _audit_metadata(audit)["argument_keys"] == ["query"]
    assert "secret" not in str(_audit_metadata(audit))


@pytest.mark.asyncio
async def test_execute_rejects_invalid_input_before_handler_and_audits_safe_summary() -> None:
    audit = InMemoryAuditPort()
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(name="demo_tool", arguments={"query": 123}, context=_context())

    assert exc_info.value.code == TOOL_INPUT_VALIDATION_FAILED
    assert handler.called is False
    assert audit.events[0].status is AuditStatus.FAILURE
    assert _audit_metadata(audit)["error_fields"] == ["query"]
    assert "123" not in str(_audit_metadata(audit))


@pytest.mark.asyncio
async def test_execute_rejects_missing_permission_before_handler() -> None:
    audit = InMemoryAuditPort()
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "policy"},
            context=_context(permissions=("other:permission",)),
        )

    assert exc_info.value.code == TOOL_PERMISSION_DENIED
    assert handler.called is False
    assert audit.events[0].status is AuditStatus.DENIED


@pytest.mark.asyncio
async def test_execute_rejects_rate_limited_call_before_handler() -> None:
    audit = InMemoryAuditPort()
    limiter = InMemoryToolRateLimiter(clock=lambda: 100.0)
    handler = HandlerProbe(_ok_handler)
    registry = _registry(audit=audit, limiter=limiter)
    registry.register(
        _definition(
            handler=handler,
            rate_limit=ToolRateLimit(max_calls=1, window_seconds=60.0),
        )
    )
    await registry.execute(name="demo_tool", arguments={"query": "one"}, context=_context())

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(name="demo_tool", arguments={"query": "two"}, context=_context())

    assert exc_info.value.code == TOOL_RATE_LIMITED
    assert handler.call_count == 1
    assert audit.events[-1].status is AuditStatus.DENIED
    assert audit.events[-1].error_code == TOOL_RATE_LIMITED


@pytest.mark.asyncio
async def test_execute_maps_timeout_without_returning_handler_output() -> None:
    audit = InMemoryAuditPort()

    async def slow_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> DemoOutput:
        _ = payload, context
        await asyncio.sleep(1.0)
        return DemoOutput(answer="late-secret")

    handler = HandlerProbe(slow_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler, timeout_seconds=0.001))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(name="demo_tool", arguments={"query": "policy"}, context=_context())

    assert exc_info.value.code == TOOL_TIMEOUT
    assert audit.events[0].status is AuditStatus.FAILURE
    assert "late-secret" not in str(audit.events[0].metadata)


@pytest.mark.asyncio
async def test_execute_rejects_invalid_output_without_exposing_raw_result() -> None:
    audit = InMemoryAuditPort()

    async def invalid_output_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> dict[str, object]:
        _ = payload, context
        return {"answer": 123, "secret_result": "classified"}

    handler = HandlerProbe(invalid_output_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(name="demo_tool", arguments={"query": "policy"}, context=_context())

    assert exc_info.value.code == TOOL_OUTPUT_VALIDATION_FAILED
    assert audit.events[0].status is AuditStatus.FAILURE
    assert "classified" not in str(audit.events[0].metadata)
    assert "secret_result" not in str(audit.events[0].metadata)


@pytest.mark.asyncio
async def test_execute_wraps_handler_error_and_audits_failure() -> None:
    audit = InMemoryAuditPort()

    async def failing_handler(
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> DemoOutput:
        _ = payload, context
        raise RuntimeError("provider token leaked")

    handler = HandlerProbe(failing_handler)
    registry = _registry(audit=audit)
    registry.register(_definition(handler=handler))

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(name="demo_tool", arguments={"query": "policy"}, context=_context())

    assert exc_info.value.code == TOOL_HANDLER_FAILED
    assert audit.events[0].status is AuditStatus.FAILURE
    assert "provider token leaked" not in str(audit.events[0].metadata)


@pytest.mark.asyncio
async def test_audit_failure_logs_warning_without_faking_tool_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
