"""Independent offline regressions for controlled exports and native decisions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, ToolMessage
from langsmith import tracing_context

from packages.agent.adapters.langchain import export_registry_tools
from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.agent.registry import ToolRegistry
from packages.common.audit import InMemoryAuditPort
from packages.integrations.langchain_boundary import stream_export
from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.dto import GenerateChunk, GenerateRequest
from packages.llm.exceptions import LLMProviderError
from packages.rag.adapters.langchain import AegisQueryRunnable
from tests.framework_imports import require_langgraph_prebuilt
from tests.unit.agent.test_tool_registry import DemoInput, DemoOutput
from tests.unit.framework_exports.test_langchain_exports import context
from tests.unit.llm.test_langchain_provider import _provider
from tests.unit.llm.test_structured_decisions import _call, _decision_body, _decision_request
from tests.unit.rag.test_query_service import _service


async def test_export_stream_preserves_task_affine_timeout_after_first_yield() -> None:
    """An ordinary cooperative generator's own deadline must still cancel its work."""
    closed = asyncio.Event()

    async def timed_stream() -> AsyncIterator[int]:
        try:
            async with asyncio.timeout(0.02):
                yield 1
                await asyncio.Event().wait()
        finally:
            closed.set()

    exported = stream_export(timed_stream, request_context=context())
    assert await anext(exported) == 1
    next_item = asyncio.create_task(anext(exported))
    # Observe without cancelling it, so the caller cannot manufacture the expected timeout.
    done, _ = await asyncio.wait({next_item}, timeout=0.15)
    timed_out_itself = bool(done)
    if not done:
        next_item.cancel()
    try:
        await next_item
    except (TimeoutError, asyncio.CancelledError):
        pass
    await exported.aclose()
    assert closed.is_set()
    assert timed_out_itself, "export resumes a generator in a new Task, orphaning its own timeout"


async def test_export_stream_closes_actual_nested_provider_before_returning() -> None:
    """Closing the exported real service must release its actual provider stream."""
    closed = asyncio.Event()
    observed: list[AsyncIterator[GenerateChunk]] = []

    class ClosingProvider(FakeLLMProvider):
        def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
            iterator = self._stream(request)
            # Keep a reference to rule out incidental garbage collection as lifecycle ownership.
            observed.append(iterator)
            return iterator

        async def _stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
            try:
                yield GenerateChunk(delta="partial", index=0)
                await asyncio.Event().wait()
            finally:
                closed.set()

    adapter = AegisQueryRunnable(service=_service(provider=ClosingProvider()), context=context())
    exported = adapter.astream("question")
    while (await anext(exported)).event != "token":
        pass
    await exported.aclose()
    was_closed = closed.is_set()
    # The assertion checks deterministic ownership; this close avoids leaving test resources open.
    for iterator in observed:
        await iterator.aclose()
    assert was_closed, "provider remains open after AegisQueryRunnable.aclose returns"


async def test_real_toolnode_serializes_distinct_tools_on_same_registry() -> None:
    require_langgraph_prebuilt()
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    active = 0
    maximum = 0

    async def handler(payload: DemoInput, request_context: Any) -> DemoOutput:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.01)
            return DemoOutput(answer=payload.query)
        finally:
            active -= 1

    registry = ToolRegistry(audit=InMemoryAuditPort())
    for name in ("first", "second"):
        registry.register(
            ToolDefinition(
                name=name,
                description="Offline composition review",
                input_schema=DemoInput,
                output_schema=DemoOutput,
                permission="agent:tool:demo",
                timeout_seconds=1,
                rate_limit=ToolRateLimit(max_calls=10, window_seconds=60),
                handler=handler,
            )
        )
    tools = await export_registry_tools(
        registry=registry,
        context=context(),
        tool_names=("first", "second"),
        agent_run_id="run-review",
    )
    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode(list(tools), handle_tool_errors=False))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    with tracing_context(enabled=False):
        result = await builder.compile(checkpointer=False).ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": name, "id": name, "args": {"query": name}}
                            for name in ("first", "second")
                        ],
                    )
                ]
            }
        )
    assert maximum == 1
    messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert len(messages) == 2
    assert [message.artifact["output"]["answer"] for message in messages] == ["first", "second"]


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
async def test_native_transport_truncated_decisions_never_retry(finish_reason: str) -> None:
    count = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        body = _decision_body(_call())
        body["choices"][0]["finish_reason"] = finish_reason
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(LLMProviderError) as caught:
            await _provider(client).decide(_decision_request(retry_budget=3))
    assert caught.value.code == "LLM_PROVIDER_MALFORMED_RESPONSE"
    assert count == 1


