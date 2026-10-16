"""A finite late retrieval and terminal persistence use real separate SQL sessions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.agent.adapters.session_scoped import (
    SessionFactoryRetrievalApplication,
    SessionFactoryToolCallRecorder,
)
from packages.agent.dto import ToolRateLimit
from packages.agent.exceptions import TOOL_TIMEOUT, AgentToolError
from packages.agent.registry import ToolRegistry
from packages.agent.storage.models import AgentRunModel, ToolCallModel
from packages.agent.tools.rag_search import build_rag_search_tool
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.base import Base
from packages.retrieval.application import RetrieveResponse


async def test_terminal_commit_and_late_retrieval_keep_independent_transactions(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'owned.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entered, cancelled, release, finished = (asyncio.Event() for _ in range(4))
    used: list[AsyncSession] = []
    context = AuthenticatedRequestContext(
        request_id="ordinary-request",
        trace_id="ordinary-trace",
        auth=AuthContext(
            tenant_id="tenant-a",
            user_id="user-a",
            roles=("knowledge_user",),
            permissions=("agent:tool:rag_search", "document:read", "retrieval:query"),
        ),
    )

    class Retrieval:
        def __init__(self, session: AsyncSession) -> None:
            self.session = session
            used.append(session)

        async def retrieve(self, **_kwargs: object) -> RetrieveResponse:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            try:
                run = await self.session.get(AgentRunModel, "run-a")
                assert run is not None and run.status == "stopped"
                return RetrieveResponse(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id="tenant-a",
                    user_id="user-a",
                    top_k=5,
                    query_summary={"length": 8},
                    latency_ms=1,
                )
            finally:
                finished.set()

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync,
                    tables=[AgentRunModel.__table__, ToolCallModel.__table__],
                )
            )
        async with sessions() as seed:
            seed.add(
                AgentRunModel(
                    id="run-a",
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id="tenant-a",
                    user_id="user-a",
                    created_by="user-a",
                    status="running",
                    max_steps=3,
                    max_tool_calls=2,
                    timeout_seconds=5,
                )
            )
            await seed.commit()
        registry = ToolRegistry(
            audit=InMemoryAuditPort(),
            tool_call_recorder=SessionFactoryToolCallRecorder(sessions),
        )
        registry.register(
            build_rag_search_tool(
                retrieval_app=SessionFactoryRetrievalApplication(
                    session_factory=sessions,
                    build_service=cast(Any, Retrieval),
                ),
                timeout_seconds=0.03,
                rate_limit=ToolRateLimit(max_calls=3, window_seconds=60),
            )
        )
        async with sessions() as request_session:
            with pytest.raises(AgentToolError) as error:
                await registry.execute(
                    name="rag_search",
                    arguments={"query": "ordinary"},
                    context=context,
                    agent_run_id="run-a",
                )
            assert error.value.code == TOOL_TIMEOUT
            await asyncio.wait_for(cancelled.wait(), 2)
            run = await request_session.get(AgentRunModel, "run-a")
            assert run is not None
            run.status = "stopped"
            await request_session.commit()
        assert entered.is_set() and used[0] is not request_session
        release.set()
        await asyncio.wait_for(finished.wait(), 2)
        await asyncio.sleep(0)
        async with sessions() as observer:
            calls = list(await observer.scalars(select(ToolCallModel)))
            assert len(calls) == 1 and calls[0].error_code == TOOL_TIMEOUT
            run = await observer.get(AgentRunModel, "run-a")
            assert run is not None and run.status == "stopped"
    finally:
        release.set()
        if entered.is_set():
            await asyncio.wait_for(finished.wait(), 2)
            await asyncio.sleep(0)
        await engine.dispose()
