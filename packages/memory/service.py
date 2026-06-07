from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from packages.common.context import AuthenticatedRequestContext
from packages.memory.dto import (
    ChatHistoryMessage,
    ChatMemoryConfig,
    ChatMessageCreate,
    ChatMessageRecord,
    ChatSessionCreate,
    ChatSessionRecord,
    PackedChatHistory,
)
from packages.memory.exceptions import chat_memory_invalid_request, chat_session_not_found

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\b(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S+", re.I),
    re.compile(r"[A-Za-z]:[\\/][^\s]+"),
    re.compile(r"/(?:home|Users|etc|var)/[^\s]+"),
    re.compile(r"\b(select|insert|update|delete)\s+.+\s+(from|into|set)\s+", re.I),
)


class ChatMemoryRepository(Protocol):
    async def create_session(self, record: ChatSessionCreate) -> ChatSessionRecord: ...

    async def get_active_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> ChatSessionRecord | None: ...

    async def append_message(self, record: ChatMessageCreate) -> ChatMessageRecord: ...

    async def list_recent_messages(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> list[ChatMessageRecord]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class ChatMemoryService:
    def __init__(
        self,
        repository: ChatMemoryRepository | None = None,
        config: ChatMemoryConfig | None = None,
    ) -> None:
        self._repository = repository
        self._config = config or ChatMemoryConfig()

    @property
    def config(self) -> ChatMemoryConfig:
        return self._config

    async def get_or_create_session(
        self,
        *,
        context: AuthenticatedRequestContext,
        session_id: str | None,
        query: str,
    ) -> ChatSessionRecord:
        repository = self._require_repository(context=context, session_id=session_id)
        normalized_session_id = session_id.strip() if session_id is not None else None
        if normalized_session_id:
            session = await repository.get_active_session(
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                session_id=normalized_session_id,
            )
            if session is None:
                raise chat_session_not_found(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    session_id=normalized_session_id,
                )
            return session

        return await repository.create_session(
            ChatSessionCreate(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                created_by=context.auth.user_id,
                title=self._session_title(query),
                metadata={
                    "query_char_count": len(query),
                    "title_strategy": "deterministic_safe_summary",
                },
            )
        )

    async def append_user_message(
        self,
        *,
        context: AuthenticatedRequestContext,
        session_id: str,
        content: str,
    ) -> ChatMessageRecord:
        repository = self._require_repository(context=context, session_id=session_id)
        return await repository.append_message(
            ChatMessageCreate(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                session_id=session_id,
                role="user",
                content=_bounded_content(content, max_chars=self._config.max_message_chars),
                content_summary=self.summarize_user_content(content),
                token_count=_estimate_tokens(content),
                metadata={"content_char_count": len(content), "summary_strategy": "deterministic"},
            )
        )

    async def append_assistant_message(
        self,
        *,
        context: AuthenticatedRequestContext,
        session_id: str,
        content: str,
        citations_metadata: dict[str, object] | None = None,
        no_answer: bool = False,
        error_code: str | None = None,
    ) -> ChatMessageRecord:
        repository = self._require_repository(context=context, session_id=session_id)
        metadata: dict[str, object] = {
            "content_char_count": len(content),
            "summary_strategy": "deterministic",
            "no_answer": no_answer,
        }
        if citations_metadata:
            metadata.update(_safe_citation_metadata(citations_metadata))
        if error_code is not None:
            metadata["error_code"] = error_code
        stored_content = content.strip() or "assistant_no_content"
        return await repository.append_message(
            ChatMessageCreate(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                session_id=session_id,
                role="assistant",
                content=_bounded_content(stored_content, max_chars=self._config.max_message_chars),
                content_summary=self.summarize_assistant_content(stored_content),
                token_count=_estimate_tokens(stored_content),
                metadata=metadata,
            )
        )

    async def load_packed_history(
        self,
        *,
        context: AuthenticatedRequestContext,
        session_id: str,
    ) -> PackedChatHistory:
        repository = self._require_repository(context=context, session_id=session_id)
        session = await repository.get_active_session(
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            session_id=session_id,
        )
        if session is None:
            raise chat_session_not_found(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                session_id=session_id,
            )
        messages = await repository.list_recent_messages(
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            session_id=session_id,
            limit=self._config.max_messages,
        )
        return self.pack_history(
            session_id=session_id,
            messages=messages,
            total_message_count=session.message_count,
        )

    def summarize_user_content(self, content: str) -> str:
        return _safe_summary(
            content,
            max_chars=self._config.max_summary_chars,
            fallback="user_message",
            force_summary=True,
        )

    def summarize_assistant_content(self, content: str) -> str:
        return _safe_summary(
            content,
            max_chars=self._config.max_summary_chars,
            fallback="assistant_message",
        )

    def pack_history(
        self,
        *,
        session_id: str,
        messages: Sequence[ChatMessageRecord],
        total_message_count: int | None = None,
    ) -> PackedChatHistory:
        ordered = sorted(messages, key=lambda item: item.sequence_no)
        active_allowed = [
            item
            for item in ordered
            if item.status == "active" and item.role in self._config.allowed_roles
        ]
        recent = active_allowed[-self._config.max_messages :]
        packed_reversed: list[ChatHistoryMessage] = []
        token_count = 0
        source_count = max(total_message_count or len(ordered), len(ordered))
        dropped = source_count - len(recent)

        for message in reversed(recent):
            content = _safe_history_content(
                message.content_summary or message.content,
                max_chars=self._config.max_message_chars,
            )
            history_tokens = _estimate_tokens(content)
            next_tokens = token_count + history_tokens
            if next_tokens > self._config.max_history_tokens:
                dropped += 1
                continue
            packed_reversed.append(
                ChatHistoryMessage(
                    role=message.role,
                    content=content,
                    token_count=history_tokens,
                    sequence_no=message.sequence_no,
                )
            )
            token_count = next_tokens

        packed = tuple(reversed(packed_reversed))
        return PackedChatHistory(
            session_id=session_id,
            messages=packed,
            message_count=source_count,
            used_count=len(packed),
            dropped_count=dropped,
            token_count=token_count,
            safe_counts={
                "memory_message_count": source_count,
                "memory_used_count": len(packed),
                "memory_dropped_count": dropped,
                "memory_token_count": token_count,
            },
        )

    async def commit(self) -> None:
        repository = self._repository
        if repository is not None:
            await repository.commit()

    async def rollback(self) -> None:
        repository = self._repository
        if repository is not None:
            await repository.rollback()

    def _session_title(self, query: str) -> str:
        return _safe_summary(query, max_chars=120, fallback="chat_session")

    def _require_repository(
        self,
        *,
        context: AuthenticatedRequestContext,
        session_id: str | None,
    ) -> ChatMemoryRepository:
        if self._repository is None:
            raise chat_memory_invalid_request(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                session_id=session_id,
                reason="memory_repository_not_configured",
            )
        return self._repository


def _safe_summary(
    content: str,
    *,
    max_chars: int,
    fallback: str,
    force_summary: bool = False,
) -> str:
    normalized = " ".join(content.split())
    redacted = _redact_sensitive_text(normalized)
    if not redacted:
        return fallback
    if force_summary:
        return _deterministic_summary(redacted, max_chars=max_chars, fallback=fallback)
    if len(redacted) <= max_chars:
        return redacted
    return redacted[: max_chars - 3].rstrip() + "..."


def _safe_history_content(content: str, *, max_chars: int) -> str:
    return _safe_summary(content, max_chars=max_chars, fallback="redacted_message")


def _redact_sensitive_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _bounded_content(content: str, *, max_chars: int) -> str:
    normalized = content.strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _deterministic_summary(content: str, *, max_chars: int, fallback: str) -> str:
    words = content.split()
    if not words:
        return fallback
    if len(words) == 1:
        summary = words[0][: max(1, min(len(words[0]), 32))]
    else:
        summary = " ".join(words[: min(len(words), 12)])
    summary = f"summary:{summary}"
    if summary == content:
        summary = f"summary:{len(content)} chars"
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 3].rstrip() + "..."


def _estimate_tokens(content: str) -> int:
    stripped = content.strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def _safe_citation_metadata(metadata: dict[str, object]) -> dict[str, object]:
    allowed = {
        "citation_count",
        "citations",
        "unsupported_count",
        "no_answer",
        "forged_reference_count",
    }
    result: dict[str, object] = {}
    for key, value in metadata.items():
        if key not in allowed:
            continue
        if key == "citations":
            result[key] = _safe_citation_summaries(value)
        else:
            result[key] = value
    return result


def _safe_citation_summaries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list | tuple):
        return []
    summaries: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        summary: dict[str, object] = {}
        for key in ("document_id", "version_id", "chunk_id", "retrieval_method", "score"):
            candidate = item.get(key)
            if key == "score" and isinstance(candidate, int | float) and not isinstance(
                candidate,
                bool,
            ):
                summary[key] = float(candidate)
            elif key != "score" and isinstance(candidate, str) and candidate.strip():
                summary[key] = candidate.strip()
        if {"document_id", "version_id", "chunk_id"} <= summary.keys():
            summaries.append(summary)
    return summaries[:50]
