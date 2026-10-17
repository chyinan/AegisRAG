"""Bounded, content-free diagnostics for durable review probes."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable
from pathlib import Path
from time import monotonic
from typing import Any, TypeVar

from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.common.errors import DomainError

T = TypeVar("T")
DIAGNOSTIC_CODES = frozenset(
    {
        "DURABLE_STORAGE_TIMEOUT",
        "DURABLE_STORAGE_UNAVAILABLE",
        "DURABLE_LEASE_LOST",
        "DURABLE_RESUME_CONFLICT",
        "DURABLE_GRAPH_CHECKPOINT_FAILED",
        "DURABLE_GRAPH_STATE_UNSAFE",
        "DURABLE_VALIDATION_TIMEOUT",
        "DURABLE_APPROVAL_STALE",
        "DURABLE_CLOCK_REGRESSION",
        "AGENT_TIMEOUT",
    }
)


def _code(value: object) -> str:
    if isinstance(value, str) and value in DIAGNOSTIC_CODES:
        return value
    return "UNCLASSIFIED"


class ObservedDurableStore(SqlAlchemyDurableStore):
    """Record operation timing and safe state, never arguments or exception text."""

    def __init__(self, sessions: Any) -> None:
        super().__init__(sessions)
        self.observations: deque[dict[str, object]] = deque(maxlen=24)

    async def _observed(self, name: str, operation: Awaitable[T], snapshot: Any) -> T:
        event: dict[str, object] = {"operation": name, "outcome": "pending"}
        if getattr(snapshot, "phase", None) in {"decision", "tool", "validation", "done"}:
            event["phase"] = snapshot.phase
        if type(getattr(snapshot, "revision", None)) is int:
            event["revision"] = snapshot.revision
        self.observations.append(event)
        started = monotonic()
        try:
            result = await operation
            event["outcome"] = "completed"
            return result
        except DomainError as exc:
            event["outcome"] = _code(exc.code)
            raise
        except asyncio.CancelledError:
            event["outcome"] = "cancelled"
            raise
        except Exception:
            event["outcome"] = "exception"
            raise
        finally:
            event["duration_ms"] = round((monotonic() - started) * 1000, 3)

    async def acquire(self, snapshot: Any, **kwargs: Any) -> Any:
        return await self._observed("acquire", super().acquire(snapshot, **kwargs), snapshot)

    async def renew(self, snapshot: Any, **kwargs: Any) -> Any:
        return await self._observed("renew", super().renew(snapshot, **kwargs), snapshot)

    async def save(self, previous: Any, snapshot: Any, **kwargs: Any) -> Any:
        return await self._observed("save", super().save(previous, snapshot, **kwargs), snapshot)

    async def graph_get(self, snapshot: Any, checkpoint_id: Any = None) -> Any:
        return await self._observed(
            "graph_get", super().graph_get(snapshot, checkpoint_id), snapshot
        )

    async def graph_put(self, snapshot: Any, **kwargs: Any) -> Any:
        return await self._observed("graph_put", super().graph_put(snapshot, **kwargs), snapshot)

    async def graph_writes(self, snapshot: Any, **kwargs: Any) -> Any:
        return await self._observed(
            "graph_writes", super().graph_writes(snapshot, **kwargs), snapshot
        )


def _resume_state(task: asyncio.Task[Any]) -> dict[str, object]:
    if task.cancelled():
        return {"task": "cancelled"}
    if task.done():
        try:
            result = task.result()
        except DomainError as exc:
            return {"task": "failed", "error_code": _code(exc.code)}
        except Exception:
            return {"task": "failed", "error_code": "UNCLASSIFIED"}
        state: dict[str, object] = {"task": "finished"}
        if getattr(result, "status", None) in {
            "running",
            "awaiting_approval",
            "completed",
            "failed",
            "stopped",
            "cancelled",
            "intervention_required",
        }:
            state["status"] = result.status
        if getattr(result, "error_code", None) is not None:
            state["error_code"] = _code(result.error_code)
        return state
    return {
        "task": "pending",
        "stack": [
            (Path(frame.f_code.co_filename).name, frame.f_code.co_name, frame.f_lineno)
            for frame in task.get_stack(limit=3)
        ],
    }


async def await_handler_started(
    task: asyncio.Task[Any],
    entered: asyncio.Event,
    *,
    timeout: float,
    lease_seconds: float,
    store: ObservedDurableStore | None = None,
) -> None:
    """Fail early on a failed resume instead of hiding it behind an event timeout."""
    waiter = asyncio.create_task(entered.wait())
    try:
        await asyncio.wait({task, waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if entered.is_set():
            return
        detail = _resume_state(task)
        detail["lease_seconds"] = lease_seconds
        detail["renew_deadline_seconds"] = min(lease_seconds / 3, 5.0)
        if store is not None:
            detail["store_operations"] = list(store.observations)
        raise AssertionError(f"handler_not_started: {detail}")
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
