import math
from datetime import datetime
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from packages.agent.dto import (
    ToolCallCreate,
    ToolCallQuery,
    ToolCallRecord,
    ToolCallRecorderPort,
    ToolDefinition,
    ToolRateLimit,
)
from packages.common.context import AuthenticatedRequestContext


class DemoInput(BaseModel):
    query: str


class DemoOutput(BaseModel):
    answer: str


async def demo_handler(payload: DemoInput, context: AuthenticatedRequestContext) -> DemoOutput:
    _ = context
    return DemoOutput(answer=payload.query)


def sync_handler(payload: DemoInput, context: AuthenticatedRequestContext) -> DemoOutput:
    _ = context
    return DemoOutput(answer=payload.query)


def test_tool_definition_requires_structured_contract() -> None:
    definition = ToolDefinition(
        name="rag_search",
        description="Search governed RAG context.",
        input_schema=DemoInput,
        output_schema=DemoOutput,
        permission="agent:tool:rag_search",
        timeout_seconds=2.0,
        rate_limit=ToolRateLimit(max_calls=5, window_seconds=60.0),
        handler=demo_handler,
    )

    assert definition.name == "rag_search"
    assert definition.input_json_schema["title"] == "DemoInput"
    assert definition.output_json_schema["title"] == "DemoOutput"


@pytest.mark.parametrize(
    "name",
    ["", "RagSearch", "rag.search", "rag search", "../rag_search", "rag-search"],
)
def test_tool_definition_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(
            name=name,
            description="Search governed RAG context.",
            input_schema=DemoInput,
            output_schema=DemoOutput,
            permission="agent:tool:rag_search",
            timeout_seconds=2.0,
            rate_limit=ToolRateLimit(max_calls=5, window_seconds=60.0),
            handler=demo_handler,
        )


def test_tool_definition_rejects_unstructured_schema_and_handler_reference() -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(
            name="rag_search",
            description="Search governed RAG context.",
            input_schema=cast(Any, {"query": "string"}),
            output_schema=DemoOutput,
            permission="agent:tool:rag_search",
            timeout_seconds=2.0,
            rate_limit=ToolRateLimit(max_calls=5, window_seconds=60.0),
            handler=cast(Any, "packages.tools.rag_search"),
        )


def test_tool_definition_rejects_sync_handler() -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(
            name="rag_search",
            description="Search governed RAG context.",
            input_schema=DemoInput,
            output_schema=DemoOutput,
            permission="agent:tool:rag_search",
            timeout_seconds=2.0,
            rate_limit=ToolRateLimit(max_calls=5, window_seconds=60.0),
            handler=cast(Any, sync_handler),
        )


@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, math.inf, math.nan])
def test_tool_definition_requires_finite_positive_timeout(timeout_seconds: float) -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(
            name="rag_search",
            description="Search governed RAG context.",
            input_schema=DemoInput,
            output_schema=DemoOutput,
            permission="agent:tool:rag_search",
            timeout_seconds=timeout_seconds,
            rate_limit=ToolRateLimit(max_calls=5, window_seconds=60.0),
            handler=demo_handler,
        )


@pytest.mark.parametrize(
    ("max_calls", "window_seconds"),
    [(0, 60.0), (-1, 60.0), (1, 0.0), (1, -1.0), (1, math.inf), (1, math.nan)],
)
def test_rate_limit_requires_finite_positive_limits(
    max_calls: int,
    window_seconds: float,
) -> None:
    with pytest.raises(ValidationError):
        ToolRateLimit(max_calls=max_calls, window_seconds=window_seconds)


def test_tool_call_create_stores_safe_summaries_without_raw_payload_fields() -> None:
    record = ToolCallCreate(
        agent_run_id="run-1",
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        tool_name="rag_search",
        permission="agent:tool:rag_search",
        status="success",
        latency_ms=12.5,
        error_code=None,
        arguments_summary={"argument_keys": ["query"], "argument_count": 1},
        result_summary={"result_keys": ["citations"], "status": "success"},
    )

    assert record.status == "success"
    assert record.arguments_summary["argument_keys"] == ["query"]
    assert "raw" not in record.model_dump()

    with pytest.raises(ValidationError):
        ToolCallCreate.model_validate(
            {
                **record.model_dump(),
                "raw_arguments": {"query": "secret"},
            }
        )


def test_tool_call_create_rejects_unsafe_summary_payloads() -> None:
    base = ToolCallCreate(
        agent_run_id="run-1",
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        tool_name="rag_search",
        permission="agent:tool:rag_search",
        status="success",
        latency_ms=12.5,
        error_code=None,
        arguments_summary={"argument_keys": ["query"], "argument_count": 1},
        result_summary={"result_keys": ["citations"], "status": "success"},
    ).model_dump()

    unsafe_summaries: list[dict[str, object]] = [
        {"arguments_summary": {"query": "secret policy text"}},
        {"arguments_summary": {"argument_keys": ["query"], "raw_prompt": "ignore system"}},
        {"result_summary": {"file_path": "C:\\secret\\policy.txt"}},
        {"result_summary": {"result_keys": ["answer"], "raw_output": "classified"}},
        {"result_summary": {"provider_payload": {"token": "secret"}}},
        {"result_summary": {"status": "success", "excerpt": "x" * 201}},
    ]

    for update in unsafe_summaries:
        with pytest.raises(ValidationError):
            ToolCallCreate.model_validate({**base, **update})


def test_tool_call_record_and_query_define_tenant_scoped_contract() -> None:
    created_at = datetime.fromisoformat("2026-06-08T14:20:00+08:00")
    record = ToolCallRecord(
        id="tool-call-1",
        agent_run_id="run-1",
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        tool_name="calculator",
        permission="agent:tool:calculator",
        status="failure",
        latency_ms=1.0,
        error_code="TOOL_HANDLER_FAILED",
        arguments_summary={"argument_keys": ["expression"]},
        result_summary={"status": "failure", "error_code": "TOOL_HANDLER_FAILED"},
        created_at=created_at,
        updated_at=created_at,
    )
    query = ToolCallQuery(
        tenant_id="tenant-1",
        user_id="user-1",
        agent_run_id="run-1",
        tool_name="calculator",
        status="failure",
    )

    assert record.id == "tool-call-1"
    assert query.tenant_id == "tenant-1"
    assert query.user_id == "user-1"


def test_tool_call_recorder_port_is_storage_free_protocol() -> None:
    assert getattr(ToolCallRecorderPort, "_is_protocol", False) is True
