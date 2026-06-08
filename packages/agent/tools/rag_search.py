from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.common.context import AuthenticatedRequestContext
from packages.retrieval.application import (
    RetrieveApplicationService,
    RetrieveCandidateResponse,
    RetrieveCommand,
    RetrieveResponse,
)
from packages.retrieval.exceptions import RETRIEVAL_FORBIDDEN_FILTER, RetrievalError

RAG_SEARCH_PERMISSION = "agent:tool:rag_search"
_MAX_AGENT_TOP_K = 20
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SENSITIVE_METADATA_KEYS = {
    "absolute_path",
    "access_token",
    "acl",
    "api_key",
    "authorization",
    "body",
    "chunk_content",
    "chunk_text",
    "content",
    "created_by",
    "document_content",
    "embedding",
    "embedding_vector",
    "file_path",
    "full_query",
    "local_path",
    "password",
    "permissions",
    "prompt",
    "provider_raw_response",
    "query",
    "query_text",
    "query_vector",
    "raw_response",
    "roles",
    "secret",
    "sql",
    "text",
    "token",
    "tsquery",
    "tsvector",
    "user_id",
    "vector",
}
_SENSITIVE_COMPACT_KEYS = {
    "".join(char for char in key if char.isalnum()) for key in _SENSITIVE_METADATA_KEYS
}


class RetrievalApplication(Protocol):
    async def retrieve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: RetrieveCommand,
    ) -> RetrieveResponse: ...


class RagSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    top_k: int = Field(default=5, ge=1, le=_MAX_AGENT_TOP_K)
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("query")
    @classmethod
    def _query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @field_validator("top_k", mode="before")
    @classmethod
    def _top_k_must_be_int(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("top_k must be an integer")
        return value

    @field_validator("metadata_filter", mode="before")
    @classmethod
    def _metadata_filter_must_be_safe(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata_filter must be an object")

        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("metadata_filter keys must be strings")
            normalized_key = key.strip()
            if not normalized_key:
                raise ValueError("metadata_filter keys must not be blank")
            if normalized_key.startswith("$") or any(char.isspace() for char in normalized_key):
                raise ValueError("metadata_filter keys must be structured field names")
            if normalized_key != "tenant_id" and _is_sensitive_key(normalized_key):
                raise ValueError("metadata_filter contains sensitive fields")
            if not _is_scalar_metadata_value(item):
                raise ValueError("metadata_filter values must be scalar")
            if isinstance(item, str) and _looks_like_local_absolute_path(item):
                raise ValueError("metadata_filter values must not be local absolute paths")
            normalized[normalized_key] = item
        return normalized


class RagSearchResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    source: str | None = None
    source_uri: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    summary: str = ""


class RagSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["success", "error"]
    query_summary: dict[str, int] = Field(default_factory=dict)
    result_count: int = Field(ge=0)
    results: tuple[RagSearchResultItem, ...] = ()
    error_code: str | None = None
    message: str | None = None


def build_rag_search_tool(
    *,
    retrieval_app: RetrieveApplicationService | RetrievalApplication,
    timeout_seconds: float,
    rate_limit: ToolRateLimit,
) -> ToolDefinition:
    async def handler(
        payload: RagSearchInput,
        context: AuthenticatedRequestContext,
    ) -> RagSearchOutput:
        forbidden_output = _forbidden_tenant_filter_output(payload, context)
        if forbidden_output is not None:
            return forbidden_output

        command = RetrieveCommand(
            query=payload.query,
            top_k=payload.top_k,
            metadata_filter=dict(payload.metadata_filter),
            score_threshold=payload.score_threshold,
        )
        try:
            response = await retrieval_app.retrieve(context=context, command=command)
        except RetrievalError as exc:
            return RagSearchOutput(
                status="error",
                error_code=exc.code,
                message="retrieval_request_failed",
                result_count=0,
                results=(),
            )

        results = tuple(_result_item(candidate) for candidate in response.candidates)
        return RagSearchOutput(
            status="success",
            query_summary=_safe_query_summary(response.query_summary),
            result_count=len(results),
            results=results,
            message=None if results else "no_authorized_results",
        )

    return ToolDefinition(
        name="rag_search",
        description="Search authorized RAG retrieval candidates through backend retrieval filters.",
        input_schema=RagSearchInput,
        output_schema=RagSearchOutput,
        permission=RAG_SEARCH_PERMISSION,
        timeout_seconds=timeout_seconds,
        rate_limit=rate_limit,
        handler=handler,
    )


def _forbidden_tenant_filter_output(
    payload: RagSearchInput,
    context: AuthenticatedRequestContext,
) -> RagSearchOutput | None:
    tenant_filter = payload.metadata_filter.get("tenant_id")
    if tenant_filter is None or tenant_filter == context.auth.tenant_id:
        return None
    return RagSearchOutput(
        status="error",
        error_code=RETRIEVAL_FORBIDDEN_FILTER,
        message="retrieval_request_not_allowed",
        result_count=0,
        results=(),
    )


def _result_item(candidate: RetrieveCandidateResponse) -> RagSearchResultItem:
    return RagSearchResultItem(
        document_id=candidate.document_id,
        version_id=candidate.version_id,
        chunk_id=candidate.chunk_id,
        source=_safe_optional_text(candidate.source),
        source_uri=_safe_optional_text(candidate.source_uri),
        source_type=candidate.source_type,
        page_start=candidate.page_start,
        page_end=candidate.page_end,
        title_path=candidate.title_path,
        score=candidate.score,
        retrieval_method=candidate.retrieval_method,
        summary=_summary(candidate),
    )


def _summary(candidate: RetrieveCandidateResponse) -> str:
    title = " / ".join(candidate.title_path)
    source = _safe_optional_text(candidate.source)
    page_text = _page_text(candidate.page_start, candidate.page_end)
    details = ", ".join(part for part in (source, page_text) if part)
    if title and details:
        return f"{title} ({details})"
    return title or details


def _page_text(page_start: int | None, page_end: int | None) -> str:
    if page_start is None or page_end is None:
        return ""
    if page_start == page_end:
        return f"page {page_start}"
    return f"pages {page_start}-{page_end}"


def _safe_query_summary(summary: Mapping[str, object]) -> dict[str, int]:
    safe: dict[str, int] = {}
    for key, value in summary.items():
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
            safe[key] = value
    return safe


def _is_scalar_metadata_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | bool):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _safe_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or _looks_like_local_absolute_path(stripped):
        return None
    return stripped


def _looks_like_local_absolute_path(value: str) -> bool:
    normalized = value.strip()
    return (
        normalized.startswith("/")
        or normalized.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(normalized) is not None
    )


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return normalized in _SENSITIVE_METADATA_KEYS or compact in _SENSITIVE_COMPACT_KEYS
