"""A failed checkpoint insertion must settle dependent framework writes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from packages.agent.durable.dto import durable_error
from packages.common.errors import DomainError
from tests.framework_imports import require_langgraph


@pytest.mark.parametrize("failure", ["storage-error", "cancelled"])
async def test_failed_checkpoint_insert_releases_waiters_without_writes(failure: str) -> None:
    require_langgraph()
    from langchain_core.runnables import RunnableConfig
    from langgraph.pregel._checkpoint import empty_checkpoint

    from packages.agent.adapters.durable_langgraph import ReferenceCheckpointSaver
    from packages.agent.durable.service import _DurableExecution

    entered, release = asyncio.Event(), asyncio.Event()
    error = (
        durable_error("DURABLE_STORAGE_UNAVAILABLE", status_code=503)
        if failure == "storage-error"
        else asyncio.CancelledError()
    )

    async def fail_insert(*args: Any, **kwargs: Any) -> None:
        entered.set()
        await release.wait()
        raise error

    store = SimpleNamespace(
        graph_put=AsyncMock(side_effect=fail_insert),
        graph_writes=AsyncMock(),
    )
    execution = SimpleNamespace(
        snapshot=SimpleNamespace(run_id="checkpoint-failure"),
        token="test-lease",
        service=SimpleNamespace(_store=store, _clock=lambda: 100.0),
    )
    saver = ReferenceCheckpointSaver(cast(_DurableExecution, execution))
    checkpoint = empty_checkpoint()
    config: RunnableConfig = {"configurable": {"thread_id": execution.snapshot.run_id}}
    write_config: RunnableConfig = {
        "configurable": {
            "thread_id": execution.snapshot.run_id,
            "checkpoint_id": checkpoint["id"],
        },
    }
    insertion = asyncio.create_task(
        saver.aput(config, checkpoint, {"source": "input", "step": -1}, {})
    )
    writes: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        writes = asyncio.create_task(
            saver.aput_writes(write_config, [("route", "advance")], task_id="task-1")
        )
        await asyncio.sleep(0)
        assert not writes.done()
        store.graph_writes.assert_not_awaited()

        release.set()
        with pytest.raises(type(error)) as insertion_error:
            await asyncio.wait_for(insertion, timeout=2)
        assert insertion_error.value is error
        with pytest.raises(DomainError) as write_error:
            await asyncio.wait_for(writes, timeout=2)
        assert write_error.value.code == "DURABLE_GRAPH_CHECKPOINT_FAILED"
        store.graph_put.assert_awaited_once()
        store.graph_writes.assert_not_awaited()
    finally:
        release.set()
        tasks = [insertion] if writes is None else [insertion, writes]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
