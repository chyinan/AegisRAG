"""Sequential durable execution with business-owned safety and recovery semantics.

Only read-only tools may replay an uncertain dispatch. A completed ledger result
is reused, never dispatched again. There is no exactly-once side-effect claim.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID, uuid4

from packages.agent.approval import ToolApprovalGrant, argument_digest
from packages.agent.dto import ToolDefinition, ToolExecutionResult, ToolInvocationStatus
from packages.agent.durable.dto import (
    TERMINAL_STATUSES,
    ApprovalRequest,
    DurableActiveBudget,
    DurableBackgroundDispatch,
    DurableCancelCommand,
    DurableResumeCommand,
    DurableRunResponse,
    DurableSnapshot,
    DurableStartCommand,
    DurableStartup,
    ToolLedgerEntry,
    decision_digest,
    durable_error,
    keyed_run_id,
    start_command_fingerprint,
    validate_private_arguments,
)
from packages.agent.durable.evidence import EvidenceRevalidator
from packages.agent.policies import has_tool_permission
from packages.agent.runtime import (
    AgentActionType,
    AgentRunConfig,
    AgentRunResult,
    AgentRuntime,
    _AgentExecution,
    _await_with_timeout,
    _cancel_task,
    _summarize_tool_result,
)
from packages.agent.storage.durable import KeyedCreateResult, SqlAlchemyDurableStore
from packages.auth.policies import has_agent_run_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError

logger = logging.getLogger(__name__)
DurableRuntimeFactory = Callable[[AgentRunConfig, str], AgentRuntime]
ContextRevalidator = Callable[[AuthenticatedRequestContext], Awaitable[AuthenticatedRequestContext]]


class DurableAgentService:
    def __init__(
        self,
        *,
        store: SqlAlchemyDurableStore,
        runtime_factory: DurableRuntimeFactory,
        evidence_revalidator: EvidenceRevalidator,
        backend: Literal["native", "langgraph"] = "native",
        default_max_steps: int = 8,
        default_max_tool_calls: int = 6,
        default_timeout_seconds: float = 120,
        repeated_action_threshold: int = 3,
        retention_seconds: float = 86400,
        lease_seconds: float = 30,
        wall_clock: Callable[[], float] = time.time,
        context_revalidator: ContextRevalidator | None = None,
        audit: AuditPort | None = None,
        background_enabled: bool = False,
    ) -> None:
        if backend not in ("native", "langgraph"):
            raise ValueError("unsupported durable backend")
        if not 0 < lease_seconds <= 60 or retention_seconds < 120:
            raise ValueError("invalid lease or retention duration")
        self._store = store
        self._runtime_factory = runtime_factory
        self._evidence = evidence_revalidator
        self._backend = backend
        self._defaults = (default_max_steps, default_max_tool_calls, default_timeout_seconds)
        self._repeat_threshold = repeated_action_threshold
        self._retention_seconds = retention_seconds
        self._lease_seconds = lease_seconds
        self._clock = wall_clock
        self._context_revalidator = context_revalidator
        self._audit = audit
        self._background_enabled = background_enabled
        self._creation_cleanup_tasks: set[asyncio.Task[None]] = set()

    async def start(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: DurableStartCommand,
    ) -> DurableRunResponse:
        self._authorize(context)
        if command.execution_mode == "background" and not self._background_enabled:
            raise durable_error("DURABLE_BACKGROUND_UNCONFIGURED", status_code=503)
        startup = None
        run_id = str(uuid4())
        if command.idempotency_key is not None:
            run_id = keyed_run_id(
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                key=command.idempotency_key,
            )
            startup = DurableStartup(command_fingerprint=start_command_fingerprint(command))
            # Read before constructing any runtime or resolving mutable defaults.
            # Existing attempts keep the original backend, deadline and budgets.
            existing = await self._lookup_keyed(context, run_id)
            if existing is not None:
                return await self._existing_start(context, existing, startup)
        # Raw user input is necessary for a new process to resume. Reject obvious
        # secrets rather than treating the application database as a credential vault.
        validate_private_arguments({"input": command.input})
        now = self._clock()
        config = AgentRunConfig(
            max_steps=command.max_steps or self._defaults[0],
            max_tool_calls=(
                command.max_tool_calls if command.max_tool_calls is not None else self._defaults[1]
            ),
            timeout_seconds=command.timeout_seconds or self._defaults[2],
            repeated_action_threshold=self._repeat_threshold,
            input_text=command.input,
        )
        if config.timeout_seconds > self._retention_seconds:
            raise durable_error("DURABLE_BUDGET_EXCEEDS_RETENTION", status_code=422)
        snapshot = DurableSnapshot(
            run_id=run_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            request_id=context.request_id,
            trace_id=context.trace_id,
            backend=self._backend,
            startup=startup,
            active_budget=(
                DurableActiveBudget(
                    active_since_at=now if command.execution_mode == "inline" else None,
                    approval_timeout_seconds=command.approval_timeout_seconds or 3600,
                )
                if command.budget_mode == "active"
                else None
            ),
            dispatch=(
                DurableBackgroundDispatch() if command.execution_mode == "background" else None
            ),
            config=config,
            started_at=now,
            deadline_at=now + config.timeout_seconds,
            last_observed_at=now,
            expires_at=now + self._retention_seconds,
            approval_tools=command.approval_tools,
        )
        registry = self._runtime_factory(config, snapshot.run_id)._registry
        if any(name not in registry.registered_tool_names for name in command.approval_tools):
            raise durable_error("DURABLE_APPROVAL_TOOL_UNKNOWN", status_code=422)
        if startup is not None:
            return await self._start_keyed(context=context, snapshot=snapshot)
        try:
            await _await_with_timeout(
                self._store.purge_expired(tenant_id=context.auth.tenant_id, now=now),
                timeout_seconds=max(snapshot.deadline_at - self._clock(), 0),
            )
            await _await_with_timeout(
                self._store.create(snapshot),
                timeout_seconds=max(snapshot.deadline_at - self._clock(), 0),
            )
            await self._audit_transition(context, snapshot, "agent.durable.created")
        except asyncio.CancelledError:
            execution = _DurableExecution(
                service=self,
                context=context,
                snapshot=snapshot,
                token=str(uuid4()),
                created_here=True,
            )
            await self._cancel_after_disconnect(execution)
            raise
        except TimeoutError as exc:
            raise durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503) from exc
        if snapshot.dispatch is not None:
            return DurableRunResponse.from_snapshot(snapshot)
        return await self._drive(context=context, snapshot=snapshot, created_here=True)

    async def _lookup_keyed(
        self, context: AuthenticatedRequestContext, run_id: str
    ) -> DurableSnapshot | None:
        try:
            return await _await_with_timeout(
                self._store.lookup_keyed(
                    tenant_id=context.auth.tenant_id, user_id=context.auth.user_id, run_id=run_id
                ),
                timeout_seconds=5.0,
            )
        except TimeoutError as exc:
            raise durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503) from exc

    async def _existing_start(
        self,
        context: AuthenticatedRequestContext,
        snapshot: DurableSnapshot,
        startup: DurableStartup,
    ) -> DurableRunResponse:
        if self._clock() >= snapshot.expires_at:
            # Use normal expiry/purge handling, before comparing private data.
            return await self.get(context=context, run_id=snapshot.run_id)
        if snapshot.startup != startup:
            raise durable_error("DURABLE_START_KEY_CONFLICT")
        # A duplicate is a read, not a lease acquisition, approval or resume.
        # In particular completed results require the normal current ACL checks.
        return await self.get(context=context, run_id=snapshot.run_id)

    async def recover(
        self,
        *,
        context: AuthenticatedRequestContext,
        idempotency_key: UUID,
    ) -> DurableRunResponse:
        self._authorize(context)
        if idempotency_key.version != 4:
            raise durable_error("DURABLE_START_KEY_INVALID", status_code=422)
        run_id = keyed_run_id(
            tenant_id=context.auth.tenant_id, user_id=context.auth.user_id, key=idempotency_key
        )
        snapshot = await self._lookup_keyed(context, run_id)
        if snapshot is None:
            raise durable_error("DURABLE_RUN_NOT_FOUND", status_code=404)
        return await self.get(context=context, run_id=run_id)

    async def _start_keyed(
        self, *, context: AuthenticatedRequestContext, snapshot: DurableSnapshot
    ) -> DurableRunResponse:
        token = str(uuid4())
        creation: asyncio.Task[KeyedCreateResult] | None = None
        try:
            await _await_with_timeout(
                self._store.purge_expired(tenant_id=snapshot.tenant_id, now=self._clock()),
                timeout_seconds=max(snapshot.deadline_at - self._clock(), 0),
            )
            # Only storage runs inside this child task. Even a cancellation-
            # resistant late commit has no continuation that can launch tools.
            creation = asyncio.create_task(
                self._store.create_keyed(
                    snapshot, token=token, now=self._clock(), lease_seconds=self._lease_seconds
                )
            )

            async def wait_creation() -> KeyedCreateResult:
                assert creation is not None
                return await asyncio.shield(creation)

            def cancel_creation() -> None:
                assert creation is not None
                creation.cancel()

            result = await _await_with_timeout(
                wait_creation(),
                timeout_seconds=max(snapshot.deadline_at - self._clock(), 0),
                on_cancel=cancel_creation,
            )
            if result.inserted:
                await self._audit_transition(context, snapshot, "agent.durable.created")
        except BaseException as exc:
            if creation is not None and not creation.done():
                # A response/connection failure cannot prove whether COMMIT ran.
                # Reconcile now AND after a late storage task exits, always with
                # this attempt's random token, never an unclaimed-row fallback.
                creation.add_done_callback(
                    lambda task: self._schedule_creation_cleanup(
                        task, context=context, snapshot=snapshot, token=token
                    )
                )
            await self._cancel_keyed_creation(context=context, snapshot=snapshot, token=token)
            if isinstance(exc, TimeoutError):
                raise durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503) from exc
            raise
        if not result.inserted:
            assert snapshot.startup is not None
            return await self._existing_start(context, result.snapshot, snapshot.startup)
        if snapshot.dispatch is not None:
            await _await_with_timeout(
                self._store.release(snapshot, token=token), timeout_seconds=1.0
            )
            return DurableRunResponse.from_snapshot(snapshot)
        return await self._drive(context=context, snapshot=snapshot, initial_token=token)

    def _schedule_creation_cleanup(
        self,
        creation: asyncio.Task[KeyedCreateResult],
        *,
        context: AuthenticatedRequestContext,
        snapshot: DurableSnapshot,
        token: str,
    ) -> None:
        # Consume any storage exception without logging SQL or private payloads.
        if not creation.cancelled():
            creation.exception()
        task = asyncio.create_task(
            self._cancel_keyed_creation(context=context, snapshot=snapshot, token=token)
        )
        self._creation_cleanup_tasks.add(task)
        task.add_done_callback(self._creation_cleanup_tasks.discard)

    async def _cancel_keyed_creation(
        self,
        *,
        context: AuthenticatedRequestContext,
        snapshot: DurableSnapshot,
        token: str,
    ) -> None:
        async def persist() -> None:
            while True:
                try:
                    current = await self._store.load(
                        tenant_id=snapshot.tenant_id,
                        user_id=snapshot.user_id,
                        run_id=snapshot.run_id,
                    )
                except DomainError as exc:
                    if exc.code in ("DURABLE_RUN_NOT_FOUND", "DURABLE_RUN_EXPIRED"):
                        return
                    raise
                if current.status in TERMINAL_STATUSES:
                    return
                if not await self._store.owns(current, token=token, allow_unclaimed=False):
                    return
                try:
                    saved = await self._store.cancel(
                        current, now=self._clock(), ownership_token=token, allow_unclaimed=False
                    )
                except DomainError as exc:
                    if exc.code == "DURABLE_RESUME_CONFLICT":
                        continue
                    raise
                await self._audit_transition(context, saved, "agent.durable.cancelled")
                return

        async def bounded_cleanup() -> None:
            try:
                await _await_with_timeout(persist(), timeout_seconds=1.0)
            except Exception as exc:
                # Best effort, never report that a fence committed when it did
                # not. The original error remains the caller's result.
                logger.warning(
                    "agent.durable.creation_cleanup_failed",
                    extra={"error_type": type(exc).__name__},
                )

        cleanup = asyncio.create_task(bounded_cleanup())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()

    async def get(
        self,
        *,
        context: AuthenticatedRequestContext,
        run_id: str,
    ) -> DurableRunResponse:
        snapshot = await self._load(context, run_id)
        if snapshot.status == "completed":
            # Cached answers are not an authorization bypass after ACL revocation.
            # Reads get a fresh bounded validation window without renewing the
            # finished run's original execution deadline.
            runtime = self._runtime_factory(snapshot.config, snapshot.run_id)
            try:
                await _await_with_timeout(
                    self._revalidate(context, snapshot, runtime), timeout_seconds=5.0
                )
            except TimeoutError as exc:
                raise durable_error("DURABLE_VALIDATION_TIMEOUT", status_code=503) from exc
        return DurableRunResponse.from_snapshot(snapshot)

    async def resume(
        self,
        *,
        context: AuthenticatedRequestContext,
        run_id: str,
        command: DurableResumeCommand,
    ) -> DurableRunResponse:
        snapshot = await self._load(context, run_id)
        if snapshot.revision != command.expected_revision:
            raise durable_error("DURABLE_RESUME_CONFLICT")
        if snapshot.status in TERMINAL_STATUSES:
            raise durable_error("DURABLE_RUN_TERMINAL")
        if snapshot.status == "awaiting_approval":
            approval = snapshot.approval
            if (
                approval is None
                or command.approval_id != approval.approval_id
                or command.approve is None
                or self._clock() >= approval.expires_at
            ):
                raise durable_error("DURABLE_APPROVAL_STALE")
            if (
                snapshot.decision is None
                or decision_digest(snapshot.decision) != approval.action_digest
            ):
                raise durable_error("DURABLE_APPROVAL_STALE")
            runtime = self._runtime_factory(snapshot.config, snapshot.run_id)
            try:
                definition = await _await_with_timeout(
                    self._definition(runtime, snapshot, context),
                    timeout_seconds=min(self._remaining(snapshot), 5.0),
                )
            except TimeoutError as exc:
                raise durable_error("DURABLE_VALIDATION_TIMEOUT", status_code=503) from exc
            if _definition_digest(definition) != approval.definition_digest:
                raise durable_error("DURABLE_APPROVAL_STALE")
        elif command.approval_id is not None:
            raise durable_error("DURABLE_APPROVAL_STALE")
        return await self._drive(
            context=context,
            snapshot=snapshot,
            approval=command,
            queue_only=snapshot.dispatch is not None,
        )

    def _remaining(self, snapshot: DurableSnapshot) -> float:
        """Remaining allowance for this window, without granting a fresh budget."""
        now = self._clock()
        budget = snapshot.active_budget
        if budget is None:
            return max(snapshot.deadline_at - now, 0)
        elapsed = max(now - budget.active_since_at, 0) if budget.active_since_at is not None else 0
        return max(
            min(
                snapshot.config.timeout_seconds - budget.seconds_used - elapsed,
                snapshot.expires_at - now,
            ),
            0,
        )

    async def run_background(
        self, *, context: AuthenticatedRequestContext, run_id: str, expected_revision: int
    ) -> DurableRunResponse:
        """Trusted scheduler entry. A freshly resolved context is required per dispatch."""
        if not self._background_enabled or self._context_revalidator is None:
            raise durable_error("DURABLE_BACKGROUND_UNCONFIGURED", status_code=503)
        snapshot = await self._load(context, run_id)
        if snapshot.dispatch is None or snapshot.status != "running":
            raise durable_error("DURABLE_BACKGROUND_NOT_READY")
        if snapshot.revision != expected_revision:
            raise durable_error("DURABLE_RESUME_CONFLICT")
        return await self._drive(context=context, snapshot=snapshot)

    async def cancel(
        self,
        *,
        context: AuthenticatedRequestContext,
        run_id: str,
        expected_revision: int | None = None,
        command: DurableCancelCommand | None = None,
    ) -> DurableRunResponse:
        snapshot = await self._load(context, run_id)
        revision = command.expected_revision if command is not None else expected_revision
        if revision is None or snapshot.revision != revision:
            raise durable_error("DURABLE_RESUME_CONFLICT")
        if snapshot.status in TERMINAL_STATUSES:
            raise durable_error("DURABLE_RUN_TERMINAL")
        try:
            saved = await _await_with_timeout(
                self._store.cancel(snapshot, now=self._clock()),
                timeout_seconds=5.0,
            )
        except TimeoutError as exc:
            raise durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503) from exc
        await self._audit_transition(context, saved, "agent.durable.cancelled")
        return DurableRunResponse.from_snapshot(saved)

    async def _load(self, context: AuthenticatedRequestContext, run_id: str) -> DurableSnapshot:
        self._authorize(context)
        try:
            value = await _await_with_timeout(
                self._store.load(
                    tenant_id=context.auth.tenant_id, user_id=context.auth.user_id, run_id=run_id
                ),
                timeout_seconds=5.0,
            )
        except TimeoutError as exc:
            raise durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503) from exc
        if self._clock() >= value.expires_at:
            try:
                await _await_with_timeout(
                    self._store.purge_expired(tenant_id=context.auth.tenant_id, now=self._clock()),
                    timeout_seconds=5.0,
                )
            except TimeoutError as exc:
                raise durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503) from exc
            raise durable_error("DURABLE_RUN_EXPIRED", status_code=410)
        return value

    async def _audit_transition(
        self,
        context: AuthenticatedRequestContext,
        snapshot: DurableSnapshot,
        action: str,
        *,
        approval_ref: str | None = None,
    ) -> None:
        if self._audit is None:
            return
        status = (
            AuditStatus.FAILURE
            if snapshot.status in ("failed", "intervention_required")
            else AuditStatus.DENIED
            if snapshot.status in ("cancelled", "stopped")
            else AuditStatus.SUCCESS
        )
        try:
            await _await_with_timeout(
                self._audit.record(
                    AuditEvent(
                        request_id=context.request_id,
                        trace_id=context.trace_id,
                        tenant_id=snapshot.tenant_id,
                        user_id=snapshot.user_id,
                        action=action,
                        resource=AuditResource(type="agent_run", id=snapshot.run_id),
                        status=status,
                        error_code=snapshot.error_code,
                        latency_ms=max(self._clock() - snapshot.started_at, 0) * 1000,
                        metadata={
                            "durable_status": snapshot.status,
                            "revision": snapshot.revision,
                            "steps_used": snapshot.steps_used,
                            "tool_calls_used": snapshot.tool_calls_used,
                            "approval_ref": approval_ref,
                        },
                    )
                ),
                timeout_seconds=min(max(self._remaining(snapshot), 0.01), 0.5),
            )
        except Exception as exc:
            logger.warning("agent.durable.audit_failed", extra={"error_type": type(exc).__name__})

    @staticmethod
    def _authorize(context: AuthenticatedRequestContext) -> None:
        if not has_agent_run_permission(context.auth):
            raise durable_error("AGENT_RUN_FORBIDDEN", status_code=403)

    async def _definition(
        self,
        runtime: AgentRuntime,
        snapshot: DurableSnapshot,
        context: AuthenticatedRequestContext,
    ) -> ToolDefinition:
        assert snapshot.decision is not None and snapshot.decision.tool_name is not None
        definition = await runtime._registry.get(name=snapshot.decision.tool_name, context=context)
        if not has_tool_permission(context.auth, definition.permission):
            raise durable_error("TOOL_PERMISSION_DENIED", status_code=403)
        if definition.durable_replay_policy == "unsupported":
            raise durable_error("DURABLE_TOOL_UNSUPPORTED", status_code=422)
        validate_private_arguments(snapshot.decision.arguments)
        return definition

    async def _revalidate(
        self,
        context: AuthenticatedRequestContext,
        snapshot: DurableSnapshot,
        runtime: AgentRuntime,
    ) -> tuple[AuthenticatedRequestContext, _AgentExecution, dict[str, str]]:
        if self._context_revalidator is not None:
            context = await self._context_revalidator(context)
        self._authorize(context)
        if (context.auth.tenant_id, context.auth.user_id) != (snapshot.tenant_id, snapshot.user_id):
            raise durable_error("DURABLE_IDENTITY_CHANGED", status_code=403)
        for observation in snapshot.observations:
            definition = await runtime._registry.get(name=observation.tool_name, context=context)
            if not has_tool_permission(context.auth, definition.permission):
                raise durable_error("TOOL_PERMISSION_DENIED", status_code=403)
        evidence = await self._evidence.rehydrate(
            context=context,
            observations=snapshot.observations,
            fingerprints=snapshot.evidence_fingerprints,
        )
        execution = runtime._new_execution(context)
        now = self._clock()
        if now < snapshot.last_observed_at:
            raise durable_error("DURABLE_CLOCK_REGRESSION")
        execution.started -= (
            snapshot.config.timeout_seconds - self._remaining(snapshot)
            if snapshot.active_budget
            else max(now - snapshot.started_at, 0)
        )
        # Monotonic deadlines are reconstructed only from the remaining original
        # wall-clock budget, never from a fresh full timeout.
        execution.deadline = runtime._perf_counter() + self._remaining(snapshot)
        execution.steps_used = snapshot.steps_used
        execution.tool_calls_used = snapshot.tool_calls_used
        execution.observations = list(evidence.observations)
        execution.decision = snapshot.decision
        execution.repeated_detector.restore(snapshot.repeated_counts)
        return context, execution, evidence.fingerprints

    async def _drive(
        self,
        *,
        context: AuthenticatedRequestContext,
        snapshot: DurableSnapshot,
        approval: DurableResumeCommand | None = None,
        created_here: bool = False,
        initial_token: str | None = None,
        queue_only: bool = False,
    ) -> DurableRunResponse:
        token = initial_token or str(uuid4())
        execution = _DurableExecution(
            service=self, context=context, snapshot=snapshot, token=token, created_here=created_here
        )
        try:
            # New keyed runs already own the lease inserted with their rows.
            # Verify it is still live; never reacquire as an unrelated worker.
            claim = self._store.renew if initial_token is not None else self._store.acquire
            claimed = await _await_with_timeout(
                claim(
                    snapshot,
                    token=token,
                    now=self._clock(),
                    lease_seconds=self._lease_seconds,
                    **(
                        {"activate_budget": not queue_only}
                        if snapshot.active_budget and initial_token is None
                        else {}
                    ),
                ),
                timeout_seconds=(
                    5.0
                    if snapshot.active_budget
                    else min(max(snapshot.deadline_at - self._clock(), 0.01), 5.0)
                ),
            )
            if isinstance(claimed, DurableSnapshot):
                snapshot = claimed
                execution.snapshot = claimed
        except asyncio.CancelledError:
            await self._cancel_after_disconnect(execution)
            raise
        except TimeoutError as exc:
            await self._cancel_after_disconnect(execution)
            raise durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503) from exc
        # The unclaimed revision-zero fallback is only for interrupted creation/
        # acquisition. After a confirmed claim, even this run's creator must
        # present its token; a committed release permanently ends that authority.
        execution.created_here = False
        heartbeat = asyncio.create_task(execution.heartbeat())

        async def run_body() -> None:
            if self._clock() < snapshot.last_observed_at:
                await execution.stop("DURABLE_CLOCK_REGRESSION")
            elif snapshot.status == "awaiting_approval" and approval is not None:
                assert snapshot.approval is not None
                if not approval.approve:
                    await execution.stop("DURABLE_APPROVAL_REJECTED", status="cancelled")
                else:
                    updates: dict[str, object] = {
                        "status": "running",
                        "approved_action_digest": snapshot.approval.action_digest,
                        "approved_definition_digest": snapshot.approval.definition_digest,
                        "approval": None,
                    }
                    if snapshot.active_budget is not None:
                        updates["active_budget"] = snapshot.active_budget.model_copy(
                            update={
                                "active_since_at": None if queue_only else self._clock(),
                            }
                        )
                    await execution.save(
                        snapshot.model_copy(
                            update=updates,
                        )
                    )
            if queue_only:
                return
            if execution.snapshot.status == "running":
                if snapshot.backend == "langgraph":
                    from packages.agent.adapters.durable_langgraph import run_durable_graph

                    await run_durable_graph(execution)
                else:
                    # Each iteration durably advances one bounded business phase.
                    while execution.snapshot.status == "running":
                        await execution.advance()

        async def response(*, reconcile: bool = False) -> DurableRunResponse:
            if not reconcile and execution.snapshot.status != "completed":
                return DurableRunResponse.from_snapshot(execution.snapshot)
            try:
                # Every completed response is an owned read with current ACL/
                # evidence checks, even when another owner completed the row
                # observed by advance() or timeout cleanup before our heartbeat.
                return await _await_with_timeout(
                    self.get(context=context, run_id=snapshot.run_id), timeout_seconds=1.0
                )
            except TimeoutError:
                raise durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503) from None

        business = asyncio.create_task(run_body())
        cancelled = False
        try:
            done, _ = await asyncio.wait(
                {business, heartbeat},
                timeout=self._remaining(snapshot),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done or execution.revocation_error is not None:
                # A failed renewal revokes this process immediately. A normal
                # heartbeat exit means a terminal save won the race while
                # audit/framework cleanup is still in progress.
                execution.revoke()
                await _cancel_task(business)
                failure: BaseException | None = execution.revocation_error
                if heartbeat.done() and not heartbeat.cancelled():
                    failure = heartbeat.exception() or failure
                current = await response(reconcile=True)
                if current.status in TERMINAL_STATUSES:
                    return current
                if failure is not None:
                    raise failure
                raise durable_error("DURABLE_LEASE_LOST")
            if business in done:
                try:
                    business.result()
                except TimeoutError:
                    execution.revoke()
                    await self._terminal_cleanup(execution, cancelled=False)
                return await response()
            execution.revoke()
            await _cancel_task(business)
            await self._terminal_cleanup(execution, cancelled=False)
            return await response()
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            execution.revoke()
            releasing = False

            # Cleanup has its own short bound. Shielding it must not consume a
            # disconnect: preserve cancellation after children/release settle.
            async def cleanup() -> None:
                nonlocal releasing
                await asyncio.gather(_cancel_task(business), _cancel_task(heartbeat))
                if cancelled:
                    await self._cancel_after_disconnect(execution)
                try:
                    releasing = True
                    await _await_with_timeout(
                        self._store.release(execution.snapshot, token=token),
                        timeout_seconds=1.0,
                    )
                except asyncio.CancelledError:
                    # A disconnect while release is still cooperative must get
                    # its fenced cancellation attempt before we give up the token.
                    # A commit that already happened cannot be undone here.
                    releasing = False
                    await self._cancel_after_disconnect(execution)
                    raise
                except Exception as exc:
                    logger.warning(
                        "agent.durable.release_failed", extra={"error_type": type(exc).__name__}
                    )
                finally:
                    releasing = False

            cleanup_task = asyncio.create_task(cleanup())
            interrupted = False
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    interrupted = True
                    cancelled = True
                    if releasing:
                        cleanup_task.cancel()
            cleanup_task.result()
            if interrupted:
                await self._cancel_after_disconnect(execution)
                raise asyncio.CancelledError

    async def _terminal_cleanup(self, execution: _DurableExecution, *, cancelled: bool) -> None:
        async def persist() -> None:
            while True:
                try:
                    snapshot = await self._store.load(
                        tenant_id=execution.snapshot.tenant_id,
                        user_id=execution.snapshot.user_id,
                        run_id=execution.snapshot.run_id,
                    )
                except DomainError as exc:
                    if exc.code in ("DURABLE_RUN_NOT_FOUND", "DURABLE_RUN_EXPIRED"):
                        return
                    raise
                execution.snapshot = snapshot
                if snapshot.status in TERMINAL_STATUSES:
                    return
                owned = await self._store.owns(
                    snapshot, token=execution.token, allow_unclaimed=execution.created_here
                )
                if not owned:
                    if cancelled:
                        return
                    raise durable_error("DURABLE_LEASE_LOST")
                try:
                    if cancelled:
                        execution.snapshot = await self._store.cancel(
                            snapshot,
                            now=self._clock(),
                            ownership_token=execution.token,
                            allow_unclaimed=execution.created_here,
                        )
                        await self._audit_transition(
                            execution.context, execution.snapshot, "agent.durable.cancelled"
                        )
                    else:
                        if snapshot.pending_call_id is not None:
                            ledger = await self._store.ledger(snapshot, snapshot.pending_call_id)
                            if ledger.status == "started" and ledger.replay_policy == "never":
                                await execution.stop(
                                    "DURABLE_TOOL_OUTCOME_UNKNOWN",
                                    status="intervention_required",
                                    allow_revoked=True,
                                )
                                return
                        await execution.stop("AGENT_TIMEOUT", status="stopped", allow_revoked=True)
                    return
                except DomainError as exc:
                    if exc.code != "DURABLE_RESUME_CONFLICT":
                        raise

        try:
            await _await_with_timeout(persist(), timeout_seconds=1.0)
        except TimeoutError as exc:
            # Never pretend a terminal fence exists when storage is unavailable.
            logger.error("agent.durable.terminal_persistence_timeout")
            raise durable_error("DURABLE_TERMINAL_PERSISTENCE_FAILED", status_code=503) from exc

    async def _cancel_after_disconnect(self, execution: _DurableExecution) -> None:
        # Persist the terminal fence despite repeated transport/task cancellation.
        task = asyncio.create_task(self._terminal_cleanup(execution, cancelled=True))
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                interrupted = True
            except Exception:
                # Observe/log the completed cleanup task below without letting
                # its failure replace the caller's cancellation.
                break
        try:
            task.result()
        except Exception as exc:
            # A transport cancellation remains cancellation even when its best-
            # effort fence cannot commit. Never claim persistence in that case.
            logger.warning(
                "agent.durable.disconnect_cleanup_failed",
                extra={"error_type": type(exc).__name__},
            )
        if interrupted:
            raise asyncio.CancelledError


class _DurableExecution:
    def __init__(
        self,
        *,
        service: DurableAgentService,
        context: AuthenticatedRequestContext,
        snapshot: DurableSnapshot,
        token: str,
        created_here: bool = False,
    ) -> None:
        self.created_here = created_here
        self.service = service
        self.context = context
        self.snapshot = snapshot
        self.token = token
        self.runtime = service._runtime_factory(snapshot.config, snapshot.run_id)
        self.revocation_error: DomainError | None = None
        self._live: _AgentExecution | None = None

    def revoke(self, error: DomainError | None = None) -> None:
        if self.revocation_error is None:
            self.revocation_error = error or durable_error("DURABLE_LEASE_LOST")
        # Fail closed before renewal cancellation grace or any other await. A
        # pending registry dependency may become ready during that grace period.
        if self._live is not None and self._live.dispatch_state is not None:
            self._live.dispatch_state.revoke()

    def check_active(self) -> None:
        if self.revocation_error is not None:
            raise self.revocation_error

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.service._lease_seconds / 3)
            if self.snapshot.status in TERMINAL_STATUSES:
                return
            try:
                await _await_with_timeout(
                    self.service._store.renew(
                        self.snapshot,
                        token=self.token,
                        now=self.service._clock(),
                        lease_seconds=self.service._lease_seconds,
                    ),
                    timeout_seconds=min(
                        self.service._lease_seconds / 3,
                        self.service._remaining(self.snapshot),
                        5.0,
                    ),
                    on_cancel=lambda: self.revoke(
                        durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503)
                    ),
                )
            except TimeoutError:
                error = durable_error("DURABLE_STORAGE_TIMEOUT", status_code=503)
                self.revoke(error)
                raise error from None
            except DomainError as exc:
                self.revoke(exc)
                raise
            except Exception:
                error = durable_error("DURABLE_STORAGE_UNAVAILABLE", status_code=503)
                self.revoke(error)
                raise error from None

    async def save(
        self,
        snapshot: DurableSnapshot,
        *,
        ledger: ToolLedgerEntry | None = None,
        allow_revoked: bool = False,
    ) -> None:
        if not allow_revoked:
            self.check_active()
        now = self.service._clock()
        if now < self.snapshot.last_observed_at:
            if snapshot.error_code != "DURABLE_CLOCK_REGRESSION":
                raise durable_error("DURABLE_CLOCK_REGRESSION")
            now = self.snapshot.last_observed_at
        previous = self.snapshot
        self.snapshot = await self.service._store.save(
            self.snapshot,
            snapshot,
            token=self.token,
            now=now,
            ledger=ledger,
        )
        if not allow_revoked:
            self.check_active()
        if previous.status != self.snapshot.status:
            approval_ref = (
                previous.approval.approval_id
                if previous.approval
                else self.snapshot.approval.approval_id
                if self.snapshot.approval
                else None
            )
            await self.service._audit_transition(
                self.context,
                self.snapshot,
                f"agent.durable.{self.snapshot.status}",
                approval_ref=approval_ref,
            )

    async def stop(
        self,
        code: str,
        *,
        status: Literal["failed", "stopped", "cancelled", "intervention_required"] = "failed",
        allow_revoked: bool = False,
    ) -> None:
        await self.save(
            self.snapshot.model_copy(
                update={
                    "status": status,
                    "phase": "done",
                    "error_code": code,
                    "approval": None,
                    "result": None,
                }
            ),
            allow_revoked=allow_revoked,
        )

    async def finish(
        self, result: AgentRunResult, *, ledger: ToolLedgerEntry | None = None
    ) -> None:
        result = result.model_copy(
            update={
                "steps_used": max(result.steps_used, self.snapshot.steps_used),
                "tool_calls_used": max(result.tool_calls_used, self.snapshot.tool_calls_used),
            }
        )
        await self.save(
            self.snapshot.model_copy(
                update={
                    "status": result.status.value,
                    "phase": "done",
                    "result": result,
                    "steps_used": result.steps_used,
                    "tool_calls_used": result.tool_calls_used,
                    "observations": result.observations,
                    "error_code": result.error_code,
                    "approval": None,
                    "pending_call_id": None,
                }
            ),
            ledger=ledger,
        )

    async def advance(self) -> None:
        self.check_active()
        # Framework checkpoint replay never reuses an old closure's business
        # state. Reload and reconcile the authoritative snapshot on every node.
        self.snapshot = await self.service._store.load(
            tenant_id=self.context.auth.tenant_id,
            user_id=self.context.auth.user_id,
            run_id=self.snapshot.run_id,
        )
        self.check_active()
        if self.snapshot.status != "running":
            return
        now = self.service._clock()
        if now < self.snapshot.last_observed_at:
            await self.stop("DURABLE_CLOCK_REGRESSION")
            return
        if self.service._remaining(self.snapshot) <= 0:
            await self.stop("AGENT_TIMEOUT", status="stopped")
            return
        try:
            self.context, live, fingerprints = await self.service._revalidate(
                self.context,
                self.snapshot,
                self.runtime,
            )
            self.check_active()
            if self.snapshot.phase == "decision":
                if (
                    self.snapshot.observations
                    and self.snapshot.observations[-1].status is ToolInvocationStatus.FAILURE
                ):
                    await self.stop("AGENT_TOOL_FAILED")
                    return
                # Reserve model attempts before I/O. A crash before/after the
                # response cannot silently refund its decision/cost budget.
                if self.snapshot.steps_used < self.snapshot.config.max_steps:
                    await self.save(
                        self.snapshot.model_copy(
                            update={
                                "steps_used": self.snapshot.steps_used + 1,
                                "decision_inflight": True,
                            }
                        )
                    )
                result = await self.runtime._decide(live)
                if result is not None:
                    await self.finish(result)
                    return
                assert live.decision is not None
                validate_private_arguments(live.decision.arguments)
                await self.save(
                    self.snapshot.model_copy(
                        update={
                            "steps_used": live.steps_used,
                            "decision": live.decision,
                            "decision_inflight": False,
                            "phase": (
                                "validation"
                                if live.decision.action is AgentActionType.FINAL_ANSWER
                                else "tool"
                            ),
                            "evidence_fingerprints": fingerprints,
                        }
                    )
                )
            elif self.snapshot.phase == "validation":
                result = await self.runtime._validate_result(live)
                # Check current source state again after any asynchronous validation.
                await self.service._revalidate(self.context, self.snapshot, self.runtime)
                if self.service._remaining(self.snapshot) <= 0:
                    await self.stop("AGENT_TIMEOUT", status="stopped")
                    return
                await self.finish(result)
            elif self.snapshot.phase == "tool":
                await self.execute_tool(live)
            else:
                raise durable_error("DURABLE_PHASE_INVALID")
        except DomainError as exc:
            if exc.code in ("DURABLE_LEASE_LOST", "DURABLE_RESUME_CONFLICT"):
                raise
            await self.stop(exc.code, status="stopped" if exc.code == "AGENT_TIMEOUT" else "failed")

    async def execute_tool(self, live: _AgentExecution) -> None:
        self._live = live
        self.check_active()
        definition = await self.service._definition(self.runtime, self.snapshot, self.context)
        assert self.snapshot.decision is not None
        digest = decision_digest(self.snapshot.decision)
        definition_digest = _definition_digest(definition)
        if self.snapshot.pending_call_id is not None:
            ledger = await self.service._store.ledger(self.snapshot, self.snapshot.pending_call_id)
            if ledger.action_digest != digest:
                raise durable_error("DURABLE_LEDGER_INVALID")
            if ledger.status == "completed":
                # Both fields are one atomic transaction, so this combination
                # means a malformed snapshot rather than permission to replay.
                raise durable_error("DURABLE_LEDGER_INVALID")
            if (
                ledger.replay_policy != "read_only"
                or definition.durable_replay_policy != "read_only"
            ):
                await self.stop("DURABLE_TOOL_OUTCOME_UNKNOWN", status="intervention_required")
                return
            await self.save(
                self.snapshot.model_copy(update={"pending_call_id": None}),
                ledger=ledger.model_copy(update={"status": "unknown"}),
            )
        if self.snapshot.tool_calls_used >= self.snapshot.config.max_tool_calls:
            result = await self.runtime._execute_tool(live)
            assert result is not None
            await self.finish(result)
            return
        needs_approval = (
            definition.requires_approval or definition.name in self.snapshot.approval_tools
        )
        if needs_approval and self.snapshot.approved_action_digest != digest:
            await self.save(
                self.snapshot.model_copy(
                    update={
                        "status": "awaiting_approval",
                        "approval": ApprovalRequest(
                            approval_id=str(uuid4()),
                            tool_name=definition.name,
                            arguments=dict(self.snapshot.decision.arguments),
                            action_digest=digest,
                            definition_digest=definition_digest,
                            expires_at=(
                                min(
                                    self.service._clock()
                                    + self.snapshot.active_budget.approval_timeout_seconds,
                                    self.snapshot.expires_at,
                                )
                                if self.snapshot.active_budget
                                else self.snapshot.deadline_at
                            ),
                        ),
                    }
                )
            )
            return
        if needs_approval and self.snapshot.approved_definition_digest != definition_digest:
            raise durable_error("DURABLE_APPROVAL_STALE")
        journal = _ToolJournal(self, definition)
        result = await self.runtime._execute_tool(live, journal=journal)
        if result is not None:
            if self.snapshot.pending_call_id is not None:
                ledger = await self.service._store.ledger(
                    self.snapshot, self.snapshot.pending_call_id
                )
                if (
                    live.dispatch_state is not None
                    and live.dispatch_state.registry_entered
                    and not live.dispatch_state.handler_started
                ):
                    # A live registry receipt proves rejection before handler
                    # entry. Commit the known failure and ledger together. An
                    # interrupted process has no such proof and stays unknown.
                    await self.finish(
                        result, ledger=ledger.model_copy(update={"status": "completed"})
                    )
                    return
                if ledger.status == "started" and ledger.replay_policy == "never":
                    await self.stop("DURABLE_TOOL_OUTCOME_UNKNOWN", status="intervention_required")
                    return
            await self.finish(result)
        else:
            # after_dispatch already committed the result and next phase before
            # emitting the result event. This checkpoint clears the consumed grant.
            await self.save(
                self.snapshot.model_copy(
                    update={
                        "phase": "decision",
                        "decision": None,
                        "approved_action_digest": None,
                        "approved_definition_digest": None,
                    }
                )
            )


class _ToolJournal:
    def __init__(self, owner: _DurableExecution, definition: ToolDefinition) -> None:
        self.owner = owner
        self.definition = definition

    async def before_dispatch(
        self, execution: _AgentExecution, call_id: str
    ) -> ToolApprovalGrant | None:
        assert execution.decision is not None
        policy = self.definition.durable_replay_policy
        assert policy in ("read_only", "never")
        await self.owner.save(
            self.owner.snapshot.model_copy(
                update={
                    "steps_used": execution.steps_used,
                    "tool_calls_used": execution.tool_calls_used,
                    "repeated_counts": execution.repeated_detector.snapshot(),
                    "pending_call_id": call_id,
                }
            ),
            ledger=ToolLedgerEntry(
                call_id=call_id,
                tool_name=self.definition.name,
                action_digest=decision_digest(execution.decision),
                replay_policy=policy,
                status="started",
            ),
        )

        self.owner.check_active()
        now = self.owner.service._clock()
        if self.owner.service._remaining(self.owner.snapshot) <= 0:
            raise durable_error("AGENT_TIMEOUT")
        if now < self.owner.snapshot.last_observed_at:
            raise durable_error("DURABLE_CLOCK_REGRESSION")
        if self.definition.requires_approval:
            if self.owner.snapshot.approved_action_digest != decision_digest(execution.decision):
                raise durable_error("DURABLE_APPROVAL_REQUIRED")
            return ToolApprovalGrant(
                run_id=self.owner.snapshot.run_id,
                tenant_id=self.owner.snapshot.tenant_id,
                user_id=self.owner.snapshot.user_id,
                tool_name=self.definition.name,
                arguments_digest=argument_digest(execution.decision.arguments),
                expires_at=self.owner.snapshot.deadline_at,
                clock=self.owner.service._clock,
            )
        return None

    async def after_dispatch(
        self,
        execution: _AgentExecution,
        call_id: str,
        result: ToolExecutionResult,
    ) -> None:
        observation = _summarize_tool_result(result)
        ledger = await self.owner.service._store.ledger(self.owner.snapshot, call_id)
        observations = (*execution.observations, observation)
        evidence = await self.owner.service._evidence.rehydrate(
            context=self.owner.context,
            observations=observations,
            fingerprints=self.owner.snapshot.evidence_fingerprints,
        )
        await self.owner.save(
            self.owner.snapshot.model_copy(
                update={
                    "phase": "decision",
                    "decision": None,
                    "observations": evidence.observations,
                    "pending_call_id": None,
                    "evidence_fingerprints": evidence.fingerprints,
                    "approved_action_digest": None,
                    "approved_definition_digest": None,
                }
            ),
            ledger=ledger.model_copy(update={"status": "completed", "observation": observation}),
        )


def _definition_digest(definition: ToolDefinition) -> str:
    payload = {
        "name": definition.name,
        "permission": definition.permission,
        "input_schema": definition.input_json_schema,
        "output_schema": definition.output_json_schema,
        "requires_approval": definition.requires_approval,
        "replay_policy": definition.durable_replay_policy,
        "implementation_version": definition.implementation_version,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
