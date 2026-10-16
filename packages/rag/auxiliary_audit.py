"""Opt-in finite waits for RAG audit hooks that are already best-effort."""

from __future__ import annotations

import logging

from packages.common.audit import AuditEvent, AuditPort
from packages.common.auxiliary_io import (
    AUXILIARY_IO_TIMEOUT_SECONDS,
    AuxiliaryOperationCancelledError,
    await_auxiliary_io,
    validate_auxiliary_timeout,
)

logger = logging.getLogger(__name__)


class BoundedBestEffortAuditPort:
    """Decorate an explicitly resource-owning audit port, never a request session.

    The delegate must own its resources through completion/cancellation cleanup,
    including a possible late result. Production uses SessionFactoryAuditPort.
    Selecting this decorator is an explicit best-effort policy: never pass it to
    successful nonstream Query audit or other mandatory persistence. Legacy
    programmatic service constructors do not wrap their supplied ports.
    """

    def __init__(
        self,
        *,
        owned_audit: AuditPort,
        timeout_seconds: float = AUXILIARY_IO_TIMEOUT_SECONDS,
    ) -> None:
        self._owned_audit = owned_audit
        self._timeout_seconds = validate_auxiliary_timeout(timeout_seconds)

    async def record(self, event: AuditEvent) -> None:
        try:
            await await_auxiliary_io(
                self._owned_audit.record(event),
                timeout_seconds=self._timeout_seconds,
                operation_cancelled_as_error=True,
                cancellation_logger=logger,
                cancellation_log_event="rag.auxiliary_audit.cancel_timeout",
            )
        except Exception as exc:
            # Caller CancelledError is a BaseException and is never swallowed.
            logger.warning(
                "rag.auxiliary_audit.audit_failed",
                extra={
                    "request_id": event.request_id,
                    "trace_id": event.trace_id,
                    "audit_action": event.action,
                    "audit_status": event.status.value,
                    "audit_error_type": (
                        "CancelledError"
                        if isinstance(exc, AuxiliaryOperationCancelledError)
                        else type(exc).__name__
                    ),
                },
            )
