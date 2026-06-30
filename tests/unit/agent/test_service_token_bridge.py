from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from apps.api.factories.domain import _build_agent_tool_registry
from packages.agent.dto import (
    AgentRunCreate,
    AgentRunRecord,
    AgentRunUpdate,
    ToolCallRecord,
    ToolRateLimit,
)
from packages.agent.registry import ToolRegistry
from packages.agent.service_token_bridge import (
    SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN,
    ServiceTokenToolBridge,
    ServiceTokenToolBridgeCandidate,
    ServiceTokenToolChoice,
)
from packages.agent.tools.rag_search import RetrievalApplication, build_rag_search_tool
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.config import AppSettings
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError


class FakeAgentRunRepository:
    def __init__(self) -> None:
        self.created: list[AgentRunCreate] = []
        self.updated: list[AgentRunUpdate] = []

    async def create_run(self, record: AgentRunCreate) -> AgentRunRecord:
        self.created.append(record)
        return AgentRunRecord(
            id="run-1",
            request_id=record.request_id,
            trace_id=record.trace_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            created_by=record.created_by,
            status="running",
            max_steps=record.max_steps,
            max_tool_calls=record.max_tool_calls,
            timeout_seconds=record.timeout_seconds,
            input_summary=dict(record.input_summary),
            metadata=dict(record.metadata),
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    async def update_run_result(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        update: AgentRunUpdate,
    ) -> AgentRunRecord:
        _ = tenant_id, user_id, run_id
        self.updated.append(update)
        return AgentRunRecord(
            id="run-1",
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            created_by="user-1",
            status=update.status,
            max_steps=1,
            max_tool_calls=1,
            timeout_seconds=30.0,
            steps_used=update.steps_used,
            tool_calls_used=update.tool_calls_used,
            termination_reason=update.termination_reason,
            error_code=update.error_code,
            latency_ms=update.latency_ms,
            input_summary={"length": 4},
            metadata=dict(update.metadata),
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeToolCallRepository:
    async def list_by_agent_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_run_id: str,
    ) -> list[ToolCallRecord]:
        _ = tenant_id, user_id, agent_run_id
        return [
            ToolCallRecord(
                id="call-1",
                agent_run_id="run-1",
                request_id="req-1",
                trace_id="trace-1",
                tenant_id="tenant-1",
                user_id="user-1",
                tool_name="rag_search",
                permission="agent:tool:rag_search",
                status="success",
                latency_ms=5.0,
                error_code=None,
                arguments_summary={"argument_keys": ["query"]},
                result_summary={"result_keys": ["results"]},
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )
        ]

    async def record_tool_call(self, record: object) -> None:
        _ = record
        return None


class FakeRetrievalApp:
    async def retrieve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: Any,
    ) -> object:
        _ = context, command
        return type(
            "RetrieveResponse",
            (),
            {
                "query_summary": {"candidate_count": 1},
                "candidates": [
                    type(
                        "Candidate",
                        (),
                        {
                            "document_id": "doc-1",
                            "version_id": "v1",
                            "chunk_id": "chunk-1",
                            "source_display_name": "policy.md",
                            "source_type": "markdown",
                            "page_start": 1,
                            "page_end": 1,
                            "title_path": ("Policy",),
                            "score": 0.92,
                            "retrieval_method": "hybrid",
                        },
                    )()
                ],
            },
        )()


@pytest.mark.asyncio
async def test_bridge_denies_service_token_without_agent_run_permission() -> None:
    audit = InMemoryAuditPort()
    bridge = ServiceTokenToolBridge(
        registry=ToolRegistry(audit=audit),
        agent_runs=FakeAgentRunRepository(),
        tool_calls=FakeToolCallRepository(),
        audit=audit,
    )

    with pytest.raises(DomainError) as exc_info:
        await bridge.execute(
            context=_context(permissions=("document:read", "retrieval:query")),
            latest_user_message="question",
            session_id=None,
            candidates=(
                ServiceTokenToolBridgeCandidate(
                    name="rag_search",
                    description="Search authorized content.",
                    schema_summary={
                        "type": "object",
                        "property_names": ("query",),
                        "required": ("query",),
                        "property_count": 1,
                    },
                    declaration_type="modern",
                ),
            ),
            tool_choice=ServiceTokenToolChoice(mode="tool", tool_name="rag_search"),
            requested_model="rag",
        )

    assert exc_info.value.code == SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN
    assert audit.events[0].status == "denied"


@pytest.mark.asyncio
async def test_bridge_executes_registered_rag_search_and_returns_safe_summary() -> None:
    audit = InMemoryAuditPort()
    registry = ToolRegistry(audit=audit)
    registry.register(
        build_rag_search_tool(
            retrieval_app=cast(RetrievalApplication, FakeRetrievalApp()),
            timeout_seconds=5.0,
            rate_limit=ToolRateLimit(max_calls=5, window_seconds=60.0),
        )
    )
    bridge = ServiceTokenToolBridge(
        registry=registry,
        agent_runs=FakeAgentRunRepository(),
        tool_calls=FakeToolCallRepository(),
        audit=audit,
    )

    result = await bridge.execute(
        context=_context(
            permissions=("document:read", "retrieval:query", "agent:run", "agent:tool:rag_search")
        ),
        latest_user_message="vacation policy",
        session_id="session-1",
        candidates=(
            ServiceTokenToolBridgeCandidate(
                name="rag_search",
                description="Search authorized content.",
                schema_summary={
                    "type": "object",
                    "property_names": ("query",),
                    "required": ("query",),
                    "property_count": 1,
                },
                declaration_type="modern",
            ),
        ),
        tool_choice=ServiceTokenToolChoice(mode="tool", tool_name="rag_search"),
        requested_model="rag",
    )

    assert result.agent_run_id == "run-1"
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "rag_search"
    assert result.status == "success"
    assert result.citations[0].document_id == "doc-1"
    assert "policy.md" in result.assistant_text
    assert "source_uri" not in str(result.model_dump())


def test_build_agent_tool_registry_registers_shared_tool_set() -> None:
    registry = _build_agent_tool_registry(
        settings=AppSettings(),
        audit=InMemoryAuditPort(),
        tool_call_repository=FakeToolCallRepository(),
        retrieve_application_service=cast(Any, FakeRetrievalApp()),
    )

    assert registry.registered_tool_names == frozenset({"rag_search", "calculator", "file_reader"})


def _context(*, permissions: tuple[str, ...]) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth_method="service_token",
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-1",
            roles=("knowledge_user",),
            department="engineering",
            permissions=permissions,
        ),
    )
