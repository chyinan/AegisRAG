"""Contract tests use real optional framework types and offline application ports."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError

pytest.importorskip("langchain_core")

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables.config import set_config_context, var_child_runnable_config
from langchain_core.tools import StructuredTool
from langchain_core.tracers.context import collect_runs, tracing_v2_enabled
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import get_tracing_context

from packages.agent.adapters.langchain import AegisStructuredTool, export_registry_tools
from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.agent.exceptions import (
    TOOL_HANDLER_FAILED,
    TOOL_INPUT_VALIDATION_FAILED,
    TOOL_OUTPUT_VALIDATION_FAILED,
    TOOL_PERMISSION_DENIED,
    TOOL_RATE_LIMITED,
    TOOL_TIMEOUT,
    AgentToolError,
)
from packages.agent.registry import ToolRegistry
from packages.agent.tools.calculator import CALCULATOR_PERMISSION, build_calculator_tool
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.common.tenant_context import current_tenant_id, tenant_scope
from packages.integrations.langchain_boundary import FrameworkExportError, stream_export
from packages.rag.adapters.langchain import AegisQueryRunnable
from packages.rag.citation_identity import citation_id
from packages.rag.dto import QueryCommand, QueryResponse
from packages.rag.exceptions import RagQueryError
from packages.rag.query import RagQueryApplicationService
from packages.rag.streaming import RagStreamEvent, token_event
from packages.retrieval.adapters.langchain import AegisRetriever
from packages.retrieval.application import RetrieveApplicationService
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import RetrievalError
from packages.retrieval.service import RetrievalService
from tests.unit.agent.test_tool_registry import DemoInput, DemoOutput, FakeToolCallRecorder
from tests.unit.rag.test_query_service import _service as query_service
from tests.unit.retrieval.test_retrieve_application import FakeRetrievalLogPort
from tests.unit.retrieval.test_retrieve_application import _candidate as retrieval_candidate


def context(*, permissions: tuple[str, ...] | None = None) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            tenant_id="tenant-1",
            user_id="user-1",
            permissions=permissions
            if permissions is not None
            else ("document:read", "retrieval:query", CALCULATOR_PERMISSION, "agent:tool:demo"),
        ),
    )


class Candidates:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self.candidates = candidates
        self.filters: list[RetrievalFilterSet] = []

    async def retrieve(
        self, *, request: RetrievalRequest, filters: RetrievalFilterSet
    ) -> list[RetrievalCandidate]:
        self.filters.append(filters)
        return self.candidates


def retriever(
    *, candidates: list[RetrievalCandidate] | None = None
) -> tuple[AegisRetriever, Candidates, InMemoryAuditPort]:
    source = Candidates(
        candidates
        if candidates is not None
        else [retrieval_candidate("chunk-1", 0.9).model_copy(update={"content": "evidence " * 200})]
    )
    audit = InMemoryAuditPort()
    app = RetrieveApplicationService(
        retrieval_service=RetrievalService(retriever=source),
        retrieval_log=FakeRetrievalLogPort(),
        audit=audit,
    )
    return AegisRetriever(service=app, context=context()), source, audit


async def calculator(
    *, permissions: tuple[str, ...] | None = None, max_calls: int = 10
) -> tuple[AegisStructuredTool, InMemoryAuditPort, FakeToolCallRecorder]:
    audit, recorder = InMemoryAuditPort(), FakeToolCallRecorder()
    registry = ToolRegistry(audit=audit, tool_call_recorder=recorder)
    registry.register(
        build_calculator_tool(
            timeout_seconds=1, rate_limit=ToolRateLimit(max_calls=max_calls, window_seconds=60)
        )
    )
    tools = await export_registry_tools(
        registry=registry,
        context=context(permissions=permissions),
        tool_names=("calculator",),
        agent_run_id="run-1",
    )
    return tools[0], audit, recorder


async def test_retriever_preserves_authorized_excerpt_and_canonical_metadata() -> None:
    adapter, source, audit = retriever()
    assert isinstance(adapter, BaseRetriever)
    docs = await adapter.ainvoke("private query", config={"configurable": {"tenant_id": "evil"}})
    assert len(docs) == 1
    assert len(docs[0].page_content) == 1200
    assert docs[0].id == citation_id(source.candidates[0])
    assert docs[0].metadata["citation_id"] == docs[0].id
    assert docs[0].metadata["content_scope"] == "authorized_excerpt"
    for field in (
        "document_id",
        "version_id",
        "chunk_id",
        "tenant_id",
        "acl",
        "source_ref",
        "source_display_name",
        "page_start",
        "page_end",
        "title_path",
        "retrieval_method",
        "score",
        "request_id",
        "trace_id",
    ):
        assert field in docs[0].metadata
    assert "secret-token" not in json.dumps(docs[0].metadata)
    assert "chunk body" not in json.dumps(docs[0].metadata)
    assert source.filters[0].tenant_id == "tenant-1"
    assert audit.events[-1].action == "retrieval.retrieve"
    assert "private query" not in str(audit.events)
    assert "tenant-1" not in adapter.model_dump_json()


async def test_retriever_reuses_real_service_acl_and_rejects_wrong_tenant() -> None:
    private = retrieval_candidate("private", 0.9).model_copy(
        update={"acl": {"visibility": "private", "allowed_users": ["someone-else"]}}
    )
    adapter, _, _ = retriever(candidates=[private])
    assert await adapter.ainvoke("query") == []
    adapter, _, audit = retriever(candidates=[private.model_copy(update={"tenant_id": "other"})])
    with pytest.raises(RetrievalError) as raised:
        await adapter.ainvoke("query")
    assert raised.value.code == "RETRIEVAL_BACKEND_FAILED"
    assert audit.events[-1].status.value == "failure"


async def test_retriever_rejects_invalid_policy_and_input() -> None:
    adapter, _, _ = retriever()
    with pytest.raises(ValidationError):
        await adapter.ainvoke(" ")
    with pytest.raises(ValidationError):
        AegisRetriever(service=adapter._service, context=context(), top_k=101)


async def test_query_preserves_response_and_full_native_event_contract() -> None:
    audit = InMemoryAuditPort()
    app = query_service(audit=audit)
    adapter = AegisQueryRunnable(service=app, context=context())
    response = await adapter.ainvoke({"query": "private question", "top_k": 2})
    assert isinstance(response, QueryResponse)
    assert response.citations[0].citation_id == citation_id(response.citations[0])
    events = [event async for event in adapter.astream(QueryCommand(query="question"))]
    assert all(isinstance(event, RagStreamEvent) for event in events)
    assert {event.event for event in events} == {"token", "citation", "final"}
    assert events[-1].payload.citations[0].citation_id == response.citations[0].citation_id
    assert events[-1].payload.tenant_id == "tenant-1"
    assert all(event.payload.request_id == "req-1" for event in events)
    assert {event.action for event in audit.events} == {"rag.query", "rag.query.stream"}


async def test_query_service_denial_and_stream_errors_are_not_rewritten() -> None:
    adapter = AegisQueryRunnable(service=query_service(), context=context(permissions=()))
    with pytest.raises(RagQueryError) as raised:
        await adapter.ainvoke("query")
    assert raised.value.code == "RAG_QUERY_FORBIDDEN"
    events = [event async for event in adapter.astream("query")]
    assert [event.event for event in events] == ["error", "final"]
    assert events[0].payload.code == "RAG_QUERY_FORBIDDEN"
    assert events[-1].payload.status == "error"


async def test_query_rejects_identity_and_service_overrides_in_input() -> None:
    adapter = AegisQueryRunnable(service=query_service(), context=context())
    for field in ("tenant_id", "context", "callbacks", "memory_context", "service"):
        with pytest.raises(ValueError, match="only QueryCommand fields"):
            await adapter.ainvoke({"query": "hello", field: "untrusted"})


async def test_structured_tool_schema_and_tool_message_keep_registry_envelope() -> None:
    tool, audit, recorder = await calculator()
    assert isinstance(tool, StructuredTool)
    assert tool.func is None and tool.coroutine is None
    assert tool.args_schema.model_json_schema()["properties"]["expression"]["type"] == "string"
    result = await tool.ainvoke({"expression": "6 * 7"})
    assert result["output"]["result"] == "42"
    assert result["status"] == "success"
    assert "prompt_context" not in result
    assert recorder.records[-1].agent_run_id == "run-1"
    assert audit.events[-1].status.value == "success"
    message = await tool.ainvoke(
        {"type": "tool_call", "name": "calculator", "id": "call-1", "args": {"expression": "1/0"}}
    )
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-1"
    assert message.status == "error"
    assert json.loads(message.content) == message.artifact
    assert message.artifact["output"]["error_code"] == "CALCULATOR_DIVISION_BY_ZERO"
    assert "tenant-1" not in tool.model_dump_json()
    with pytest.raises(ValidationError):
        tool.func = lambda: "bypass"


async def test_structured_tool_serialization_keeps_schema_class_and_json_schema() -> None:
    tool, _, _ = await calculator()
    schema = tool.args_schema
    assert isinstance(schema, type) and issubclass(schema, BaseModel)
    expected = schema.model_json_schema()

    assert tool.model_dump()["args_schema"] is schema
    assert tool.model_dump(mode="json")["args_schema"] == expected
    serialized = json.loads(tool.model_dump_json())
    assert serialized["args_schema"] == expected
    assert serialized["name"] == "calculator"
    assert serialized["func"] is None and serialized["coroutine"] is None
    assert tool.get_input_schema() is schema
    assert tool.args == expected["properties"]
    assert tool.args_schema is schema
    result = await tool.ainvoke({"expression": "6 * 7"})
    assert result["output"]["result"] == "42"


async def test_structured_tool_serialization_preserves_inherited_nested_schema() -> None:
    class ExtendedInput(DemoInput):
        details: DemoOutput
        count: int = 1

    async def handler(payload: ExtendedInput, context: AuthenticatedRequestContext) -> DemoOutput:
        return DemoOutput(answer=payload.details.answer * payload.count)

    registry = ToolRegistry(audit=InMemoryAuditPort())
    registry.register(
        ToolDefinition(
            name="demo",
            description="Nested schema serialization",
            input_schema=ExtendedInput,
            output_schema=DemoOutput,
            permission="agent:tool:demo",
            timeout_seconds=1,
            rate_limit=ToolRateLimit(max_calls=10, window_seconds=60),
            handler=handler,
        )
    )
    (tool,) = await export_registry_tools(
        registry=registry, context=context(), tool_names=("demo",), agent_run_id="run-1"
    )
    expected = ExtendedInput.model_json_schema()
    assert "$defs" in expected
    assert tool.model_dump()["args_schema"] is ExtendedInput
    assert tool.model_dump(mode="json")["args_schema"] == expected
    assert json.loads(tool.model_dump_json())["args_schema"] == expected
    assert tool.get_input_schema() is ExtendedInput
    result = await tool.ainvoke({"query": "hello", "details": {"answer": "ok"}})
    assert result["output"] == {"answer": "ok"}


@pytest.mark.parametrize("route", ["ainvoke", "arun", "_arun"])
async def test_all_async_tool_entrypoints_preserve_registry_validation(route: str) -> None:
    tool, audit, recorder = await calculator()
    with pytest.raises(AgentToolError) as raised:
        if route == "_arun":
            await tool._arun(expression="2+2", extra="not allowed")
        else:
            await getattr(tool, route)({"expression": "2+2", "extra": "not allowed"})
    assert raised.value.code == TOOL_INPUT_VALIDATION_FAILED
    assert audit.events[-1].error_code == TOOL_INPUT_VALIDATION_FAILED
    assert recorder.records[-1].error_code == TOOL_INPUT_VALIDATION_FAILED


async def test_tool_permission_and_rate_limit_remain_registry_owned() -> None:
    tool, audit, recorder = await calculator(permissions=())
    with pytest.raises(AgentToolError) as raised:
        await tool.ainvoke({"expression": "2+2"}, config={"configurable": {"permissions": ["*"]}})
    assert raised.value.code == TOOL_PERMISSION_DENIED
    assert audit.events[-1].status.value == "denied"
    assert recorder.records[-1].status == "denied"
    tool, _, _ = await calculator(max_calls=1)
    await tool.ainvoke({"expression": "2+2"})
    with pytest.raises(AgentToolError) as raised:
        await tool.ainvoke({"expression": "2+2"})
    assert raised.value.code == TOOL_RATE_LIMITED


@pytest.mark.parametrize(
    "failure,code",
    [
        ("timeout", TOOL_TIMEOUT),
        ("handler", TOOL_HANDLER_FAILED),
        ("output", TOOL_OUTPUT_VALIDATION_FAILED),
    ],
)
async def test_registry_timeouts_and_failures_survive_tool_export(failure: str, code: str) -> None:
    async def handler(payload: DemoInput, context: AuthenticatedRequestContext) -> object:
        if failure == "timeout":
            await asyncio.sleep(10)
        if failure == "handler":
            raise RuntimeError("password=never expose")
        return {"wrong": "output"}

    audit = InMemoryAuditPort()
    registry = ToolRegistry(audit=audit)
    registry.register(
        ToolDefinition(
            name="demo",
            description="Governed demo",
            input_schema=DemoInput,
            output_schema=DemoOutput,
            permission="agent:tool:demo",
            timeout_seconds=0.01,
            rate_limit=ToolRateLimit(max_calls=10, window_seconds=60),
            handler=handler,
        )
    )
    (tool,) = await export_registry_tools(
        registry=registry, context=context(), tool_names=("demo",), agent_run_id="run-1"
    )
    with pytest.raises(AgentToolError) as raised:
        await tool.ainvoke({"query": "private query"})
    assert raised.value.code == code
    assert "password=" not in str(raised.value)
    assert audit.events[-1].error_code == code


async def test_factory_requires_explicit_unique_names_and_run_attribution() -> None:
    registry = ToolRegistry(audit=InMemoryAuditPort())
    for names, run_id in [("calculator", "run"), (("x", "x"), "run"), (("x",), " ")]:
        with pytest.raises(ValueError):
            await export_registry_tools(
                registry=registry, context=context(), tool_names=names, agent_run_id=run_id
            )
    tool, _, _ = await calculator()
    with pytest.raises(ValueError, match="name"):
        await tool.ainvoke({"type": "tool_call", "name": "other", "id": "1", "args": {}})


@pytest.mark.parametrize("kind", ["retrieval", "query", "tool"])
async def test_explicit_and_ambient_callbacks_cannot_observe_export_calls(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    class Capture(BaseCallbackHandler):
        def on_retriever_start(self, *args: Any, **kwargs: Any) -> None:
            seen.append(args)

        def on_chain_start(self, *args: Any, **kwargs: Any) -> None:
            seen.append(args)

        def on_tool_start(self, *args: Any, **kwargs: Any) -> None:
            seen.append(args)

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: seen.append(run))
    if kind == "retrieval":
        adapter, _, _ = retriever()
        value = "private query"
    elif kind == "query":
        adapter = AegisQueryRunnable(service=query_service(), context=context())
        value = "private query"
    else:
        adapter, _, _ = await calculator()
        value = {"expression": "1+1"}
    with collect_runs() as collector, tracing_v2_enabled(client=MagicMock()):
        with set_config_context({"callbacks": [Capture()]}) as inherited:
            await asyncio.create_task(
                adapter.ainvoke(value, config={"callbacks": [Capture()]}), context=inherited
            )
        assert collector.traced_runs == []
    assert seen == []


async def test_isolation_preserves_application_scope_without_arbitrary_context() -> None:
    secret = ContextVar("secret-test-context", default=None)
    observed: list[Any] = []

    class Probe:
        async def query(self, **kwargs: Any) -> QueryResponse:
            observed.append(
                (
                    current_tenant_id(),
                    secret.get(),
                    var_child_runnable_config.get(),
                    get_tracing_context()["enabled"],
                    kwargs["context"],
                )
            )
            return QueryResponse(
                request_id="req-1",
                trace_id="trace-1",
                tenant_id="tenant-1",
                user_id="user-1",
                answer="ok",
            )

    adapter = AegisQueryRunnable(
        service=cast(RagQueryApplicationService, Probe()), context=context()
    )
    token = secret.set("private inherited value")
    try:
        await adapter.ainvoke("hello")
        assert current_tenant_id() is None
        assert secret.get() == "private inherited value"
    finally:
        secret.reset(token)
    assert observed == [("tenant-1", None, None, False, context())]
    with tenant_scope("other"):
        with pytest.raises(FrameworkExportError, match="TENANT_MISMATCH"):
            await adapter.ainvoke("hello")
    assert len(observed) == 1


@pytest.mark.parametrize("kind", ["retrieval", "query", "tool"])
async def test_debug_logging_fails_closed_and_sync_usage_is_explicit(
    kind: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import langchain_core.globals as lc_globals

    if kind == "retrieval":
        adapter, _, _ = retriever()
        value = "private input"
    elif kind == "query":
        adapter = AegisQueryRunnable(service=query_service(), context=context())
        value = "private input"
    else:
        adapter, _, _ = await calculator()
        value = {"expression": "1+1"}
    with pytest.raises(NotImplementedError, match="async-only"):
        adapter.invoke(value)
    monkeypatch.setattr(lc_globals, "_debug", True)
    with pytest.raises(FrameworkExportError, match="DEBUG_DISABLED"):
        await adapter.ainvoke(value)
    assert "private input" not in capsys.readouterr().out


class BlockingQuery:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def query(self, **kwargs: Any) -> QueryResponse:
        self.started.set()
        try:
            await asyncio.sleep(60)
        finally:
            self.closed.set()
        raise AssertionError("unreachable")

    async def stream_query(self, **kwargs: Any) -> AsyncIterator[RagStreamEvent]:
        try:
            yield token_event(request_id="req-1", trace_id="trace-1", index=0, delta="one")
            self.started.set()
            await asyncio.sleep(60)
        finally:
            self.closed.set()


async def test_query_cancellation_reaches_service_without_orphan_task() -> None:
    probe = BlockingQuery()
    adapter = AegisQueryRunnable(service=cast(RagQueryApplicationService, probe), context=context())
    task = asyncio.create_task(adapter.ainvoke("private question"))
    await asyncio.wait_for(probe.started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert probe.closed.is_set()


@pytest.mark.parametrize("cancel", [False, True])
async def test_query_stream_close_and_cancellation_close_native_generator(cancel: bool) -> None:
    probe = BlockingQuery()
    adapter = AegisQueryRunnable(service=cast(RagQueryApplicationService, probe), context=context())
    stream = adapter.astream("question")
    assert (await anext(stream)).payload.delta == "one"
    if cancel:
        task = asyncio.create_task(anext(stream))
        await asyncio.wait_for(probe.started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await stream.aclose()
    assert probe.closed.is_set()


async def test_query_domain_error_identity_survives_boundary() -> None:
    error = RagQueryError(code="EXPECTED_ERROR", message="Safe application error", status_code=503)

    class Broken:
        async def query(self, **kwargs: Any) -> QueryResponse:
            raise error

    adapter = AegisQueryRunnable(
        service=cast(RagQueryApplicationService, Broken()), context=context()
    )
    with pytest.raises(RagQueryError) as raised:
        await adapter.ainvoke("question")
    assert raised.value is error


async def test_query_batch_serializes_loop_bound_service_resources() -> None:
    active = 0
    max_active = 0

    class Probe:
        async def query(self, **kwargs: Any) -> QueryResponse:
            nonlocal active, max_active
            active += 1
            max_active = max(active, max_active)
            try:
                await asyncio.sleep(0.01)
                return QueryResponse(
                    request_id="req-1",
                    trace_id="trace-1",
                    tenant_id="tenant-1",
                    user_id="user-1",
                    answer=kwargs["command"].query,
                )
            finally:
                active -= 1

    adapter = AegisQueryRunnable(
        service=cast(RagQueryApplicationService, Probe()), context=context()
    )
    results = await adapter.abatch(["one", "two", "three"])
    assert [result.answer for result in results] == ["one", "two", "three"]
    assert max_active == 1


async def test_exported_tools_share_serial_execution_for_one_registry_session() -> None:
    active = 0
    max_active = 0

    async def handler(payload: DemoInput, context: AuthenticatedRequestContext) -> DemoOutput:
        nonlocal active, max_active
        active += 1
        max_active = max(active, max_active)
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
                description="Guarded tool",
                input_schema=DemoInput,
                output_schema=DemoOutput,
                permission="agent:tool:demo",
                timeout_seconds=1,
                rate_limit=ToolRateLimit(max_calls=10, window_seconds=60),
                handler=handler,
            )
        )
    exports = await export_registry_tools(
        registry=registry, context=context(), tool_names=("first", "second"), agent_run_id="run-1"
    )
    results = await asyncio.gather(*(tool.ainvoke({"query": tool.name}) for tool in exports))
    assert [result["output"]["answer"] for result in results] == ["first", "second"]
    assert max_active == 1


@pytest.mark.parametrize("kind", ["retrieval", "tool"])
async def test_cancellation_reaches_retrieval_and_registry_handlers(kind: str) -> None:
    started, closed = asyncio.Event(), asyncio.Event()

    async def block() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            closed.set()

    if kind == "retrieval":

        class BlockingRetrieval:
            async def retrieve(self, **kwargs: Any) -> Any:
                await block()

        adapter = AegisRetriever(
            service=cast(RetrieveApplicationService, BlockingRetrieval()), context=context()
        )
        value = "question"
    else:

        async def handler(payload: DemoInput, context: AuthenticatedRequestContext) -> object:
            await block()
            return DemoOutput(answer="unreachable")

        registry = ToolRegistry(audit=InMemoryAuditPort())
        registry.register(
            ToolDefinition(
                name="blocking",
                description="Bounded cancellation test",
                input_schema=DemoInput,
                output_schema=DemoOutput,
                permission="agent:tool:demo",
                timeout_seconds=120,
                rate_limit=ToolRateLimit(max_calls=10, window_seconds=60),
                handler=handler,
            )
        )
        (adapter,) = await export_registry_tools(
            registry=registry, context=context(), tool_names=("blocking",), agent_run_id="run-1"
        )
        value = {"query": "question"}
    task = asyncio.create_task(adapter.ainvoke(value))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_stream_keeps_isolation_across_iterations_and_consumer_close() -> None:
    observed: list[Any] = []
    secret = ContextVar("stream-secret", default=None)

    class StreamProbe:
        async def stream_query(self, **kwargs: Any) -> AsyncIterator[RagStreamEvent]:
            try:
                for index in range(2):
                    observed.append(
                        (
                            get_tracing_context()["enabled"],
                            secret.get(),
                            current_tenant_id(),
                            var_child_runnable_config.get(),
                        )
                    )
                    yield token_event(
                        request_id="req-1", trace_id="trace-1", index=index, delta="private token"
                    )
            finally:
                observed.append(("closed", get_tracing_context()["enabled"], secret.get()))

    adapter = AegisQueryRunnable(
        service=cast(RagQueryApplicationService, StreamProbe()), context=context()
    )
    token = secret.set("do not inherit")
    try:
        with collect_runs() as collector, tracing_v2_enabled(client=MagicMock()):
            stream = adapter.astream("private query")
            assert (await anext(stream)).payload.delta == "private token"
            assert (await anext(stream)).payload.delta == "private token"
            await stream.aclose()
            assert collector.traced_runs == []
    finally:
        secret.reset(token)
    assert observed == [
        (False, None, "tenant-1", None),
        (False, None, "tenant-1", None),
        ("closed", False, None),
    ]


async def test_debug_enabled_midstream_still_closes_application_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import langchain_core.globals as lc_globals

    probe = BlockingQuery()
    adapter = AegisQueryRunnable(service=cast(RagQueryApplicationService, probe), context=context())
    stream = adapter.astream("private query")
    await anext(stream)
    monkeypatch.setattr(lc_globals, "_debug", True)
    with pytest.raises(FrameworkExportError, match="DEBUG_DISABLED"):
        await anext(stream)
    assert probe.closed.is_set()


async def test_stream_timeout_remains_owned_by_one_producer_task() -> None:
    closed = asyncio.Event()
    tasks: list[asyncio.Task[Any] | None] = []

    async def source() -> AsyncIterator[int]:
        try:
            tasks.append(asyncio.current_task())
            async with asyncio.timeout(0.02):
                yield 1
                tasks.append(asyncio.current_task())
                await asyncio.Event().wait()
        finally:
            tasks.append(asyncio.current_task())
            closed.set()

    exported = stream_export(source, request_context=context())
    assert await anext(exported) == 1
    pending = asyncio.create_task(anext(exported))
    done, _ = await asyncio.wait({pending}, timeout=0.2)
    if not done:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        pytest.fail("source deadline did not terminate its own producer")
    with pytest.raises(TimeoutError):
        await pending
    assert closed.is_set()
    assert len(tasks) == 3 and tasks[0] is tasks[1] is tasks[2]


async def test_stream_does_not_prefetch_or_complete_before_consumer_pulls() -> None:
    observed: list[tuple[str, asyncio.Task[Any] | None]] = []

    async def source() -> AsyncIterator[int]:
        try:
            observed.append(("first", asyncio.current_task()))
            yield 1
            observed.append(("second", asyncio.current_task()))
            yield 2
            observed.append(("completed", asyncio.current_task()))
        finally:
            observed.append(("closed", asyncio.current_task()))

    exported = stream_export(source, request_context=context())
    assert await anext(exported) == 1
    await asyncio.sleep(0.01)
    assert [stage for stage, _ in observed] == ["first"]
    await exported.aclose()
    assert [stage for stage, _ in observed] == ["first", "closed"]
    assert observed[0][1] is observed[1][1]


async def test_actual_query_export_owns_nested_provider_stream_lifetime() -> None:
    from packages.llm.adapters.fake import FakeLLMProvider
    from packages.llm.dto import GenerateChunk, GenerateRequest

    closed = asyncio.Event()
    held_references: list[AsyncIterator[GenerateChunk]] = []

    class Provider(FakeLLMProvider):
        def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
            iterator = self._tokens(request)
            held_references.append(iterator)
            return iterator

        async def _tokens(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
            try:
                yield GenerateChunk(delta="partial", index=0)
                await asyncio.Event().wait()
            finally:
                closed.set()

    audit = InMemoryAuditPort()
    adapter = AegisQueryRunnable(
        service=query_service(provider=Provider(), audit=audit), context=context()
    )
    exported = adapter.astream("question")
    while (await anext(exported)).event != "token":
        pass
    await exported.aclose()
    assert held_references and closed.is_set()
    assert len(audit.events) == 1
    assert audit.events[0].error_code == "RAG_QUERY_CLIENT_DISCONNECTED"


async def test_producer_failure_while_consumer_paused_remains_observable() -> None:
    closed = asyncio.Event()

    async def source() -> AsyncIterator[int]:
        try:
            async with asyncio.timeout(0.01):
                yield 1
                yield 2
        finally:
            closed.set()

    exported = stream_export(source, request_context=context())
    assert await anext(exported) == 1
    await asyncio.wait_for(closed.wait(), 0.2)
    next_item = asyncio.create_task(anext(exported))
    done, _ = await asyncio.wait({next_item}, timeout=0.2)
    if not done:
        next_item.cancel()
        with pytest.raises(asyncio.CancelledError):
            await next_item
        pytest.fail("producer termination was lost while the consumer was paused")
    with pytest.raises((asyncio.CancelledError, TimeoutError)):
        await next_item
    await exported.aclose()
