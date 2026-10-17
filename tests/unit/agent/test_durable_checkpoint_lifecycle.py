"""Checkpoint dependencies must settle even when their insertion never starts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from packages.agent.durable.service import _DurableExecution
from packages.common.errors import DomainError
from tests.framework_imports import require_langgraph


def execution(store: Any) -> _DurableExecution:
    return cast(
        _DurableExecution,
        SimpleNamespace(
            snapshot=SimpleNamespace(
                run_id="checkpoint-lifecycle",
                revision=1,
                status="running",
                config=SimpleNamespace(max_steps=3),
            ),
            token="test-lease",
            service=SimpleNamespace(_store=store, _clock=lambda: 100.0),
        ),
    )


async def test_abort_releases_missing_insert_dependency_and_rejects_new_work() -> None:
    require_langgraph()
    from langchain_core.runnables import RunnableConfig

    from packages.agent.adapters.durable_langgraph import ReferenceCheckpointSaver

    store = SimpleNamespace(graph_writes=AsyncMock(), graph_get=AsyncMock())
    saver = ReferenceCheckpointSaver(execution(store))
    config: RunnableConfig = {
        "configurable": {"thread_id": "checkpoint-lifecycle", "checkpoint_id": "never-started"}
    }
    writes = asyncio.create_task(saver.aput_writes(config, [("route", "advance")], "task"))
    try:
        await asyncio.sleep(0)
        assert not writes.done()
        saver.abort()
        with pytest.raises(DomainError, match="DURABLE_GRAPH_CHECKPOINT_FAILED"):
            await asyncio.wait_for(writes, 0.5)
        for operation in (
            saver.aput_writes(config, [("route", "advance")], "later-task"),
            saver.aget_tuple(config),
        ):
            with pytest.raises(DomainError, match="DURABLE_GRAPH_CHECKPOINT_FAILED"):
                await operation
        store.graph_writes.assert_not_awaited()
        store.graph_get.assert_not_awaited()
        assert set(saver._checkpoint_ready) == {"never-started"}
    finally:
        saver.abort()
        writes.cancel()
        await asyncio.gather(writes, return_exceptions=True)


async def test_failed_predecessor_releases_later_checkpoint_without_inserting_it() -> None:
    require_langgraph()
    from langchain_core.runnables import RunnableConfig
    from langgraph.pregel._checkpoint import empty_checkpoint

    from packages.agent.adapters.durable_langgraph import ReferenceCheckpointSaver

    error = RuntimeError("storage details are not part of the public error")
    store = SimpleNamespace(graph_put=AsyncMock(side_effect=error), graph_writes=AsyncMock())
    saver = ReferenceCheckpointSaver(execution(store))
    config: RunnableConfig = {"configurable": {"thread_id": "checkpoint-lifecycle"}}
    later: RunnableConfig = {
        "configurable": {**config["configurable"], "checkpoint_id": "later-not-started"}
    }
    writes = asyncio.create_task(saver.aput_writes(later, [("route", "advance")], "task"))
    try:
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError) as caught:
            await saver.aput(config, empty_checkpoint(), {"source": "input", "step": -1}, {})
        assert caught.value is error
        with pytest.raises(DomainError, match="DURABLE_GRAPH_CHECKPOINT_FAILED"):
            await asyncio.wait_for(writes, 0.5)
        with pytest.raises(DomainError, match="DURABLE_GRAPH_CHECKPOINT_FAILED"):
            await saver.aput(config, empty_checkpoint(), {"source": "input", "step": -1}, {})
        store.graph_put.assert_awaited_once()
        store.graph_writes.assert_not_awaited()
    finally:
        saver.abort()
        writes.cancel()
        await asyncio.gather(writes, return_exceptions=True)


async def test_abort_during_read_prevents_publishing_checkpoint_state() -> None:
    require_langgraph()
    from packages.agent.adapters.durable_langgraph import ReferenceCheckpointSaver

    entered, release = asyncio.Event(), asyncio.Event()

    async def read(*args: Any) -> None:
        entered.set()
        await release.wait()

    saver = ReferenceCheckpointSaver(
        execution(SimpleNamespace(graph_get=AsyncMock(side_effect=read)))
    )
    task = asyncio.create_task(
        saver.aget_tuple({"configurable": {"thread_id": "checkpoint-lifecycle"}})
    )
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        saver.abort()
        release.set()
        with pytest.raises(DomainError, match="DURABLE_GRAPH_CHECKPOINT_FAILED"):
            await asyncio.wait_for(task, 0.5)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_graph_cancel_aborts_dependencies_before_joining_owned_invoke(
    monkeypatch: Any,
) -> None:
    require_langgraph()
    from packages.agent.adapters import durable_langgraph as adapter

    entered, settled = asyncio.Event(), asyncio.Event()
    captured: list[Any] = []

    class Builder:
        def __init__(self, *args: Any) -> None:
            pass

        def add_node(self, *args: Any) -> None:
            pass

        def add_edge(self, *args: Any) -> None:
            pass

        def add_conditional_edges(self, *args: Any) -> None:
            pass

        def compile(self, *, checkpointer: Any, **kwargs: Any) -> Any:
            captured.append(checkpointer)

            async def get_state(config: Any) -> None:
                entered.set()
                try:
                    await checkpointer.aput_writes(
                        {"configurable": {**config["configurable"], "checkpoint_id": "missing"}},
                        [("route", "advance")],
                        "task",
                    )
                finally:
                    settled.set()

            return SimpleNamespace(aget_state=get_state)

    monkeypatch.setattr(adapter, "StateGraph", Builder)
    task = asyncio.create_task(adapter.run_durable_graph(execution(SimpleNamespace())))
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        assert captured[0]._closed
        assert settled.is_set()
        assert all(ready.is_set() for ready in captured[0]._checkpoint_ready.values())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
