from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent.dto import (
    AgentRunCreate,
    AgentRunRecord,
    AgentRunUpdate,
    ToolCallCreate,
    ToolCallQuery,
    ToolCallRecord,
)
from packages.agent.exceptions import agent_run_storage_failed
from packages.agent.storage.models import AgentRunModel, ToolCallModel
from packages.data.storage.base import generate_uuid


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, record: AgentRunCreate) -> AgentRunRecord:
        now = datetime.now(tz=UTC)
        model = AgentRunModel(
            id=generate_uuid(),
            created_at=now,
            updated_at=now,
            request_id=record.request_id,
            trace_id=record.trace_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            created_by=record.created_by,
            status=record.status,
            max_steps=record.max_steps,
            max_tool_calls=record.max_tool_calls,
            timeout_seconds=record.timeout_seconds,
            steps_used=0,
            tool_calls_used=0,
            termination_reason=None,
            error_code=None,
            latency_ms=None,
            input_summary=dict(record.input_summary),
            metadata_=dict(record.metadata),
        )
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise agent_run_storage_failed(
                request_id=record.request_id,
                trace_id=record.trace_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                reason="run_write_failed",
            ) from exc
        return agent_run_record_from_model(model)

    async def update_run_result(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        update: AgentRunUpdate,
    ) -> AgentRunRecord:
        try:
            model = await self._session.scalar(
                select(AgentRunModel).where(
                    AgentRunModel.tenant_id == tenant_id,
                    AgentRunModel.user_id == user_id,
                    AgentRunModel.id == run_id,
                )
            )
            if model is None:
                raise agent_run_storage_failed(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    run_id=run_id,
                    reason="run_not_found_for_update",
                )
            model.status = update.status
            model.steps_used = update.steps_used
            model.tool_calls_used = update.tool_calls_used
            model.termination_reason = update.termination_reason
            model.error_code = update.error_code
            model.latency_ms = update.latency_ms
            model.metadata_ = dict(update.metadata)
            model.updated_at = datetime.now(tz=UTC)
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise agent_run_storage_failed(
                tenant_id=tenant_id,
                user_id=user_id,
                run_id=run_id,
                reason="run_update_failed",
            ) from exc
        return agent_run_record_from_model(model)

    async def get_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
    ) -> AgentRunRecord | None:
        try:
            model = await self._session.scalar(
                select(AgentRunModel).where(
                    AgentRunModel.tenant_id == tenant_id,
                    AgentRunModel.user_id == user_id,
                    AgentRunModel.id == run_id,
                )
            )
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise agent_run_storage_failed(
                tenant_id=tenant_id,
                user_id=user_id,
                run_id=run_id,
                reason="run_read_failed",
            ) from exc
        return None if model is None else agent_run_record_from_model(model)

    async def get_run_by_request_id(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str,
    ) -> AgentRunRecord | None:
        try:
            model = await self._session.scalar(
                select(AgentRunModel).where(
                    AgentRunModel.tenant_id == tenant_id,
                    AgentRunModel.user_id == user_id,
                    AgentRunModel.request_id == request_id,
                )
            )
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise agent_run_storage_failed(
                tenant_id=tenant_id,
                user_id=user_id,
                request_id=request_id,
                reason="run_read_failed",
            ) from exc
        return None if model is None else agent_run_record_from_model(model)

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise agent_run_storage_failed(reason="commit_failed") from exc

    async def rollback(self) -> None:
        await self._session.rollback()


class ToolCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_tool_call(self, record: ToolCallCreate) -> None:
        await self.create_tool_call(record)
        await self.commit()

    async def create_tool_call(self, record: ToolCallCreate) -> ToolCallRecord:
        now = datetime.now(tz=UTC)
        model = ToolCallModel(
            id=generate_uuid(),
            created_at=now,
            updated_at=now,
            request_id=record.request_id,
            trace_id=record.trace_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            created_by=record.user_id,
            agent_run_id=record.agent_run_id,
            tool_name=record.tool_name,
            permission=record.permission,
            status=record.status,
            latency_ms=record.latency_ms,
            error_code=record.error_code,
            arguments_summary=dict(record.arguments_summary),
            result_summary=dict(record.result_summary),
        )
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise agent_run_storage_failed(
                request_id=record.request_id,
                trace_id=record.trace_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                run_id=record.agent_run_id,
                reason="tool_call_write_failed",
            ) from exc
        return tool_call_record_from_model(model)

    async def list_by_agent_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_run_id: str,
    ) -> list[ToolCallRecord]:
        return await self.list_tool_calls(
            ToolCallQuery(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_run_id=agent_run_id,
            )
        )

    async def list_tool_calls(self, query: ToolCallQuery) -> list[ToolCallRecord]:
        statement = select(ToolCallModel).where(ToolCallModel.tenant_id == query.tenant_id)
        if query.user_id is not None:
            statement = statement.where(ToolCallModel.user_id == query.user_id)
        if query.agent_run_id is not None:
            statement = statement.where(ToolCallModel.agent_run_id == query.agent_run_id)
        if query.tool_name is not None:
            statement = statement.where(ToolCallModel.tool_name == query.tool_name)
        if query.status is not None:
            statement = statement.where(ToolCallModel.status == query.status)
        if query.created_at_from is not None:
            statement = statement.where(ToolCallModel.created_at >= query.created_at_from)
        if query.created_at_to is not None:
            statement = statement.where(ToolCallModel.created_at <= query.created_at_to)
        statement = statement.order_by(ToolCallModel.created_at, ToolCallModel.id).limit(
            query.limit
        )
        try:
            models = (await self._session.scalars(statement)).all()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise agent_run_storage_failed(
                tenant_id=query.tenant_id,
                user_id=query.user_id,
                run_id=query.agent_run_id,
                reason="tool_call_read_failed",
            ) from exc
        return [tool_call_record_from_model(model) for model in models]

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise agent_run_storage_failed(reason="tool_call_commit_failed") from exc

    async def rollback(self) -> None:
        await self._session.rollback()


def agent_run_record_from_model(model: AgentRunModel) -> AgentRunRecord:
    return AgentRunRecord(
        id=model.id,
        request_id=model.request_id,
        trace_id=model.trace_id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        created_by=model.created_by,
        status=_status(model.status),
        max_steps=model.max_steps,
        max_tool_calls=model.max_tool_calls,
        timeout_seconds=model.timeout_seconds,
        steps_used=model.steps_used,
        tool_calls_used=model.tool_calls_used,
        termination_reason=model.termination_reason,
        error_code=model.error_code,
        latency_ms=model.latency_ms,
        input_summary=dict(model.input_summary or {}),
        metadata=dict(model.metadata_ or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _status(value: str) -> Literal["running", "completed", "stopped", "failed"]:
    if value in {"running", "completed", "stopped", "failed"}:
        return cast(Literal["running", "completed", "stopped", "failed"], value)
    raise agent_run_storage_failed(reason="invalid_run_status")


def tool_call_record_from_model(model: ToolCallModel) -> ToolCallRecord:
    return ToolCallRecord(
        id=model.id,
        agent_run_id=model.agent_run_id,
        request_id=model.request_id,
        trace_id=model.trace_id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        tool_name=model.tool_name,
        permission=model.permission,
        status=_tool_call_status(model.status),
        latency_ms=model.latency_ms,
        error_code=model.error_code,
        arguments_summary=dict(model.arguments_summary or {}),
        result_summary=dict(model.result_summary or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _tool_call_status(value: str) -> Literal["success", "denied", "failure"]:
    if value in {"success", "denied", "failure"}:
        return cast(Literal["success", "denied", "failure"], value)
    raise agent_run_storage_failed(reason="invalid_tool_call_status")
