"""Bound only opted-in RAG telemetry, preserving answers and required audit."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from packages.common.audit import (
    AuditEvent,
    AuditPort,
    AuditResource,
    AuditStatus,
    InMemoryAuditPort,
)
from packages.common.config import AppSettings
from packages.memory import ChatMemoryService
from packages.rag.auxiliary_audit import BoundedBestEffortAuditPort
from packages.rag.chat import ChatApplicationService
from packages.rag.dto import QueryCommand
from packages.rag.exceptions import RAG_QUERY_FAILED, RagQueryError
from packages.rag.generation import RagGenerationService
from packages.rag.service_token import (
    OpenAIChatCompletionRequest,
    OpenAIChatMessage,
    ServiceTokenChatAdapter,
)
from packages.rag.streaming import RagStreamEvent
from tests.unit.memory.test_chat_application_service import (
    FakeChatMemoryRepository,
    FakeRagQueryService,
)
from tests.unit.memory.test_chat_application_service import (
    _context as _chat_context,
)
from tests.unit.rag.test_query_service import _context, _service
from tests.unit.rag.test_service_token_adapter import StubChatService, StubToolBridge
from tests.unit.rag.test_service_token_adapter import _context as _adapter_context

CANARY = "PRIVATE_RAG_AUDIT_PAYLOAD_91cb"
Mode = Literal["stall", "resistant", "failure", "self_raise", "self_return", "healthy"]


class AuditProbe:
    def __init__(self, mode: Mode = "stall") -> None:
        self.mode = mode
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.events: list[AuditEvent] = []
        self.tasks: list[asyncio.Task[object] | None] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)
        task = asyncio.current_task()
        self.tasks.append(task)
        self.entered.set()
        try:
            if self.mode == "failure":
                raise RuntimeError(CANARY)
            if self.mode == "self_raise":
                raise asyncio.CancelledError(CANARY)
            if self.mode == "self_return":
                assert task is not None
                task.cancel(CANARY)
                return
            if self.mode == "healthy":
                return
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if self.mode != "resistant":
                    raise
                await self.release.wait()
                raise RuntimeError(CANARY) from None
        finally:
            self.finished.set()


def _bounded(audit: AuditPort, timeout: float = 0.02) -> BoundedBestEffortAuditPort:
    return BoundedBestEffortAuditPort(owned_audit=audit, timeout_seconds=timeout)


def _event() -> AuditEvent:
    return AuditEvent(
        request_id="req-1", trace_id="trace-1", tenant_id="tenant-1", user_id="user-1",
        action="rag.query.stream", resource=AuditResource(type="query", id="req-1"),
        status=AuditStatus.SUCCESS, latency_ms=1.0,
    )


async def _collect(stream: AsyncIterator[RagStreamEvent]) -> list[RagStreamEvent]:
    return [event async for event in stream]


def _assert_safe_logs(caplog: pytest.LogCaptureFixture) -> None:
    assert CANARY not in caplog.text
    assert CANARY not in repr([record.__dict__ for record in caplog.records])
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("mode", ["stall", "failure", "self_raise", "self_return"])
@pytest.mark.parametrize("provider", ["native", "langchain"])
async def test_validated_query_final_survives_optional_audit(
    mode: Mode, provider: str, caplog: pytest.LogCaptureFixture,
) -> None:
    audit = AuditProbe(mode)
    mandatory = InMemoryAuditPort()
    service = _service(audit=mandatory)
    service._stream_audit = _bounded(audit)
    if provider == "langchain":
        pytest.importorskip("langchain_openai")
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        from packages.llm.adapters.langchain import LangChainChatProvider

        service._generation_service = RagGenerationService(
            provider=LangChainChatProvider(
                provider="fake", model="fake-llm", base_url="https://unused.invalid",
                api_key="synthetic-test-only",
                chat_model=FakeListChatModel(responses=["基于上下文的回答。"]),
            ),
        )
    events = await asyncio.wait_for(
        _collect(service.stream_query(context=_context(), command=QueryCommand(query="question"))),
        timeout=2,
    )
    assert events[0].event == "citation"
    assert events[-1].event == "final"
    assert events[-1].payload.status == "success"  # type: ignore[union-attr]
    assert events[-1].payload.answer == "基于上下文的回答。"  # type: ignore[union-attr]
    assert sum(event.event == "final" for event in events) == 1
    assert not any(event.event == "error" for event in events)
    assert mandatory.events == []
    assert len(audit.events) == 1
    assert audit.events[0].metadata["citation_count"] == 1
    _assert_safe_logs(caplog)


async def test_no_answer_query_final_survives_stalled_optional_audit() -> None:
    audit = AuditProbe()
    service = _service(retrieval_candidates=())
    service._stream_audit = _bounded(audit)
    events = await asyncio.wait_for(
        _collect(service.stream_query(context=_context(), command=QueryCommand(query="question"))),
        timeout=1,
    )
    assert [event.event for event in events] == ["final"]
    assert events[-1].payload.no_answer is True  # type: ignore[union-attr]
    assert audit.cancelled.is_set()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mode", ["stall", "failure", "self_raise", "self_return"])
async def test_chat_commits_assistant_before_bounded_audit_and_final(
    streaming: bool, mode: Mode,
) -> None:
    repository = FakeChatMemoryRepository()
    audit = AuditProbe(mode)

    class OrderedAudit:
        async def record(self, event: AuditEvent) -> None:
            assert repository.commits == 2
            roles = [message.role for message in repository.created_messages]
            assert roles == ["user", "assistant"]
            await audit.record(event)

    service = ChatApplicationService(
        memory_service=ChatMemoryService(repository=repository),
        rag_query_service=FakeRagQueryService(), audit=_bounded(OrderedAudit()),
    )
    if streaming:
        events = await asyncio.wait_for(
            _collect(service.stream_chat(
                context=_chat_context(), command=QueryCommand(query="question"), session_id=None,
            )),
            timeout=1,
        )
        assert [event.event for event in events] == ["token", "final"]
        assert events[-1].payload.status == "success"  # type: ignore[union-attr]
    else:
        response = await asyncio.wait_for(service.chat(
            context=_chat_context(), command=QueryCommand(query="question"), session_id=None,
        ), timeout=1)
        assert response.answer == "基于上下文的回答。"
    assert repository.commits == 2
    assert repository.rollbacks == 0
    assert len(audit.events) == 1


@pytest.mark.parametrize("bridge", [False, True])
@pytest.mark.parametrize("mode", ["stall", "failure", "self_raise", "self_return"])
async def test_adapter_final_precedes_optional_audit_and_done_survives(
    bridge: bool, mode: Mode,
) -> None:
    audit = AuditProbe(mode)
    adapter = ServiceTokenChatAdapter(
        chat_service=StubChatService(), tool_bridge=StubToolBridge() if bridge else None,
        model_id="fake", owned_by="local", audit=_bounded(audit),
    )
    request = _request()
    if bridge:
        request = request.model_copy(update={
            "tools": ({
                "type": "function",
                "function": {
                    "name": "calculator", "description": "Calculate", "parameters": {
                        "type": "object", "properties": {"expression": {"type": "string"}},
                    },
                },
            },),
            "tool_choice": "auto",
        })
    stream = adapter.stream_chat_completion(context=_adapter_context(), request=request)
    frames: list[str] = []
    async with asyncio.timeout(1):
        async for frame in stream:
            frames.append(frame)
            if '"finish_reason":"stop"' in frame or '"finish_reason": "stop"' in frame:
                assert not audit.entered.is_set()
    assert frames[-1] == "data: [DONE]\n\n"
    assert len(audit.events) == 1
    assert audit.events[0].action == "rag.service_token.chat.stream"


@pytest.mark.parametrize("mode", ["failure", "self_raise"])
async def test_nonstream_query_required_audit_still_propagates(mode: Mode) -> None:
    mandatory = AuditProbe(mode)
    optional = InMemoryAuditPort()
    service = _service(audit=mandatory)
    service._stream_audit = _bounded(optional)
    expected = RagQueryError if mode == "failure" else asyncio.CancelledError
    with pytest.raises(expected) as error:
        await service.query(context=_context(), command=QueryCommand(query="question"))
    if isinstance(error.value, RagQueryError):
        assert error.value.code == RAG_QUERY_FAILED
    assert optional.events == []
    assert all(task is asyncio.current_task() for task in mandatory.tasks)


async def test_nonstream_query_audit_is_not_reclassified_or_timed_out() -> None:
    mandatory = AuditProbe()
    service = _service(audit=mandatory)
    service._stream_audit = _bounded(InMemoryAuditPort(), timeout=0.001)
    operation = asyncio.create_task(
        service.query(context=_context(), command=QueryCommand(query="question")),
    )
    try:
        await asyncio.wait_for(mandatory.entered.wait(), timeout=1)
        await asyncio.sleep(0.03)
        assert not operation.done()
        assert mandatory.tasks == [operation]
        assert not mandatory.cancelled.is_set()
        mandatory.release.set()
        assert (await operation).answer == "基于上下文的回答。"
    finally:
        mandatory.release.set()
        await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("target", ["query", "chat", "adapter"])
async def test_legacy_programmatic_ports_remain_inline_and_unbounded(target: str) -> None:
    audit = AuditProbe()
    if target == "query":
        service = _service(audit=audit)
        assert service._stream_audit is audit
        operation = asyncio.create_task(_collect(service.stream_query(
            context=_context(), command=QueryCommand(query="question"),
        )))
    elif target == "chat":
        chat = ChatApplicationService(
            memory_service=ChatMemoryService(repository=FakeChatMemoryRepository()),
            rag_query_service=FakeRagQueryService(), audit=audit,
        )
        operation = asyncio.create_task(_collect(chat.stream_chat(
            context=_chat_context(), command=QueryCommand(query="question"), session_id=None,
        )))
    else:
        adapter = ServiceTokenChatAdapter(
            chat_service=StubChatService(), model_id="fake", owned_by="local", audit=audit,
        )

        async def collect_adapter() -> list[str]:
            return [frame async for frame in adapter.stream_chat_completion(
                context=_adapter_context(), request=_request(),
            )]

        operation = asyncio.create_task(collect_adapter())  # type: ignore[assignment]
    try:
        await asyncio.wait_for(audit.entered.wait(), timeout=1)
        await asyncio.sleep(0.03)
        assert not operation.done()
        assert audit.tasks == [operation]
        assert not audit.cancelled.is_set()
    finally:
        audit.release.set()
        await operation


@pytest.mark.parametrize("mode", ["self_raise", "self_return"])
async def test_delivered_cancellation_count_does_not_misclassify_self_cancel(mode: Mode) -> None:
    async def caller() -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        assert task.cancelling() == 1
        await _bounded(AuditProbe(mode)).record(_event())
        assert task.cancelling() == 1

    await asyncio.create_task(caller())


async def test_pending_external_cancellation_is_not_swallowed() -> None:
    async def caller() -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await _bounded(AuditProbe("self_raise")).record(_event())

    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(caller())


@pytest.mark.parametrize("self_first", [False, True])
async def test_same_loop_caller_cancel_wins_over_child_self_cancel(self_first: bool) -> None:
    caller: asyncio.Task[None] | None = None

    class RacingAudit:
        async def record(self, event: AuditEvent) -> None:
            child = asyncio.current_task()
            assert child is not None and caller is not None
            if self_first:
                child.cancel()
                caller.cancel()
            else:
                caller.cancel()
                child.cancel()

    caller = asyncio.create_task(_bounded(RacingAudit()).record(_event()))
    with pytest.raises(asyncio.CancelledError):
        await caller


@pytest.mark.parametrize("repeat", [False, True])
async def test_external_cancellation_and_repeated_cleanup_cancellation_propagate(
    repeat: bool,
) -> None:
    audit = AuditProbe("resistant")
    task = asyncio.create_task(_bounded(audit, timeout=1).record(_event()))
    try:
        await asyncio.wait_for(audit.entered.wait(), timeout=1)
        task.cancel()
        await asyncio.wait_for(audit.cancelled.wait(), timeout=1)
        if repeat:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.3)
    finally:
        audit.release.set()
        await asyncio.wait_for(audit.finished.wait(), timeout=1)
        await asyncio.sleep(0)


async def test_external_cancellation_during_timeout_cleanup_remains_cancellation() -> None:
    audit = AuditProbe("resistant")
    task = asyncio.create_task(_bounded(audit, timeout=0.001).record(_event()))
    try:
        await asyncio.wait_for(audit.cancelled.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        audit.release.set()
        await asyncio.wait_for(audit.finished.wait(), timeout=1)
        await asyncio.sleep(0)


async def test_resistant_late_failure_is_owned_and_has_type_only_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    audit = AuditProbe("resistant")
    try:
        await asyncio.wait_for(_bounded(audit).record(_event()), timeout=0.5)
        assert not audit.finished.is_set()
        assert "rag.auxiliary_audit.cancel_timeout" in caplog.text
        assert any(getattr(record, "audit_error_type", None) == "TimeoutError"
                   for record in caplog.records)
    finally:
        audit.release.set()
        await asyncio.wait_for(audit.finished.wait(), timeout=1)
        await asyncio.sleep(0)
    _assert_safe_logs(caplog)


@pytest.mark.parametrize("target", ["query", "chat", "adapter"])
async def test_cancellation_during_optional_audit_never_emits_success(target: str) -> None:
    audit = AuditProbe()
    events: list[object] = []
    repository = FakeChatMemoryRepository()

    async def consume() -> None:
        if target == "query":
            query = _service()
            query._stream_audit = _bounded(audit)
            async for event in query.stream_query(
                context=_context(), command=QueryCommand(query="question"),
            ):
                events.append(event)
        elif target == "chat":
            chat = ChatApplicationService(
                memory_service=ChatMemoryService(repository=repository),
                rag_query_service=FakeRagQueryService(), audit=_bounded(audit),
            )
            async for event in chat.stream_chat(
                context=_chat_context(), command=QueryCommand(query="question"), session_id=None,
            ):
                events.append(event)
        else:
            adapter = ServiceTokenChatAdapter(
                chat_service=StubChatService(), model_id="fake", owned_by="local",
                audit=_bounded(audit),
            )
            async for frame in adapter.stream_chat_completion(
                context=_adapter_context(), request=_request(),
            ):
                events.append(frame)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(audit.entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)
    if target == "adapter":
        assert "data: [DONE]\n\n" not in events
    else:
        assert not any(isinstance(event, RagStreamEvent) and event.event == "final"
                       for event in events)
    if target == "chat":
        assert repository.commits == 2
        assert repository.rollbacks == 0


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_bounded_audit_rejects_invalid_timeout(value: float) -> None:
    with pytest.raises(ValueError):
        _bounded(InMemoryAuditPort(), timeout=value)


def test_rag_timeout_setting_is_independent_and_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_AUXILIARY_IO_TIMEOUT_SECONDS", "0.125")
    monkeypatch.setenv("AGENT_AUXILIARY_IO_TIMEOUT_SECONDS", "0.75")
    settings = AppSettings(_env_file=None)
    assert settings.rag_auxiliary_io_timeout_seconds == 0.125
    assert settings.agent_auxiliary_io_timeout_seconds == 0.75
    for value in ("0", "nan", "inf", "31"):
        monkeypatch.setenv("RAG_AUXILIARY_IO_TIMEOUT_SECONDS", value)
        with pytest.raises(ValueError):
            AppSettings(_env_file=None)


def _request() -> OpenAIChatCompletionRequest:
    return OpenAIChatCompletionRequest(
        model="fake", messages=(OpenAIChatMessage(role="user", content="question"),),
        stream=True,
    )


@pytest.mark.parametrize("mode", ["stall", "failure", "self_raise", "self_return"])
async def test_nonstream_adapter_optional_audit_cannot_replace_answer(mode: Mode) -> None:
    audit = AuditProbe(mode)
    adapter = ServiceTokenChatAdapter(
        chat_service=StubChatService(), model_id="fake", owned_by="local", audit=_bounded(audit),
    )
    response = await asyncio.wait_for(adapter.chat_completion(
        context=_adapter_context(), request=_request().model_copy(update={"stream": False}),
    ), timeout=1)
    assert response.choices[0].message.content == "answer with trusted citations"
    assert len(audit.events) == 1
    assert audit.events[0].action == "rag.service_token.chat"


async def test_provider_error_still_has_error_final_when_optional_audit_stalls() -> None:
    service = _service(provider_failure_mode="stream_failed")
    service._stream_audit = _bounded(AuditProbe())
    events = await asyncio.wait_for(_collect(service.stream_query(
        context=_context(), command=QueryCommand(query="question"),
    )), timeout=1)
    assert [event.event for event in events] == ["citation", "error", "final"]
    assert events[-1].payload.status == "error"  # type: ignore[union-attr]


@pytest.mark.parametrize("streaming", [False, True])
async def test_required_chat_commit_failure_remains_failure(streaming: bool) -> None:
    class FailingCommitRepository(FakeChatMemoryRepository):
        async def commit(self) -> None:
            self.commits += 1
            if self.commits == 2:
                raise RuntimeError("synthetic mandatory commit failure")

    repository = FailingCommitRepository()
    service = ChatApplicationService(
        memory_service=ChatMemoryService(repository=repository),
        rag_query_service=FakeRagQueryService(), audit=_bounded(AuditProbe()),
    )
    if streaming:
        events = await asyncio.wait_for(_collect(service.stream_chat(
            context=_chat_context(), command=QueryCommand(query="question"), session_id=None,
        )), timeout=1)
        assert [event.event for event in events] == ["token", "error", "final"]
        assert events[-1].payload.status == "error"  # type: ignore[union-attr]
    else:
        with pytest.raises(RagQueryError):
            await asyncio.wait_for(service.chat(
                context=_chat_context(), command=QueryCommand(query="question"), session_id=None,
            ), timeout=1)
    assert repository.rollbacks == 1