@pytest.mark.parametrize(
    "arguments",
    [
        '{"expression":"2+2"} trailing',
        '{"expression":"2+2","expression":"3+3"}',
        '{"expression":"2+2","extra":{"value":1,"value":2}}',
    ],
)
async def test_native_transport_partial_or_duplicate_json_fails_closed(arguments: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_decision_body(_call(arguments=arguments)))
        )
    ) as client:
        with pytest.raises(LLMProviderError) as caught:
            await _provider(client).decide(_decision_request())
    assert caught.value.code == "LLM_PROVIDER_MALFORMED_RESPONSE"


async def test_export_stream_preserves_pull_semantics_and_task_affine_cleanup() -> None:
    stages: list[str] = []
    tasks: list[asyncio.Task[Any] | None] = []

    async def source() -> AsyncIterator[int]:
        tasks.append(asyncio.current_task())
        try:
            stages.append("first")
            yield 1
            stages.append("second")
            yield 2
            stages.append("finished")
        finally:
            stages.append("closed")
            tasks.append(asyncio.current_task())

    stream = stream_export(source, request_context=context())
    assert await anext(stream) == 1
    await asyncio.sleep(0.02)
    assert stages == ["first"], "export must not prefetch another event during consumer pause"
    await stream.aclose()
    assert stages == ["first", "closed"]
    assert tasks[0] is tasks[1], "generator cleanup must run in its owning task"


@pytest.mark.parametrize("route", ["registry", "structured_tool"])
async def test_approval_required_definition_cannot_dispatch_without_bound_grant(route: str) -> None:
    from packages.agent.exceptions import AgentToolError

    calls: list[str] = []

    async def handler(payload: DemoInput, request_context: Any) -> DemoOutput:
        calls.append(payload.query)
        return DemoOutput(answer=payload.query)

    registry = ToolRegistry(audit=InMemoryAuditPort())
    registry.register(
        ToolDefinition(
            name="review_approved_tool",
            description="Synthetic approval-required tool",
            input_schema=DemoInput,
            output_schema=DemoOutput,
            permission="agent:tool:demo",
            timeout_seconds=1,
            rate_limit=ToolRateLimit(max_calls=10, window_seconds=60),
            handler=handler,
            requires_approval=True,
        )
    )
    try:
        if route == "registry":
            await registry.execute(
                name="review_approved_tool",
                arguments={"query": "inert test"},
                context=context(),
                agent_run_id="run-review",
            )
        else:
            (tool,) = await export_registry_tools(
                registry=registry,
                context=context(),
                tool_names=("review_approved_tool",),
                agent_run_id="run-review",
            )
            await tool.ainvoke({"query": "inert test"})
    except AgentToolError:
        pass
    assert calls == [], "requires_approval must be enforced at the shared dispatch boundary"


@pytest.mark.parametrize("call_type", [None, "custom"])
async def test_native_transport_rejects_missing_or_non_function_call_type(
    call_type: str | None,
) -> None:
    call = _call()
    if call_type is None:
        call.pop("type")
    else:
        call["type"] = call_type
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_decision_body(call))
        )
    ) as client:
        with pytest.raises(LLMProviderError) as caught:
            await _provider(client).decide(_decision_request())
    assert caught.value.code == "LLM_PROVIDER_MALFORMED_RESPONSE"


async def test_native_transport_rejects_duplicate_final_answer_members() -> None:
    from packages.llm.decisions import FINAL_ANSWER_TOOL

    call = _call(FINAL_ANSWER_TOOL, '{"final_answer":"one","final_answer":"two"}')
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_decision_body(call))
        )
    ) as client:
        with pytest.raises(LLMProviderError) as caught:
            await _provider(client).decide(_decision_request())
    assert caught.value.code == "LLM_PROVIDER_MALFORMED_RESPONSE"


async def test_export_stream_timeout_during_consumer_pause_does_not_hang_next() -> None:
    closed = asyncio.Event()

    async def source() -> AsyncIterator[int]:
        try:
            async with asyncio.timeout(0.02):
                yield 1
                await asyncio.Event().wait()
        finally:
            closed.set()

    stream = stream_export(source, request_context=context())
    assert await anext(stream) == 1
    await asyncio.sleep(0.05)
    next_item = asyncio.create_task(anext(stream))
    done, _ = await asyncio.wait({next_item}, timeout=0.15)
    terminated_without_caller_cancel = bool(done)
    if not done:
        next_item.cancel()
    try:
        await next_item
    except (TimeoutError, asyncio.CancelledError, StopAsyncIteration):
        pass
    await stream.aclose()
    assert terminated_without_caller_cancel
    assert closed.is_set()


async def test_malformed_native_payload_does_not_escape_in_serializer_warnings() -> None:
    import warnings

    private_marker = "private_review_payload"
    call = _call()
    call["function"]["arguments"] = {private_marker: "never log this malformed value"}
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=_decision_body(call))
            )
        ) as client:
            with pytest.raises(LLMProviderError) as caught:
                await _provider(client).decide(_decision_request())
    assert caught.value.code == "LLM_PROVIDER_MALFORMED_RESPONSE"
    assert all(private_marker not in str(warning.message) for warning in captured)
