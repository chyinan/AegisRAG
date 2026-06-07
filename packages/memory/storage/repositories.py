from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.data.storage.base import generate_uuid
from packages.memory.dto import (
    ChatMessageCreate,
    ChatMessageRecord,
    ChatSessionCreate,
    ChatSessionRecord,
)
from packages.memory.exceptions import chat_memory_storage_failed
from packages.memory.storage.models import ChatMessageModel, ChatSessionModel


class ChatMemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(self, record: ChatSessionCreate) -> ChatSessionRecord:
        now = datetime.now(tz=UTC)
        model = ChatSessionModel(
            id=generate_uuid(),
            created_at=now,
            updated_at=now,
            request_id=record.request_id,
            trace_id=record.trace_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            created_by=record.created_by,
            status="active",
            title=record.title,
            last_message_at=None,
            message_count=0,
            metadata_=dict(record.metadata),
        )
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise chat_memory_storage_failed(
                request_id=record.request_id,
                trace_id=record.trace_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                reason="session_write_failed",
            ) from exc
        return chat_session_record_from_model(model)

    async def get_active_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> ChatSessionRecord | None:
        try:
            model = await self._session.scalar(
                select(ChatSessionModel).where(
                    ChatSessionModel.tenant_id == tenant_id,
                    ChatSessionModel.user_id == user_id,
                    ChatSessionModel.id == session_id,
                    ChatSessionModel.status == "active",
                )
            )
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise chat_memory_storage_failed(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                reason="session_read_failed",
            ) from exc
        if model is None:
            return None
        return chat_session_record_from_model(model)

    async def append_message(self, record: ChatMessageCreate) -> ChatMessageRecord:
        session = await self._get_active_session_model(
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            session_id=record.session_id,
            for_update=True,
        )
        sequence_no = await self._next_sequence_no(
            tenant_id=record.tenant_id,
            session_id=record.session_id,
        )
        now = datetime.now(tz=UTC)
        model = ChatMessageModel(
            id=generate_uuid(),
            created_at=now,
            updated_at=now,
            session_id=record.session_id,
            request_id=record.request_id,
            trace_id=record.trace_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            created_by=record.user_id,
            status="active",
            role=record.role,
            content=record.content,
            content_summary=record.content_summary,
            token_count=record.token_count,
            sequence_no=sequence_no,
            metadata_=dict(record.metadata),
        )
        session.message_count += 1
        session.last_message_at = now
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
            await self._session.refresh(session)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise chat_memory_storage_failed(
                request_id=record.request_id,
                trace_id=record.trace_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                session_id=record.session_id,
                reason="message_write_failed",
            ) from exc
        return chat_message_record_from_model(model)

    async def list_recent_messages(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> list[ChatMessageRecord]:
        bounded_limit = min(max(limit, 1), 100)
        if await self.get_active_session(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
        ) is None:
            return []
        statement = (
            select(ChatMessageModel)
            .where(
                ChatMessageModel.tenant_id == tenant_id,
                ChatMessageModel.user_id == user_id,
                ChatMessageModel.session_id == session_id,
                ChatMessageModel.status == "active",
            )
            .order_by(ChatMessageModel.sequence_no.desc(), ChatMessageModel.created_at.desc())
            .limit(bounded_limit)
        )
        try:
            models = list(await self._session.scalars(statement))
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise chat_memory_storage_failed(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                reason="message_read_failed",
            ) from exc
        return [chat_message_record_from_model(model) for model in reversed(models)]

    async def update_session_status(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        status: Literal["active", "closed", "deleted"],
    ) -> ChatSessionRecord | None:
        try:
            model = await self._session.scalar(
                select(ChatSessionModel).where(
                    ChatSessionModel.tenant_id == tenant_id,
                    ChatSessionModel.user_id == user_id,
                    ChatSessionModel.id == session_id,
                )
            )
            if model is None:
                return None
            model.status = status
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise chat_memory_storage_failed(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                reason="session_update_failed",
            ) from exc
        return chat_session_record_from_model(model)

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise chat_memory_storage_failed(reason="commit_failed") from exc

    async def rollback(self) -> None:
        await self._session.rollback()

    async def _get_active_session_model(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        for_update: bool = False,
    ) -> ChatSessionModel:
        try:
            statement = select(ChatSessionModel).where(
                ChatSessionModel.tenant_id == tenant_id,
                ChatSessionModel.user_id == user_id,
                ChatSessionModel.id == session_id,
                ChatSessionModel.status == "active",
            )
            if for_update:
                statement = statement.with_for_update()
            model = await self._session.scalar(
                statement
            )
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise chat_memory_storage_failed(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                reason="session_read_failed",
            ) from exc
        if model is None:
            raise chat_memory_storage_failed(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                reason="session_unavailable_for_write",
            )
        return model

    async def _next_sequence_no(self, *, tenant_id: str, session_id: str) -> int:
        try:
            current = await self._session.scalar(
                select(func.max(ChatMessageModel.sequence_no)).where(
                    ChatMessageModel.tenant_id == tenant_id,
                    ChatMessageModel.session_id == session_id,
                )
            )
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise chat_memory_storage_failed(
                tenant_id=tenant_id,
                session_id=session_id,
                reason="sequence_read_failed",
            ) from exc
        return int(current or 0) + 1


def chat_session_record_from_model(model: ChatSessionModel) -> ChatSessionRecord:
    return ChatSessionRecord(
        id=model.id,
        request_id=model.request_id,
        trace_id=model.trace_id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        created_by=model.created_by,
        status=_session_status(model.status),
        title=model.title,
        last_message_at=model.last_message_at,
        message_count=model.message_count,
        metadata=dict(model.metadata_ or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def chat_message_record_from_model(model: ChatMessageModel) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=model.id,
        session_id=model.session_id,
        request_id=model.request_id,
        trace_id=model.trace_id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        created_by=model.created_by,
        status=_message_status(model.status),
        role=_message_role(model.role),
        content=model.content,
        content_summary=model.content_summary,
        token_count=model.token_count,
        sequence_no=model.sequence_no,
        metadata=dict(model.metadata_ or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _session_status(value: str) -> Literal["active", "closed", "deleted"]:
    if value in {"active", "closed", "deleted"}:
        return cast(Literal["active", "closed", "deleted"], value)
    raise chat_memory_storage_failed(reason="invalid_session_status")


def _message_status(value: str) -> Literal["active", "deleted"]:
    if value in {"active", "deleted"}:
        return cast(Literal["active", "deleted"], value)
    raise chat_memory_storage_failed(reason="invalid_message_status")


def _message_role(value: str) -> Literal["user", "assistant", "system_summary"]:
    if value in {"user", "assistant", "system_summary"}:
        return cast(Literal["user", "assistant", "system_summary"], value)
    raise chat_memory_storage_failed(reason="invalid_message_role")
