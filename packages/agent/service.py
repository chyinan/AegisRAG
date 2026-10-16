from __future__ import annotations

# pattern: Imperative Shell
import asyncio
import hashlib
import logging
import math
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from time import perf_counter as default_perf_counter
from typing import Protocol

from packages.agent.auxiliary_io import (
    AUXILIARY_IO_TIMEOUT_SECONDS,
    await_auxiliary_io,
    validate_auxiliary_timeout,
)
from packages.agent.dto import (
    AgentRunCommand,
    AgentRunCreate,
    AgentRunRecord,
    AgentRunResponse,
    AgentRunUpdate,
)
from packages.agent.exceptions import (
    AGENT_RUN_FAILED,
    AGENT_RUN_FORBIDDEN,
    AGENT_RUN_STORAGE_FAILED,
    AgentRunError,
    agent_run_storage_failed,
)
from packages.agent.runtime import AgentEventSink, AgentRunConfig, AgentRunResult
from packages.agent.streaming import AgentStreamEvent, AgentStreamSink
from packages.auth.policies import has_agent_run_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError

logger = logging.getLogger(__name__)
AGENT_RUN_CANCELLED = "AGENT_RUN_CANCELLED"
PERSISTENCE_PHASE_TIMEOUT_SECONDS = 5.0
MAX_PERSISTENCE_PHASE_TIMEOUT_SECONDS = 60.0


@dataclass
class _PersistenceState:
    """Request-owned acknowledgement state; an interrupted commit is uncertain."""

    run_id: str | None = None
    initial_acknowledged: bool = False
    terminal_acknowledged: bool = False
    terminal_phase_succeeded: bool = False
    commit_started: bool = False
    commit_acknowledged: bool = False

    @property
    def commit_uncertain(self) -> bool:
        return self.commit_started and not self.commit_acknowledged


_FORBIDDEN_METADATA_KEYS = {
    "absolute_path",
    "access_token",
    "answer",
    "api_key",
    "authorization",
    "content",
    "cookie",
    "credential",
    "file_content",
    "file_path",
    "hidden_reasoning",
    "local_path",
    "messages",
    "password",
    "private_key",
    "prompt",
    "query",
    "raw_output",
    "raw_tool_arguments",
    "raw_tool_output",
    "secret",
    "thought",
    "token",
    "tool_results",
}


class AgentRunRepositoryPort(Protocol):
    async def create_run(self, record: AgentRunCreate) -> AgentRunRecord: ...

    async def update_run_result(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        update: AgentRunUpdate,
    ) -> AgentRunRecord: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class AgentRuntimePort(Protocol):
    async def run(self, *, context: AuthenticatedRequestContext) -> AgentRunResult: ...


RuntimeFactory = Callable[[AgentRunConfig, str], AgentRuntimePort]
StreamRuntimeFactory = Callable[[AgentRunConfig, str, AgentEventSink], AgentRuntimePort]


