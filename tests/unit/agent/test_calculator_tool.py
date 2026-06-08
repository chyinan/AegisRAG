from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from packages.agent.dto import ToolExecutionResult, ToolInvocationStatus, ToolRateLimit
from packages.agent.exceptions import (
    TOOL_INPUT_VALIDATION_FAILED,
    TOOL_PERMISSION_DENIED,
    AgentToolError,
)
from packages.agent.registry import InMemoryToolRateLimiter, ToolRegistry
from packages.agent.tools import (
    CALCULATOR_PERMISSION,
    CalculatorOutput,
    build_calculator_tool,
)
from packages.auth.context import AuthContext
from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext


def _context(
    *,
    permissions: tuple[str, ...] = (CALCULATOR_PERMISSION,),
) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-1",
            roles=("analyst",),
            department="delivery",
            permissions=permissions,
        ),
    )


def _registry(
    *,
    audit: InMemoryAuditPort | None = None,
    rate_limit: ToolRateLimit | None = None,
) -> ToolRegistry:
    registry = ToolRegistry(
        audit=audit or InMemoryAuditPort(),
        rate_limiter=InMemoryToolRateLimiter(clock=lambda: 100.0),
        perf_counter=lambda: 10.0,
    )
    registry.register(
        build_calculator_tool(
            timeout_seconds=2.0,
            rate_limit=rate_limit or ToolRateLimit(max_calls=10, window_seconds=60.0),
        )
    )
    return registry


def _audit_metadata(audit: InMemoryAuditPort) -> Mapping[str, object]:
    assert len(audit.events) == 1
    return audit.events[0].metadata


def _output(result: ToolExecutionResult) -> dict[str, Any]:
    assert result.output is not None
    return result.output


@pytest.mark.asyncio
async def test_calculator_definition_registers_and_executes_safe_expression() -> None:
    audit = InMemoryAuditPort()
    registry = _registry(audit=audit)

    result = await registry.execute(
        name="calculator",
        arguments={"expression": "2 + 3 * (4 - 1)"},
        context=_context(),
    )

    assert result.status is ToolInvocationStatus.SUCCESS
    assert result.output == {
        "status": "success",
        "result": "11",
        "result_type": "integer",
        "operation_summary": "arithmetic_expression_evaluated",
        "error_code": None,
        "message": None,
    }
    assert audit.events[0].status is AuditStatus.SUCCESS
    assert _audit_metadata(audit)["tool_name"] == "calculator"
    assert _audit_metadata(audit)["permission"] == CALCULATOR_PERMISSION
    assert _audit_metadata(audit)["argument_keys"] == ["expression"]
    assert "2 + 3" not in str(audit.events[0].metadata)


@pytest.mark.parametrize(
    ("expression", "expected_result", "expected_type"),
    [
        ("7 // 2", "3", "integer"),
        ("7 / 2", "3.5", "decimal"),
        ("2 ** 8", "256", "integer"),
        ("-5 + +2", "-3", "integer"),
    ],
)
@pytest.mark.asyncio
async def test_calculator_supports_deterministic_arithmetic_subset(
    expression: str,
    expected_result: str,
    expected_type: str,
) -> None:
    registry = _registry()

    result = await registry.execute(
        name="calculator",
        arguments={"expression": expression},
        context=_context(),
    )
    output = _output(result)

    assert output["status"] == "success"
    assert output["result"] == expected_result
    assert output["result_type"] == expected_type


@pytest.mark.parametrize(
    ("expression", "error_code"),
    [
        ("", TOOL_INPUT_VALIDATION_FAILED),
        ("x" * 257, TOOL_INPUT_VALIDATION_FAILED),
    ],
)
@pytest.mark.asyncio
async def test_calculator_schema_rejects_empty_or_overlong_expression(
    expression: str,
    error_code: str,
) -> None:
    audit = InMemoryAuditPort()
    registry = _registry(audit=audit)

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="calculator",
            arguments={"expression": expression},
            context=_context(),
        )

    assert exc_info.value.code == error_code
    fields = exc_info.value.details["error_fields"]
    assert isinstance(fields, Sequence)
    assert "expression" in fields
    if expression:
        assert expression not in str(audit.events[0].metadata)


@pytest.mark.parametrize(
    ("expression", "error_code"),
    [
        ("2 +", "CALCULATOR_INVALID_EXPRESSION"),
        ("__import__('os').system('dir')", "CALCULATOR_UNSUPPORTED_EXPRESSION"),
        ("open('secret.txt')", "CALCULATOR_UNSUPPORTED_EXPRESSION"),
        ("[1, 2, 3]", "CALCULATOR_UNSUPPORTED_EXPRESSION"),
        ("1 / 0", "CALCULATOR_DIVISION_BY_ZERO"),
        ("1+" * 60 + "1", "CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED"),
        ("9 ** 99", "CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED"),
        ("10 ** 13", "CALCULATOR_RESULT_OUT_OF_RANGE"),
    ],
)
@pytest.mark.asyncio
async def test_calculator_returns_structured_errors_without_echoing_expression(
    expression: str,
    error_code: str,
) -> None:
    registry = _registry()

    result = await registry.execute(
        name="calculator",
        arguments={"expression": expression},
        context=_context(),
    )
    output = _output(result)

    assert output["status"] == "error"
    assert output["result"] is None
    assert output["result_type"] is None
    assert output["operation_summary"] == "arithmetic_expression_rejected"
    assert output["error_code"] == error_code
    assert expression not in str(output)


@pytest.mark.asyncio
async def test_calculator_permission_denied_prevents_handler_execution() -> None:
    audit = InMemoryAuditPort()
    registry = _registry(audit=audit)

    with pytest.raises(AgentToolError) as exc_info:
        await registry.execute(
            name="calculator",
            arguments={"expression": "2 + 2"},
            context=_context(permissions=()),
        )

    assert exc_info.value.code == TOOL_PERMISSION_DENIED
    assert audit.events[0].status is AuditStatus.DENIED
    assert "2 + 2" not in str(audit.events[0].metadata)


def test_calculator_output_schema_rejects_extra_fields() -> None:
    with pytest.raises(ValueError):
        CalculatorOutput.model_validate(
            {
                "status": "success",
                "result": "4",
                "result_type": "integer",
                "operation_summary": "arithmetic_expression_evaluated",
                "raw_expression": "2 + 2",
            }
        )
