import math
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.common.context import AuthenticatedRequestContext


class DemoInput(BaseModel):
    query: str


class DemoOutput(BaseModel):
    answer: str


async def demo_handler(payload: DemoInput, context: AuthenticatedRequestContext) -> DemoOutput:
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