class AgentRunApplicationService:
    def __init__(
        self,
        *,
        repository: AgentRunRepositoryPort,
        runtime_factory: RuntimeFactory,
        audit: AuditPort,
        default_max_steps: int,
        default_max_tool_calls: int,
        default_timeout_seconds: float,
        repeated_action_threshold: int,
        perf_counter: Callable[[], float] | None = None,
        stream_runtime_factory: StreamRuntimeFactory | None = None,
        auxiliary_io_timeout_seconds: float = AUXILIARY_IO_TIMEOUT_SECONDS,
        persistence_phase_timeout_seconds: float = PERSISTENCE_PHASE_TIMEOUT_SECONDS,
    ) -> None:
        self._auxiliary_io_timeout_seconds = validate_auxiliary_timeout(
            auxiliary_io_timeout_seconds,
        )
        if (
            not math.isfinite(persistence_phase_timeout_seconds)
            or not 0 < persistence_phase_timeout_seconds <= MAX_PERSISTENCE_PHASE_TIMEOUT_SECONDS
        ):
            raise ValueError("persistence_phase_timeout_seconds must be finite and in (0, 60]")
        self._persistence_phase_timeout_seconds = persistence_phase_timeout_seconds
        self._repository = repository
        self._runtime_factory = runtime_factory
        self._stream_runtime_factory = stream_runtime_factory
        self._audit = audit
        self._default_max_steps = default_max_steps
        self._default_max_tool_calls = default_max_tool_calls
        self._default_timeout_seconds = default_timeout_seconds
        self._repeated_action_threshold = repeated_action_threshold
        self._perf_counter = perf_counter or default_perf_counter

    async def run(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: AgentRunCommand,
        event_sink: AgentStreamSink | None = None,
    ) -> AgentRunResponse:
        started = self._perf_counter()
        if not has_agent_run_permission(context.auth):
            error = AgentRunError(
                code=AGENT_RUN_FORBIDDEN,
                message="Agent run permission is required.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "required_permissions": ["agent:run"],
                    "error_code": AGENT_RUN_FORBIDDEN,
                },
                status_code=403,
            )
            await self._record_audit(
                context=context,
                run_id=context.request_id,
                action="agent.run.denied",
                status=AuditStatus.DENIED,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata={"required_permissions": ["agent:run"]},
            )
            raise error

        config = self._config(command)
        persistence = _PersistenceState()
        caller = asyncio.current_task()
        caller_cancellations = 0
        try:
            # A pending disconnect must be delivered before the baseline. An
            # old cancellation already caught by a direct caller is not new.
            await asyncio.sleep(0)
            caller_cancellations = caller.cancelling() if caller is not None else 0
            created = await self._persist_phase(
                context=context,
                state=persistence,
                terminal=False,
                write=lambda: self._repository.create_run(
                    AgentRunCreate(
                        request_id=context.request_id,
                        trace_id=context.trace_id,
                        tenant_id=context.auth.tenant_id,
                        user_id=context.auth.user_id,
                        created_by=context.auth.user_id,
                        status="running",
                        max_steps=config.max_steps,
                        max_tool_calls=config.max_tool_calls,
                        timeout_seconds=config.timeout_seconds,
                        input_summary=_input_summary(command.input),
                        metadata=_create_metadata(command=command, config=config),
                    )
                ),
            )
            if event_sink is not None:
                event_sink.started(created)
            await self._record_audit(
                context=context,
                run_id=created.id,
                action="agent.run.started",
                status=AuditStatus.SUCCESS,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=None,
                metadata=_audit_metadata(created.metadata, agent_run_id=created.id),
            )
            try:
                runtime = (
                    self._stream_runtime_factory(config, created.id, event_sink)
                    if event_sink is not None and self._stream_runtime_factory is not None
                    else self._runtime_factory(config, created.id)
                )
                result = await runtime.run(context=context)
            except Exception as exc:
                await self._mark_failed_after_runtime_exception(
                    state=persistence,
                    context=context,
                    run_id=created.id,
                    started=started,
                )
                raise AgentRunError(
                    code=AGENT_RUN_FAILED,
                    message="Agent run failed.",
                    details={
                        "request_id": context.request_id,
                        "trace_id": context.trace_id,
                        "tenant_id": context.auth.tenant_id,
                        "user_id": context.auth.user_id,
                        "agent_run_id": created.id,
                        "error_code": AGENT_RUN_FAILED,
                    },
                    status_code=500,
                ) from exc
            updated = await self._persist_phase(
                context=context,
                state=persistence,
                terminal=True,
                write=lambda: self._update_from_result(
                    context=context,
                    run_id=created.id,
                    result=result,
                ),
            )
            await self._record_audit(
                context=context,
                run_id=updated.id,
                action=_lifecycle_action(updated.status),
                status=_audit_status(updated.status),
                latency_ms=updated.latency_ms or _elapsed_ms(self._perf_counter() - started),
                error_code=updated.error_code,
                metadata=_audit_metadata(updated.metadata, agent_run_id=updated.id),
            )
            return AgentRunResponse.from_record(
                updated,
                final_answer=result.final_answer,
                final_citations=result.final_citations,
            )
        except asyncio.CancelledError:
            if not persistence.terminal_acknowledged:
                if (
                    persistence.initial_acknowledged
                    and not persistence.commit_uncertain
                    and persistence.run_id is not None
                ):
                    cancellation_persisted = await self._finish_cancellation(
                        context=context,
                        state=persistence,
                        started=started,
                    )
                else:
                    # Never retry/overwrite an initial or terminal commit whose
                    # acknowledgement was interrupted. Rollback is cleanup only.
                    cleanup = asyncio.create_task(self._safe_rollback())
                    await _await_owned_cleanup(cleanup)
                    cancellation_persisted = False
                current = asyncio.current_task()
                if not cancellation_persisted and (
                    current is None or current.cancelling() <= caller_cancellations
                ):
                    raise self._storage_error(context, persistence) from None
            raise
        except AgentRunError as exc:
            await self._safe_rollback()
            if exc.code == AGENT_RUN_STORAGE_FAILED:
                raise agent_run_storage_failed(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    run_id=persistence.run_id,
                    reason="run_storage_failed",
                ) from exc
            raise
        except DomainError:
            await self._safe_rollback()
            raise
        except Exception as exc:
            await self._safe_rollback()
            raise AgentRunError(
                code=AGENT_RUN_FAILED,
                message="Agent run failed.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "error_code": AGENT_RUN_FAILED,
                },
                status_code=500,
            ) from exc

    async def stream(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: AgentRunCommand,
    ) -> AsyncGenerator[AgentStreamEvent, None]:
        """Stream redacted progress and one terminal event after persistence.

        Closing the iterator cancels the owned runtime and waits for its bounded
        terminal-storage cleanup. A socket disconnect never detaches agent work.
        """
        sink = AgentStreamSink(request_id=context.request_id, trace_id=context.trace_id)

        async def produce() -> None:
            try:
                result = await self.run(context=context, command=command, event_sink=sink)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                # A provider can cancel its own awaited operation without the
                # HTTP consumer disconnecting. The service has persisted that
                # interruption; the still-connected client needs a terminal.
                await sink.fail(AGENT_RUN_CANCELLED)
            except DomainError as exc:
                await sink.fail(exc.code)
            except Exception:
                await sink.fail(AGENT_RUN_FAILED)
            else:
                await sink.finish(result)

        producer = asyncio.create_task(produce())
        pending_event: asyncio.Task[AgentStreamEvent] | None = None
        try:
            while True:
                pending_event = asyncio.create_task(sink.queue.get())
                done, _pending = await asyncio.wait(
                    {pending_event, producer},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if pending_event not in done and sink.queue.empty():
                    # A producer may itself be cancelled by its provider. Do not
                    # invent a successful final event for a truncated run.
                    await producer
                    return
                event = await pending_event
                pending_event = None
                yield event
                if event.event in {"final", "error"}:
                    await producer
                    return
        finally:
            sink.closed = True
            if pending_event is not None:
                pending_event.cancel()
                with suppress(asyncio.CancelledError):
                    await pending_event
            if not producer.done():
                producer.cancel()
            await _await_owned_cleanup(producer)

    async def _persist_phase(
        self,
        *,
        context: AuthenticatedRequestContext,
        state: _PersistenceState,
        terminal: bool,
        write: Callable[[], Awaitable[AgentRunRecord]],
        enclosing_deadline: asyncio.Timeout | None = None,
    ) -> AgentRunRecord:
        """One cooperative deadline covers write and commit in the current task.

        Mandatory storage is never detached. A driver suppressing cancellation
        can exceed the deadline; its eventual return still cannot mean success.
        """
        state.commit_started = False
        state.commit_acknowledged = False
        # Deliver an already-requested cancellation before taking the baseline;
        # an old, already-delivered cancellation count is not a new disconnect.
        await asyncio.sleep(0)
        current = asyncio.current_task()
        cancellations = current.cancelling() if current is not None else 0

        def check_deadline(deadline: asyncio.Timeout) -> None:
            expired = int(deadline.expired()) + int(
                enclosing_deadline is not None and enclosing_deadline.expired()
            )
            if current is not None and current.cancelling() > cancellations + expired:
                raise asyncio.CancelledError
            if expired:
                raise TimeoutError

        try:
            async with asyncio.timeout(self._persistence_phase_timeout_seconds) as deadline:
                check_deadline(deadline)
                record = await write()
                state.run_id = record.id
                check_deadline(deadline)
                state.commit_started = True
                await self._repository.commit()
                state.commit_acknowledged = True
                # Mark the authoritative terminal before any subsequent await,
                # including audit. A late acknowledgement is still not success.
                if terminal:
                    state.terminal_acknowledged = True
                else:
                    state.initial_acknowledged = True
                check_deadline(deadline)
                if terminal:
                    state.terminal_phase_succeeded = True
                return record
        except asyncio.CancelledError:
            if current is not None and current.cancelling() > cancellations:
                raise
            # An awaited storage operation can cancel itself while the client
            # remains connected. That is not a persisted cancellation receipt.
            raise self._storage_error(context, state) from None
        except Exception as exc:
            raise self._storage_error(context, state) from exc

    @staticmethod
    def _storage_error(
        context: AuthenticatedRequestContext,
        state: _PersistenceState,
    ) -> AgentRunError:
        return agent_run_storage_failed(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            run_id=state.run_id,
            reason="run_storage_failed",
        )

    async def _finish_cancellation(
        self,
        *,
        context: AuthenticatedRequestContext,
        state: _PersistenceState,
        started: float,
    ) -> bool:
        run_id = state.run_id
        assert run_id is not None

        async def persist() -> None:
            # Preserve the owned cancellation-cleanup envelope. Repeated caller
            # cancellation cannot detach its request-session operations.
            async with asyncio.timeout(self._persistence_phase_timeout_seconds) as cleanup_deadline:
                if not await self._safe_rollback() or cleanup_deadline.expired():
                    raise self._storage_error(context, state)
                updated = await self._persist_phase(
                    context=context,
                    state=state,
                    terminal=True,
                    enclosing_deadline=cleanup_deadline,
                    write=lambda: self._repository.update_run_result(
                        tenant_id=context.auth.tenant_id,
                        user_id=context.auth.user_id,
                        run_id=run_id,
                        update=AgentRunUpdate(
                            status="stopped",
                            termination_reason=AGENT_RUN_CANCELLED,
                            steps_used=0,
                            tool_calls_used=0,
                            error_code=AGENT_RUN_CANCELLED,
                            latency_ms=_elapsed_ms(self._perf_counter() - started),
                            metadata={"counters_complete": False},
                        ),
                    ),
                )
                await self._record_audit(
                    context=context,
                    run_id=updated.id,
                    action="agent.run.cancelled",
                    status=AuditStatus.DENIED,
                    latency_ms=updated.latency_ms or 0.0,
                    error_code=AGENT_RUN_CANCELLED,
                    metadata={"counters_complete": False},
                )

        cleanup = asyncio.create_task(persist())
        try:
            await _await_owned_cleanup(cleanup)
        except Exception:
            # Only an acknowledged commit proves the terminal; audit failure
            # cannot invalidate it, and uncertain commits must not be retried.
            if state.terminal_phase_succeeded:
                logger.warning(
                    "agent.run.cancel_audit_incomplete",
                    extra={"agent_run_id": run_id, "request_id": context.request_id},
                )
            else:
                logger.error(
                    "agent.run.cancel_persistence_failed",
                    extra={"agent_run_id": run_id, "request_id": context.request_id},
                )
        return state.terminal_phase_succeeded

    async def _mark_failed_after_runtime_exception(
        self,
        *,
        state: _PersistenceState,
        context: AuthenticatedRequestContext,
        run_id: str,
        started: float,
    ) -> None:
        latency_ms = _elapsed_ms(self._perf_counter() - started)
        updated = await self._persist_phase(
            context=context,
            state=state,
            terminal=True,
            write=lambda: self._repository.update_run_result(
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                run_id=run_id,
                update=AgentRunUpdate(
                    status="failed",
                    termination_reason=AGENT_RUN_FAILED,
                    steps_used=0,
                    tool_calls_used=0,
                    error_code=AGENT_RUN_FAILED,
                    latency_ms=latency_ms,
                    metadata=_safe_metadata(
                        {
                            "termination_reason": AGENT_RUN_FAILED,
                            "error_code": AGENT_RUN_FAILED,
                        }
                    ),
                ),
            ),
        )
        await self._record_audit(
            context=context,
            run_id=updated.id,
            action="agent.run.failed",
            status=AuditStatus.FAILURE,
            latency_ms=latency_ms,
            error_code=AGENT_RUN_FAILED,
            metadata=_audit_metadata(updated.metadata, agent_run_id=updated.id),
        )

    async def _update_from_result(
        self,
        *,
        context: AuthenticatedRequestContext,
        run_id: str,
        result: AgentRunResult,
    ) -> AgentRunRecord:
        return await self._repository.update_run_result(
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            run_id=run_id,
            update=AgentRunUpdate(
                status=result.status.value,
                termination_reason=result.termination_reason.value,
                steps_used=result.steps_used,
                tool_calls_used=result.tool_calls_used,
                error_code=result.error_code,
                latency_ms=result.latency_ms,
                metadata=_result_metadata(result),
            ),
        )

    def _config(self, command: AgentRunCommand) -> AgentRunConfig:
        return AgentRunConfig(
            max_steps=command.max_steps or self._default_max_steps,
            max_tool_calls=command.max_tool_calls
            if command.max_tool_calls is not None
            else self._default_max_tool_calls,
            timeout_seconds=command.timeout_seconds or self._default_timeout_seconds,
            repeated_action_threshold=self._repeated_action_threshold,
            input_text=command.input,
            auxiliary_io_timeout_seconds=self._auxiliary_io_timeout_seconds,
        )

    async def _safe_rollback(self) -> bool:
        # Deliver an already-requested cancellation before taking the baseline;
        # an old, already-delivered cancellation count is not a new disconnect.
        await asyncio.sleep(0)
        current = asyncio.current_task()
        cancellations = current.cancelling() if current is not None else 0
        try:
            async with asyncio.timeout(self._persistence_phase_timeout_seconds) as deadline:
                await self._repository.rollback()
            if current is not None and current.cancelling() > cancellations:
                raise asyncio.CancelledError
            if deadline.expired():
                return False
            return True
        except asyncio.CancelledError:
            if current is not None and current.cancelling() > cancellations:
                raise
            return False
        except Exception:
            return False

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        run_id: str,
        action: str,
        status: AuditStatus,
        latency_ms: float,
        error_code: str | None,
        metadata: Mapping[str, object],
    ) -> None:
        await asyncio.sleep(0)
        current = asyncio.current_task()
        cancellations = current.cancelling() if current is not None else 0
        try:
            await await_auxiliary_io(
                self._audit.record(
                    AuditEvent(
                        request_id=context.request_id,
                        trace_id=context.trace_id,
                        tenant_id=context.auth.tenant_id,
                        user_id=context.auth.user_id,
                        action=action,
                        resource=AuditResource(
                            type="agent_run",
                            id=run_id,
                            metadata={"agent_run_id": run_id},
                        ),
                        status=status,
                        latency_ms=latency_ms,
                        error_code=error_code,
                        metadata=dict(metadata),
                    )
                ),
                timeout_seconds=self._auxiliary_io_timeout_seconds,
            )
        except asyncio.CancelledError:
            if current is not None and current.cancelling() > cancellations:
                raise
            # A self-cancelled audit is auxiliary failure, not evidence that an
            # acknowledged business terminal was cancelled.
            error_type = "CancelledError"
        except Exception as exc:
            error_type = type(exc).__name__
        else:
            return
        logger.warning(
            "agent.run.audit_failed",
            extra={
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "agent_run_id": run_id,
                "action": action,
                "audit_error_type": error_type,
            },
        )


