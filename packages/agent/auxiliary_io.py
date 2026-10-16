"""Bound already-best-effort Agent telemetry without weakening required storage.

These guards apply only to auxiliary AuditPort/progress calls. Never use them to
swallow permission, validation, ToolCallRecorder, journal or terminal-store errors.
Cancellation-resistant integrations must provide their own I/O timeouts: Python
cannot kill custom code, and a cancelled telemetry task may outlive its caller.
"""

from __future__ import annotations

import logging
from collections.abc import Coroutine
from typing import Any

from packages.common.auxiliary_io import (
    AUXILIARY_CANCEL_GRACE_SECONDS as AUXILIARY_CANCEL_GRACE_SECONDS,
)
from packages.common.auxiliary_io import (
    AUXILIARY_IO_TIMEOUT_SECONDS as AUXILIARY_IO_TIMEOUT_SECONDS,
)
from packages.common.auxiliary_io import await_auxiliary_io as _await_auxiliary_io
from packages.common.auxiliary_io import validate_auxiliary_timeout as validate_auxiliary_timeout

logger = logging.getLogger(__name__)


async def await_auxiliary_io(
    operation: Coroutine[Any, Any, None],
    *,
    timeout_seconds: float = AUXILIARY_IO_TIMEOUT_SECONDS,
) -> None:
    """Preserve Agent deadlines, cancellation propagation and warning events."""
    await _await_auxiliary_io(
        operation,
        timeout_seconds=timeout_seconds,
        cancellation_logger=logger,
        cancellation_log_event="agent.auxiliary_io.cancel_timeout",
    )
