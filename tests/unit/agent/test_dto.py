import math
from datetime import datetime
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from packages.agent.dto import (
    AgentCitationRef,
    FinalAnswerValidationResult,
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


def test_agent_citation_ref_is_structured_and_rejects_unsafe_values() -> None:
    citation = AgentCitationRef(
        document_id="doc-1",
        version_id="ver-1",
        chunk_id="chunk-1",
        source="policy",
        page_start=1,
        page_end=2,
        tool_name="rag_search",
        observation_index=0,
    )

    assert citation.evidence_key == ("doc-1", "ver-1", "chunk-1", "policy", 1, 2)

    with pytest.raises(ValidationError):
        AgentCitationRef(
            document_id="",
            version_id="ver-1",
            chunk_id="chunk-1",
        )
    with pytest.raises(ValidationError):
        AgentCitationRef(
            document_id="doc-1",
            version_id="ver-1",
            chunk_id="chunk-1",
            source="C:\\secret\\policy.txt",
        )
    with pytest.raises(ValidationError):
        AgentCitationRef.model_validate(
            {
                **citation.model_dump(),
                "raw_chunk_text": "classified",
            }
        )


def test_final_answer_validation_result_metadata_is_safe() -> None:
    result = FinalAnswerValidationResult(
        status="valid",
        answer="safe answer",
        latency_ms=1.0,
        metadata={
            "validation_status": "valid",
            "citation_refs": [
                {
                    "document_id": "doc-1",
                    "version_id": "ver-1",
                    "chunk_id": "chunk-1",
                }
            ],
        },
    )

    assert result.status == "valid"

    with pytest.raises(ValidationError):
        FinalAnswerValidationResult(
            status="valid",
            answer="safe answer",
            latency_ms=1.0,
            metadata={"raw_tool_output": "classified"},
        )
