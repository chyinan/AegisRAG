"""Opt-in durable polling worker, with a fresh trusted context per owned run.

The database is the queue. An API or worker process may exit between ticks;
unclaimed and expired-lease background work remains discoverable. This module
never restores permissions, sessions or bearer tokens from a business snapshot.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from packages.agent.durable.dto import DurableRunResponse
from packages.agent.durable.service import DurableAgentService
from packages.agent.runtime import _await_with_timeout
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.common.tenant_context import tenant_scope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DurableWorkerOwner:
    """An operator-authorized polling scope, never an authentication assertion."""

    tenant_id: str
    user_id: str


TrustedContextResolver = Callable[[DurableWorkerOwner, str], Awaitable[AuthenticatedRequestContext]]


class DurableBackgroundScheduler:
    def __init__(
        self,
        *,
        store: SqlAlchemyDurableStore,
        service: DurableAgentService,
        owners: Sequence[DurableWorkerOwner],
        resolve_context: TrustedContextResolver,
        batch_size: int = 20,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not owners or not 1 <= batch_size <= 100:
            raise ValueError("explicit owner scopes and a bounded batch are required")
        if not service._background_enabled or service._context_revalidator is None:
            raise ValueError("background service requires current-context revalidation")
        self._store = store
        self._service = service
        self._owners = tuple(owners)
        self._resolve_context = resolve_context
        self._batch_size = batch_size
        self._clock = wall_clock

    async def run_once(self) -> tuple[DurableRunResponse, ...]:
        """Run a bounded batch. Approval waits are never selected or approved here."""
        responses: list[DurableRunResponse] = []
        for owner in self._owners:
            with tenant_scope(owner.tenant_id):
                candidates = await _await_with_timeout(
                    self._store.ready_background(
                        tenant_id=owner.tenant_id,
                        user_id=owner.user_id,
                        now=self._clock(),
                        limit=self._batch_size,
                    ),
                    timeout_seconds=5.0,
                )
                for candidate in candidates:
                    try:

                        async def resolve(
                            current_owner: DurableWorkerOwner = owner,
                            run_id: str = candidate.run_id,
                        ) -> AuthenticatedRequestContext:
                            return await self._resolve_context(current_owner, run_id)

                        context = await _await_with_timeout(resolve(), timeout_seconds=5.0)
                        # The resolver is an explicit trusted application boundary.
                        # Never substitute snapshot permissions if it cannot resolve.
                        if (context.auth.tenant_id, context.auth.user_id) != (
                            owner.tenant_id,
                            owner.user_id,
                        ):
                            logger.warning("agent.durable.background_context_unavailable")
                            continue
                        responses.append(
                            await self._service.run_background(
                                context=context,
                                run_id=candidate.run_id,
                                expected_revision=candidate.revision,
                            )
                        )
                    except DomainError as exc:
                        # Expected authorization/lease conflicts leave the stored
                        # state authoritative. Do not expose private payloads/log SQL.
                        logger.warning(
                            "agent.durable.background_deferred", extra={"error_code": exc.code}
                        )
                    except TimeoutError:
                        logger.warning("agent.durable.background_dependency_timeout")
        return tuple(responses)
