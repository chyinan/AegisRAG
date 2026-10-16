"""Transactional CAS/lease storage using independent short-lived database sessions.

A business checkpoint and its tool ledger transition commit atomically. Framework
checkpoints are separate and may lag; business state is the authoritative fence.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

from pydantic import ValidationError
from sqlalchemy import and_, delete, false, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from packages.agent.durable.dto import (
    SNAPSHOT_VERSION,
    TERMINAL_STATUSES,
    DurableSnapshot,
    ToolLedgerEntry,
    durable_error,
)
from packages.agent.storage.models import (
    AgentRunModel,
    DurableAgentRunModel,
    DurableGraphCheckpointModel,
    DurableToolLedgerModel,
)
from packages.data.storage.base import generate_uuid

_P = ParamSpec("_P")
_T = TypeVar("_T")


def _private_storage_boundary(
    method: Callable[_P, Coroutine[Any, Any, _T]],
) -> Callable[_P, Coroutine[Any, Any, _T]]:
    """Do not let SQL statements/parameters escape into API or error tracebacks."""

    @wraps(method)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        cast("SqlAlchemyDurableStore", args[0])._assert_private_logging()
        try:
            return await method(*args, **kwargs)
        except SQLAlchemyError:
            raise durable_error("DURABLE_STORAGE_UNAVAILABLE", status_code=503) from None

    return wrapped


@dataclass(frozen=True)
class KeyedCreateResult:
    inserted: bool
    snapshot: DurableSnapshot


class SqlAlchemyDurableStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    def _assert_private_logging(self) -> None:
        # SQL parameter logging can reveal snapshots; DEBUG additionally logs
        # returned rows even when hide_parameters=True. Never alter global
        # logging as a side effect of a request: fail closed before private I/O.
        bind = self._sessions.kw.get("bind")
        if isinstance(bind, AsyncEngine):
            engine = bind.sync_engine
        elif isinstance(bind, AsyncConnection) and bind.sync_connection is not None:
            engine = bind.sync_connection.engine
        else:
            raise durable_error("DURABLE_STORAGE_BIND_UNSUPPORTED", status_code=503)
        if engine.echo or engine.logger.isEnabledFor(logging.INFO):
            raise durable_error("DURABLE_STORAGE_LOGGING_UNSAFE", status_code=503)

    @staticmethod
    def _add_new(
        session: AsyncSession,
        snapshot: DurableSnapshot,
        *,
        token: str | None = None,
        lease_until: float | None = None,
    ) -> None:
        metadata: dict[str, object] = {"durable": True, "snapshot_version": SNAPSHOT_VERSION}
        if snapshot.startup is not None:
            metadata["keyed_start"] = True
        now = datetime.now(tz=UTC)
        session.add(
            AgentRunModel(
                id=snapshot.run_id,
                created_at=now,
                updated_at=now,
                request_id=snapshot.request_id,
                trace_id=snapshot.trace_id,
                tenant_id=snapshot.tenant_id,
                user_id=snapshot.user_id,
                created_by=snapshot.user_id,
                status="running",
                max_steps=snapshot.config.max_steps,
                max_tool_calls=snapshot.config.max_tool_calls,
                timeout_seconds=snapshot.config.timeout_seconds,
                steps_used=0,
                tool_calls_used=0,
                input_summary={"input_char_count": len(snapshot.config.input_text)},
                metadata_=metadata,
            )
        )
        session.add(
            DurableAgentRunModel(
                id=snapshot.run_id,
                tenant_id=snapshot.tenant_id,
                user_id=snapshot.user_id,
                created_by=snapshot.user_id,
                status=snapshot.status,
                schema_version=SNAPSHOT_VERSION,
                revision=snapshot.revision,
                snapshot=snapshot.persisted(),
                expires_at=snapshot.expires_at,
                lease_token=token,
                lease_until=lease_until,
            )
        )

    @_private_storage_boundary
    async def create(self, snapshot: DurableSnapshot) -> None:
        async with self._sessions.begin() as session:
            self._add_new(session, snapshot)

    @_private_storage_boundary
    async def create_keyed(
        self,
        snapshot: DurableSnapshot,
        *,
        token: str,
        now: float,
        lease_seconds: float,
    ) -> KeyedCreateResult:
        if snapshot.startup is None:
            raise durable_error("DURABLE_START_PROTOCOL_INVALID")
        try:
            # The retained legacy primary key is also the tombstone. Insert both
            # rows and the initial ownership fence in ONE transaction. Never
            # upsert: a concurrent purge must not let a used key be recreated.
            async with self._sessions.begin() as session:
                self._add_new(session, snapshot, token=token, lease_until=now + lease_seconds)
        except IntegrityError as exc:
            original = exc.orig
            if not (
                getattr(original, "sqlstate", None) == "23505"
                or getattr(original, "pgcode", None) == "23505"
                or getattr(original, "sqlite_errorcode", None) in (1555, 2067)
            ):
                raise
            # The failed transaction has rolled back. Only a fresh owner-scoped
            # read can establish that this was a duplicate rather than corruption
            # or a cross-owner identifier collision. Do not expose SQL diagnostics.
            existing = await self.lookup_keyed(
                tenant_id=snapshot.tenant_id, user_id=snapshot.user_id, run_id=snapshot.run_id
            )
            if existing is None:
                raise durable_error("DURABLE_STORAGE_UNAVAILABLE", status_code=503) from None
            return KeyedCreateResult(inserted=False, snapshot=existing)
        return KeyedCreateResult(inserted=True, snapshot=snapshot)

    @_private_storage_boundary
    async def lookup_keyed(
        self, *, tenant_id: str, user_id: str, run_id: str
    ) -> DurableSnapshot | None:
        async with self._sessions() as session:
            # One statement observes either the private row or its retained
            # tombstone; a purge between independent reads cannot create a gap.
            pair = (
                await session.execute(
                    select(AgentRunModel, DurableAgentRunModel)
                    .outerjoin(
                        DurableAgentRunModel,
                        and_(
                            DurableAgentRunModel.id == AgentRunModel.id,
                            DurableAgentRunModel.tenant_id == AgentRunModel.tenant_id,
                            DurableAgentRunModel.user_id == AgentRunModel.user_id,
                        ),
                    )
                    .where(
                        AgentRunModel.id == run_id,
                        AgentRunModel.tenant_id == tenant_id,
                        AgentRunModel.user_id == user_id,
                    )
                )
            ).one_or_none()
            if pair is None:
                return None
            legacy, private = pair
            if legacy.metadata_.get("keyed_start") is not True:
                raise durable_error("DURABLE_START_KEY_COLLISION")
            if private is None:
                raise durable_error("DURABLE_RUN_EXPIRED", status_code=410)
            value = self._validated_snapshot(
                private, tenant_id=tenant_id, user_id=user_id, run_id=run_id
            )
            if value.startup is None:
                raise durable_error("DURABLE_START_PROTOCOL_INVALID")
            return value

    @staticmethod
    def _validated_snapshot(
        row: DurableAgentRunModel, *, tenant_id: str, user_id: str, run_id: str
    ) -> DurableSnapshot:
        if row.schema_version != SNAPSHOT_VERSION:
            raise durable_error("DURABLE_SCHEMA_UNSUPPORTED")
        try:
            value = DurableSnapshot.model_validate(row.snapshot)
        except (ValidationError, ValueError, TypeError):
            raise durable_error("DURABLE_SNAPSHOT_INVALID") from None
        if (value.revision, value.status, value.run_id, value.tenant_id, value.user_id) != (
            row.revision,
            row.status,
            run_id,
            tenant_id,
            user_id,
        ):
            raise durable_error("DURABLE_SNAPSHOT_INVALID")
        return value

    @_private_storage_boundary
    async def load(self, *, tenant_id: str, user_id: str, run_id: str) -> DurableSnapshot:
        async with self._sessions() as session:
            row = await session.scalar(
                select(DurableAgentRunModel).where(
                    DurableAgentRunModel.id == run_id,
                    DurableAgentRunModel.tenant_id == tenant_id,
                    DurableAgentRunModel.user_id == user_id,
                )
            )
            if row is None:
                legacy = await session.scalar(
                    select(AgentRunModel).where(
                        AgentRunModel.id == run_id,
                        AgentRunModel.tenant_id == tenant_id,
                        AgentRunModel.user_id == user_id,
                    )
                )
                if legacy is not None and legacy.metadata_.get("keyed_start") is True:
                    raise durable_error("DURABLE_RUN_EXPIRED", status_code=410)
                raise durable_error("DURABLE_RUN_NOT_FOUND", status_code=404)
            return self._validated_snapshot(
                row, tenant_id=tenant_id, user_id=user_id, run_id=run_id
            )

    @_private_storage_boundary
    async def acquire(
        self,
        snapshot: DurableSnapshot,
        *,
        token: str,
        now: float,
        lease_seconds: float,
    ) -> None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(DurableAgentRunModel)
                .where(
                    *self._scope(snapshot),
                    DurableAgentRunModel.revision == snapshot.revision,
                    DurableAgentRunModel.status.not_in(TERMINAL_STATUSES),
                    DurableAgentRunModel.expires_at > now,
                    or_(
                        DurableAgentRunModel.lease_until.is_(None),
                        DurableAgentRunModel.lease_until <= now,
                    ),
                )
                .values(lease_token=token, lease_until=now + lease_seconds)
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise durable_error("DURABLE_RESUME_CONFLICT")

    @_private_storage_boundary
    async def renew(
        self,
        snapshot: DurableSnapshot,
        *,
        token: str,
        now: float,
        lease_seconds: float,
    ) -> None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(DurableAgentRunModel)
                .where(
                    *self._scope(snapshot),
                    DurableAgentRunModel.lease_token == token,
                    DurableAgentRunModel.lease_until > now,
                    DurableAgentRunModel.status.not_in(TERMINAL_STATUSES),
                )
                .values(lease_until=now + lease_seconds)
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise durable_error("DURABLE_LEASE_LOST")

    @_private_storage_boundary
    async def release(self, snapshot: DurableSnapshot, *, token: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(DurableAgentRunModel)
                .where(
                    *self._scope(snapshot),
                    DurableAgentRunModel.lease_token == token,
                )
                .values(lease_token=None, lease_until=None)
            )

    @_private_storage_boundary
    async def save(
        self,
        previous: DurableSnapshot,
        snapshot: DurableSnapshot,
        *,
        token: str,
        now: float,
        ledger: ToolLedgerEntry | None = None,
    ) -> DurableSnapshot:
        saved = snapshot.model_copy(
            update={
                "revision": previous.revision + 1,
                "last_observed_at": now,
            }
        )
        payload = saved.persisted()
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(DurableAgentRunModel)
                .where(
                    *self._scope(previous),
                    DurableAgentRunModel.revision == previous.revision,
                    DurableAgentRunModel.lease_token == token,
                    DurableAgentRunModel.lease_until > now,
                    DurableAgentRunModel.status.not_in(TERMINAL_STATUSES),
                )
                .values(
                    snapshot=payload,
                    revision=saved.revision,
                    status=saved.status,
                    updated_at=datetime.now(UTC),
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise durable_error("DURABLE_LEASE_LOST")
            if ledger is not None:
                existing = await session.scalar(
                    select(DurableToolLedgerModel).where(
                        DurableToolLedgerModel.call_id == ledger.call_id,
                        DurableToolLedgerModel.run_id == saved.run_id,
                        DurableToolLedgerModel.tenant_id == saved.tenant_id,
                        DurableToolLedgerModel.user_id == saved.user_id,
                    )
                )
                entry = ledger.model_dump(mode="json")
                if existing is None:
                    session.add(
                        DurableToolLedgerModel(
                            id=generate_uuid(),
                            tenant_id=saved.tenant_id,
                            user_id=saved.user_id,
                            created_by=saved.user_id,
                            run_id=saved.run_id,
                            call_id=ledger.call_id,
                            status=ledger.status,
                            entry=entry,
                        )
                    )
                else:
                    if existing.status == "completed":
                        raise durable_error("DURABLE_LEDGER_IMMUTABLE")
                    existing.status = ledger.status
                    existing.entry = entry
            await self._legacy_update(session, saved)
        return saved

    @_private_storage_boundary
    async def cancel(
        self,
        previous: DurableSnapshot,
        *,
        now: float,
        ownership_token: str | None = None,
        allow_unclaimed: bool = False,
    ) -> DurableSnapshot:
        saved = previous.model_copy(
            update={
                "revision": previous.revision + 1,
                "status": "cancelled",
                "phase": "done",
                "approval": None,
                "result": None,
                "error_code": "DURABLE_CANCELLED",
                "last_observed_at": now,
            }
        )
        conditions = list(self._scope(previous))
        if ownership_token is not None:
            conditions.append(
                or_(
                    DurableAgentRunModel.lease_token == ownership_token,
                    and_(
                        DurableAgentRunModel.lease_token.is_(None),
                        DurableAgentRunModel.revision == 0,
                    )
                    if allow_unclaimed
                    else false(),
                )
            )
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(DurableAgentRunModel)
                .where(
                    *conditions,
                    DurableAgentRunModel.revision == previous.revision,
                    DurableAgentRunModel.status.not_in(TERMINAL_STATUSES),
                )
                .values(
                    snapshot=saved.persisted(),
                    revision=saved.revision,
                    status="cancelled",
                    lease_token=None,
                    lease_until=None,
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise durable_error("DURABLE_RESUME_CONFLICT")
            await self._legacy_update(session, saved)
        return saved

    @_private_storage_boundary
    async def owns(
        self,
        snapshot: DurableSnapshot,
        *,
        token: str,
        allow_unclaimed: bool = False,
    ) -> bool:
        async with self._sessions() as session:
            return (
                await session.scalar(
                    select(DurableAgentRunModel.id).where(
                        *self._scope(snapshot),
                        or_(
                            DurableAgentRunModel.lease_token == token,
                            and_(
                                DurableAgentRunModel.lease_token.is_(None),
                                DurableAgentRunModel.revision == 0,
                            )
                            if allow_unclaimed
                            else false(),
                        ),
                    )
                )
                is not None
            )

    @_private_storage_boundary
    async def ledger(self, snapshot: DurableSnapshot, call_id: str) -> ToolLedgerEntry:
        async with self._sessions() as session:
            row = await session.scalar(
                select(DurableToolLedgerModel).where(
                    DurableToolLedgerModel.call_id == call_id,
                    DurableToolLedgerModel.run_id == snapshot.run_id,
                    DurableToolLedgerModel.tenant_id == snapshot.tenant_id,
                    DurableToolLedgerModel.user_id == snapshot.user_id,
                )
            )
            if row is None:
                raise durable_error("DURABLE_LEDGER_MISSING")
            try:
                return ToolLedgerEntry.model_validate(row.entry)
            except ValidationError:
                raise durable_error("DURABLE_LEDGER_INVALID") from None

    @_private_storage_boundary
    async def graph_get(
        self,
        snapshot: DurableSnapshot,
        checkpoint_id: str | None,
    ) -> dict[str, Any] | None:
        async with self._sessions() as session:
            query = select(DurableGraphCheckpointModel).where(
                DurableGraphCheckpointModel.run_id == snapshot.run_id,
                DurableGraphCheckpointModel.tenant_id == snapshot.tenant_id,
                DurableGraphCheckpointModel.user_id == snapshot.user_id,
            )
            if checkpoint_id is not None:
                query = query.where(DurableGraphCheckpointModel.checkpoint_id == checkpoint_id)
            row = await session.scalar(
                query.order_by(DurableGraphCheckpointModel.checkpoint_id.desc()).limit(1)
            )
            if row is None:
                return None
            return {
                "checkpoint": row.checkpoint,
                "metadata": row.metadata_,
                "parent_id": row.parent_checkpoint_id,
                "writes": row.writes,
            }

    @_private_storage_boundary
    async def graph_put(
        self,
        snapshot: DurableSnapshot,
        *,
        token: str,
        now: float,
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        parent_id: str | None,
    ) -> None:
        async with self._sessions.begin() as session:
            await self._graph_fence(session, snapshot, token=token, now=now)
            session.add(
                DurableGraphCheckpointModel(
                    id=generate_uuid(),
                    tenant_id=snapshot.tenant_id,
                    user_id=snapshot.user_id,
                    created_by=snapshot.user_id,
                    status="active",
                    run_id=snapshot.run_id,
                    checkpoint_id=checkpoint["id"],
                    checkpoint=checkpoint,
                    metadata_=metadata,
                    parent_checkpoint_id=parent_id,
                    writes=[],
                )
            )

    @_private_storage_boundary
    async def graph_writes(
        self,
        snapshot: DurableSnapshot,
        *,
        token: str,
        now: float,
        checkpoint_id: str,
        writes: list[dict[str, Any]],
    ) -> None:
        async with self._sessions.begin() as session:
            await self._graph_fence(session, snapshot, token=token, now=now)
            row = await session.scalar(
                select(DurableGraphCheckpointModel)
                .where(
                    DurableGraphCheckpointModel.run_id == snapshot.run_id,
                    DurableGraphCheckpointModel.tenant_id == snapshot.tenant_id,
                    DurableGraphCheckpointModel.user_id == snapshot.user_id,
                    DurableGraphCheckpointModel.checkpoint_id == checkpoint_id,
                )
                .with_for_update()
            )
            if row is None:
                raise durable_error("DURABLE_GRAPH_CHECKPOINT_MISSING")
            indexed = {(str(w["task_id"]), cast(int, w["index"])): w for w in row.writes}
            for write in writes:
                indexed[(str(write["task_id"]), int(write["index"]))] = write
            row.writes = list(indexed.values())

    @_private_storage_boundary
    async def purge_expired(self, *, tenant_id: str, now: float, limit: int = 100) -> int:
        """Tenant-scoped retention sweep; legacy redacted audit rows are retained."""
        async with self._sessions.begin() as session:
            ids = list(
                (
                    await session.scalars(
                        select(DurableAgentRunModel.id)
                        .where(
                            DurableAgentRunModel.tenant_id == tenant_id,
                            DurableAgentRunModel.expires_at <= now,
                        )
                        .limit(min(max(limit, 1), 1000))
                    )
                ).all()
            )
            for table in (DurableGraphCheckpointModel, DurableToolLedgerModel):
                await session.execute(
                    delete(table).where(
                        table.tenant_id == tenant_id,
                        table.run_id.in_(ids),
                    )
                )
            await session.execute(
                delete(DurableAgentRunModel).where(
                    DurableAgentRunModel.tenant_id == tenant_id,
                    DurableAgentRunModel.id.in_(ids),
                )
            )
            return len(ids)

    @staticmethod
    def _scope(snapshot: DurableSnapshot) -> tuple[Any, ...]:
        return (
            DurableAgentRunModel.id == snapshot.run_id,
            DurableAgentRunModel.schema_version == SNAPSHOT_VERSION,
            DurableAgentRunModel.tenant_id == snapshot.tenant_id,
            DurableAgentRunModel.user_id == snapshot.user_id,
        )

    async def _graph_fence(
        self,
        session: AsyncSession,
        snapshot: DurableSnapshot,
        *,
        token: str,
        now: float,
    ) -> None:
        # A no-op UPDATE takes a row lock until the checkpoint transaction commits.
        # Terminal business state still permits the graph's final checkpoint under
        # its existing lease; cancellation clears the token and always fences it.
        result = await session.execute(
            update(DurableAgentRunModel)
            .where(
                *self._scope(snapshot),
                DurableAgentRunModel.lease_token == token,
                DurableAgentRunModel.lease_until > now,
            )
            .values(lease_token=token)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise durable_error("DURABLE_LEASE_LOST")

    @staticmethod
    async def _legacy_update(session: AsyncSession, snapshot: DurableSnapshot) -> None:
        status = snapshot.status
        if status == "awaiting_approval":
            status = "running"
        elif status in ("cancelled", "intervention_required"):
            status = "stopped"
        result = snapshot.result
        await session.execute(
            update(AgentRunModel)
            .where(
                AgentRunModel.id == snapshot.run_id,
                AgentRunModel.tenant_id == snapshot.tenant_id,
                AgentRunModel.user_id == snapshot.user_id,
            )
            .values(
                status=status,
                steps_used=snapshot.steps_used,
                tool_calls_used=snapshot.tool_calls_used,
                error_code=snapshot.error_code or (result.error_code if result else None),
                termination_reason=(
                    result.termination_reason.value if result else snapshot.error_code
                ),
                latency_ms=result.latency_ms if result else None,
                metadata_={
                    "durable": True,
                    "snapshot_version": SNAPSHOT_VERSION,
                    **({"keyed_start": True} if snapshot.startup is not None else {}),
                },
            )
        )
