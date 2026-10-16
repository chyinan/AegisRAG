"""Bound auxiliary asynchronous waits while retaining ownership of late cleanup.

Never use this helper for mandatory persistence, validation or tool execution.
Cancellation-resistant integrations require independently owned resources and
native I/O timeouts; Python cannot forcibly terminate their work.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

AUXILIARY_IO_TIMEOUT_SECONDS = 1.0
AUXILIARY_CANCEL_GRACE_SECONDS = 0.05
_pending_cleanup: set[asyncio.Task[None]] = set()


class AuxiliaryOperationCancelledError(Exception):
    """An owned auxiliary child cancelled itself; its caller was not cancelled."""


async def await_auxiliary_io(
    operation: Coroutine[Any, Any, None],
    *,
    timeout_seconds: float = AUXILIARY_IO_TIMEOUT_SECONDS,
    operation_cancelled_as_error: bool = False,
    cancellation_logger: logging.Logger = logger,
    cancellation_log_event: str = "auxiliary_io.cancel_timeout",
) -> None:
    """Wait for a hook, followed by bounded cancellation cleanup if necessary.

    Caller cancellation always propagates from an await, even during cleanup.
    Optional child-cancellation conversion happens only when inspecting a
    completed child, never by guessing from the caller's cancellation count.
    """
    if not math.isfinite(timeout_seconds):
        operation.close()
        raise ValueError("auxiliary timeout must be finite")
    if timeout_seconds <= 0:
        operation.close()
        raise TimeoutError
    task = asyncio.create_task(operation)
    try:
        done, _ = await asyncio.wait(
            {task},
            timeout=timeout_seconds,
        )
        if not done:
            raise TimeoutError
        if operation_cancelled_as_error and task.cancelled():
            raise AuxiliaryOperationCancelledError
        task.result()
    finally:
        if not task.done():
            task.cancel()
            try:
                await asyncio.wait({task}, timeout=AUXILIARY_CANCEL_GRACE_SECONDS)
            finally:
                if not task.done():
                    # Never print a late exception: it may contain input/output.
                    _pending_cleanup.add(task)
                    task.add_done_callback(_consume_result)
                    cancellation_logger.warning(cancellation_log_event)
                else:
                    _consume_result(task)
        else:
            _consume_result(task)


def _consume_result(task: asyncio.Task[None]) -> None:
    _pending_cleanup.discard(task)
    if not task.cancelled():
        task.exception()


def validate_auxiliary_timeout(timeout_seconds: float) -> float:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("auxiliary_io_timeout_seconds must be finite and positive")
    return timeout_seconds
