"""Normal operation resource ownership, including bounded cancellation cleanup."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import packages.agent.storage.session_scoped as agent_storage
from packages.agent.dto import ToolCallCreate, ToolRateLimit
from packages.agent.exceptions import TOOL_TIMEOUT, AgentToolError
from packages.agent.registry import ToolRegistry
from packages.agent.tools.rag_search import build_rag_search_tool
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.retrieval.application import RetrieveCommand, RetrieveResponse
from packages.retrieval.storage.session_scoped import SessionFactoryRetrievalApplication


class OwnedSession:
    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> OwnedSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.closed = True


class Sessions:
    def __init__(self) -> None:
        self.opened: list[OwnedSession] = []

    def __call__(self) -> OwnedSession:
        session = OwnedSession()
        self.opened.append(session)
        return session

    @property
    def factory(self) -> async_sessionmaker[AsyncSession]:
        return cast(async_sessionmaker[AsyncSession], self)


def context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="ordinary-request",
        trace_id="ordinary-trace",
        auth=AuthContext(
            tenant_id="tenant-a",
            user_id="user-a",
            roles=("knowledge_user",),
            permissions=("agent:tool:rag_search", "document:read", "retrieval:query"),
        ),
    )


def response() -> RetrieveResponse:
    return RetrieveResponse(
        request_id="ordinary-request",
        trace_id="ordinary-trace",
        tenant_id="tenant-a",
        user_id="user-a",
        top_k=5,
        query_summary={"length": 8},
        latency_ms=1.0,
    )


async def test_each_retrieval_owns_and_closes_its_session() -> None:
    sessions = Sessions()

    class App:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def retrieve(self, **_kwargs: object) -> RetrieveResponse:
            assert not self.session.closed
            return response()

    application = SessionFactoryRetrievalApplication(
        session_factory=sessions.factory,
        build_service=cast(Any, App),
    )
    for _ in range(2):
        assert (
            await application.retrieve(
                context=context(),
                command=RetrieveCommand(query="ordinary"),
            )
            == response()
        )
    assert len(sessions.opened) == 2
    assert sessions.opened[0] is not sessions.opened[1]
    assert all(session.closed for session in sessions.opened)


async def test_retrieval_failure_closes_owned_session_and_propagates() -> None:
    sessions = Sessions()

    class App:
        async def retrieve(self, **_kwargs: object) -> RetrieveResponse:
            raise OSError("ordinary upstream failure")

    application = SessionFactoryRetrievalApplication(
        session_factory=sessions.factory,
        build_service=lambda _session: cast(Any, App()),
    )
    with pytest.raises(OSError):
        await application.retrieve(context=context(), command=RetrieveCommand(query="ordinary"))
    assert len(sessions.opened) == 1 and sessions.opened[0].closed


async def test_late_tool_cleanup_keeps_its_own_session_until_it_finishes() -> None:
    sessions = Sessions()
    entered, cancelled, release, finished = (asyncio.Event() for _ in range(4))

    class App:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def retrieve(self, **_kwargs: object) -> RetrieveResponse:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                # Finite cleanup simulates an integration that needs time to
                # release native I/O. This task still owns its SQL resources.
                await release.wait()
            assert not self.session.closed
            finished.set()
            return response()

    application = SessionFactoryRetrievalApplication(
        session_factory=sessions.factory,
        build_service=cast(Any, App),
    )
    registry = ToolRegistry(audit=InMemoryAuditPort())
    registry.register(
        build_rag_search_tool(
            retrieval_app=application,
            timeout_seconds=0.02,
            rate_limit=ToolRateLimit(max_calls=3, window_seconds=60),
        )
    )
    request_session = OwnedSession()
    try:
        async with request_session:
            with pytest.raises(AgentToolError) as error:
                await registry.execute(
                    name="rag_search",
                    arguments={"query": "ordinary"},
                    context=context(),
                )
            assert error.value.code == TOOL_TIMEOUT
        await asyncio.wait_for(cancelled.wait(), 2)
        assert entered.is_set() and request_session.closed
        assert len(sessions.opened) == 1
        assert sessions.opened[0] is not request_session
        assert not sessions.opened[0].closed
    finally:
        release.set()
        if entered.is_set():
            await asyncio.wait_for(finished.wait(), 2)
        # Let the owning context exit and the registry's done callback run.
        await asyncio.sleep(0)
    assert sessions.opened[0].closed


async def test_required_recorder_and_lookup_own_separate_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = Sessions()
    used: list[OwnedSession] = []

    class Repository:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def record_tool_call(self, _record: ToolCallCreate) -> None:
            assert not self.session.closed
            used.append(self.session)

        async def list_by_agent_run(self, **_kwargs: object) -> list[Any]:
            assert not self.session.closed
            used.append(self.session)
            return []

    monkeypatch.setattr(agent_storage, "ToolCallRepository", Repository)
    recorder = agent_storage.SessionFactoryToolCallRecorder(sessions.factory)
    await recorder.record_tool_call(
        ToolCallCreate(
            agent_run_id="run-a",
            request_id="ordinary-request",
            trace_id="ordinary-trace",
            tenant_id="tenant-a",
            user_id="user-a",
            tool_name="calculator",
            permission="agent:tool:calculator",
            status="success",
            latency_ms=1,
        )
    )
    assert (
        await recorder.list_by_agent_run(
            tenant_id="tenant-a",
            user_id="user-a",
            agent_run_id="run-a",
        )
        == []
    )
    assert used == sessions.opened
    assert len(used) == 2 and used[0] is not used[1]
    assert all(session.closed for session in used)


async def test_required_recorder_failure_is_not_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = Sessions()

    class Repository:
        def __init__(self, _session: Any) -> None:
            pass

        async def record_tool_call(self, _record: Any) -> None:
            raise OSError("storage unavailable")

    monkeypatch.setattr(agent_storage, "ToolCallRepository", Repository)
    with pytest.raises(OSError):
        await agent_storage.SessionFactoryToolCallRecorder(sessions.factory).record_tool_call(
            cast(Any, type("Record", (), {"tenant_id": "tenant-a"})()),
        )
    assert sessions.opened[0].closed


async def test_evidence_operation_owns_and_closes_its_session() -> None:
    sessions = Sessions()
    result = await agent_storage.SessionFactoryEvidenceRevalidator(sessions.factory).rehydrate(
        context=context(),
        observations=(),
        fingerprints={},
    )
    assert result.observations == ()
    assert len(sessions.opened) == 1 and sessions.opened[0].closed