def _input_summary(value: str) -> dict[str, object]:
    return {
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _create_metadata(*, command: AgentRunCommand, config: AgentRunConfig) -> dict[str, object]:
    return _safe_metadata(
        {
            **command.metadata,
            "max_steps": config.max_steps,
            "max_tool_calls": config.max_tool_calls,
            "timeout_seconds": config.timeout_seconds,
            "input_length": len(command.input),
        }
    )


def _result_metadata(result: AgentRunResult) -> dict[str, object]:
    return _safe_metadata(
        {
            **dict(result.metadata),
            "safe_counts": {
                "observation_count": len(result.observations),
                "metadata_count": len(result.metadata),
            },
            "termination_reason": result.termination_reason.value,
            "steps_used": result.steps_used,
            "tool_calls_used": result.tool_calls_used,
        }
    )


def _audit_metadata(metadata: Mapping[str, object], *, agent_run_id: str) -> dict[str, object]:
    return _safe_metadata({"agent_run_id": agent_run_id, **dict(metadata)})


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if _is_forbidden_key(key_text):
            continue
        if isinstance(value, Mapping):
            nested = _safe_metadata(value)
            if nested:
                safe[key_text] = nested
        elif isinstance(value, list | tuple):
            list_items = [
                item for item in (_safe_metadata_scalar(item) for item in value) if item is not None
            ][:20]
            if list_items:
                safe[key_text] = list_items
        else:
            safe_value = _safe_metadata_scalar(value)
            if safe_value is not None:
                safe[key_text] = safe_value
    return safe


def _is_forbidden_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return normalized in _FORBIDDEN_METADATA_KEYS or compact in {
        item.replace("_", "") for item in _FORBIDDEN_METADATA_KEYS
    }


def _safe_metadata_scalar(value: object) -> object | None:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        normalized = value.strip()
        if len(normalized) > 200:
            return None
        if _looks_like_absolute_path(normalized) or _looks_like_sensitive_value(normalized):
            return None
        return normalized
    return None


def _looks_like_absolute_path(value: str) -> bool:
    return (
        value.startswith("/")
        or value.startswith("\\\\")
        or bool(re.match(r"^[A-Za-z]:[\\/]", value))
    )


def _looks_like_sensitive_value(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "api_key",
            "authorization:",
            "bearer ",
            "password=",
            "secret=",
            "token=",
        )
    )


def _audit_status(status: str) -> AuditStatus:
    if status == "completed":
        return AuditStatus.SUCCESS
    if status == "stopped":
        return AuditStatus.DENIED
    return AuditStatus.FAILURE


def _lifecycle_action(status: str) -> str:
    if status == "completed":
        return "agent.run.completed"
    if status == "stopped":
        return "agent.run.stopped"
    return "agent.run.failed"


def _elapsed_ms(elapsed_seconds: float) -> float:
    return round(max(elapsed_seconds, 0.0) * 1000, 3)


async def _await_owned_cleanup(task: asyncio.Task[object]) -> None:
    """Keep the request's storage lifetime open through repeated disconnects."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
    if task.cancelled():
        return
    task.result()
