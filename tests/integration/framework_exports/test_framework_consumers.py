"""Offline consumers compose the actual installed LangChain/LangGraph APIs."""

from __future__ import annotations

import json
from typing import Any, TypedDict

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda, RunnableSequence
from langsmith import tracing_context

from packages.agent.adapters.langchain import export_registry_tools
from packages.agent.dto import ToolRateLimit
from packages.agent.registry import ToolRegistry
from packages.agent.tools.rag_search import RAG_SEARCH_PERMISSION, build_rag_search_tool
from packages.rag.adapters.langchain import AegisQueryRunnable
from packages.rag.citation_identity import citation_id
from tests.framework_imports import require_langgraph_prebuilt
from tests.unit.framework_exports.test_langchain_exports import calculator, context, retriever
from tests.unit.rag.test_query_service import _service as query_service


async def test_langchain_retrieval_sequence_preserves_source_identity() -> None:
    adapter, source, audit = retriever()
    chain = (
        RunnableLambda(lambda payload: payload["question"])
        | adapter
        | RunnableLambda(
            lambda documents: {
                "excerpts": [doc.page_content for doc in documents],
                "citations": [doc.metadata["citation_id"] for doc in documents],
            }
        )
    )
    assert isinstance(chain, RunnableSequence)
    # The caller owns tracing of the enclosing chain's own inputs and outputs.
    with tracing_context(enabled=False):
        result = await chain.ainvoke({"question": "authorized question"}, {"callbacks": []})
    assert result["citations"] == [citation_id(source.candidates[0])]
    assert len(result["excerpts"][0]) == 1200
    assert audit.events[-1].action == "retrieval.retrieve"


async def test_langchain_query_sequence_and_async_batch_keep_native_result() -> None:
    adapter = AegisQueryRunnable(service=query_service(), context=context())
    chain = adapter | RunnableLambda(
        lambda response: {"answer": response.answer, "citations": response.citations}
    )
    with tracing_context(enabled=False):
        results = await chain.abatch(["first question", "second question"], {"callbacks": []})
    assert len(results) == 2
    assert results[0]["answer"] == results[1]["answer"]
    assert results[0]["citations"][0].citation_id.startswith("cite-")


async def test_langgraph_toolnode_executes_exported_registry_tool() -> None:
    require_langgraph_prebuilt()
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    tool, audit, recorder = await calculator()
    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode([tool], handle_tool_errors=False))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=False)
    with tracing_context(enabled=False):
        result = await graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "calculator", "id": "call-42", "args": {"expression": "7*6"}}
                        ],
                    )
                ]
            },
            {"callbacks": [], "recursion_limit": 4},
        )
    message = result["messages"][-1]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-42"
    assert json.loads(message.content)["output"]["result"] == "42"
    assert message.artifact["tool_name"] == "calculator"
    assert recorder.records[-1].agent_run_id == "run-1"
    assert audit.events[-1].status.value == "success"


async def test_langgraph_query_consumer_preserves_citations_and_authorized_identity() -> None:
    pytest.importorskip("langgraph.graph")
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict, total=False):
        question: str
        result: dict[str, Any]

    adapter = AegisQueryRunnable(service=query_service(), context=context())

    async def answer(state: State) -> State:
        result = await adapter.ainvoke(state["question"])
        return {"result": result.model_dump(mode="json")}

    builder = StateGraph(State)
    builder.add_node("query", answer)
    builder.add_edge(START, "query")
    builder.add_edge("query", END)
    with tracing_context(enabled=False):
        result = await builder.compile(checkpointer=False).ainvoke(
            {"question": "policy question"}, {"callbacks": [], "recursion_limit": 4}
        )
    response = result["result"]
    assert response["tenant_id"] == "tenant-1"
    assert response["citations"][0]["citation_id"].startswith("cite-")
    assert response["citations"][0]["version_id"] == "v1"


async def test_rag_search_tool_export_preserves_evidence_and_audit_in_real_chain() -> None:
    retrieval, source, audit = retriever()
    registry = ToolRegistry(audit=audit)
    registry.register(
        build_rag_search_tool(
            retrieval_app=retrieval._service,
            timeout_seconds=1,
            rate_limit=ToolRateLimit(max_calls=5, window_seconds=60),
        )
    )
    (tool,) = await export_registry_tools(
        registry=registry,
        context=context(permissions=(RAG_SEARCH_PERMISSION, "retrieval:query", "document:read")),
        tool_names=("rag_search",),
        agent_run_id="run-rag",
    )
    chain = RunnableLambda(lambda question: {"query": question}) | tool
    with tracing_context(enabled=False):
        result = await chain.ainvoke("policy question", {"callbacks": []})
    evidence = result["output"]["results"][0]
    for field in ("document_id", "version_id", "chunk_id"):
        assert evidence[field] == getattr(source.candidates[0], field)
    assert evidence["content_excerpt"]
    assert "prompt_context" not in result
    assert {event.action for event in audit.events} == {"retrieval.retrieve", "agent.tool.execute"}
