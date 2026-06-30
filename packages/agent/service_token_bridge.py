from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from time import perf_counter as default_perf_counter
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.agent.dto import (
    AgentRunCreate,
    AgentRunRecord,
    AgentRunUpdate,
    ToolCallRecord,
    ToolExecutionResult,
    ToolInvocationStatus,
)
from packages.agent.exceptions import AgentToolError
from packages.agent.registry import ToolRegistry
from packages.auth.policies import has_agent_run_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError

SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN = "SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN"
SERVICE_TOKEN_TOOL_NOT_AVAILABLE = "SERVICE_TOKEN_TOOL_NOT_AVAILABLE"
SERVICE_TOKEN_TOOL_SELECTION_UNSUPPORTED = "SERVICE_TOKEN_TOOL_SELECTION_UNSUPPORTED"
SERVICE_TOKEN_TOOL_BRIDGE_FAILED = "SERVICE_TOKEN_TOOL_BRIDGE_FAILED"


class ServiceTokenToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["auto", "none", "required", "tool"] = "auto"
    tool_name: str | None = None

    @field_validator("tool_name")
    @classmethod
    def _optional_tool_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("tool_name must not be blank")
        return normalized


class ServiceTokenToolBridgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    schema_summary: dict[str, object]
    declaration_type: Literal["modern", "legacy"]

    @field_validator("name", "description")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class ServiceTokenToolCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    source_display_name: str
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...] = ()
    retrieval_method: str
    score: float


class ServiceTokenToolBridgeExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    trace_id: str
    session_id: str
    assistant_text: str
    citations: tuple[ServiceTokenToolCitation, ...] = ()
    agent_run_id: str
    tool_call_id: str
    tool_name: str
    status: Literal["success", "error"]
    latency_ms: float = Field(ge=0)
    error_code: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentRunWriterPort(Protocol):
    async def create_run(self, record: AgentRunCreate) -> AgentRunRecord: ...

    async def update_run_result(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        update: AgentRunUpdate,
    ) -> AgentRunRecord: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class ToolCallLookupPort(Protocol):
    async def list_by_agent_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_run_id: str,
    ) -> list[ToolCallRecord]: ...


class ServiceTokenToolBridgePort(Protocol):
    async def execute(
        self,
        *,
        context: AuthenticatedRequestContext,
        latest_user_message: str,
        session_id: str | None,
        candidates: tuple[ServiceTokenToolBridgeCandidate, ...],
        tool_choice: ServiceTokenToolChoice,
        requested_model: str,
    ) -> ServiceTokenToolBridgeExecution: ...


class ServiceTokenToolBridge:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        agent_runs: AgentRunWriterPort,
        tool_calls: ToolCallLookupPort,
        audit: AuditPort,
        perf_counter: Callable[[], float] = default_perf_counter,
    ) -> None:
        self._registry = registry
        self._agent_runs = agent_runs
        self._tool_calls = tool_calls
        self._audit = audit
        self._perf_counter = perf_counter

    async def execute(
        self,
        *,
        context: AuthenticatedRequestContext,
        latest_user_message: str,
        session_id: str | None,
        candidates: tuple[ServiceTokenToolBridgeCandidate, ...],
        tool_choice: ServiceTokenToolChoice,
        requested_model: str,
    ) -> ServiceTokenToolBridgeExecution:
        selected_name = self._select_tool_name(candidates=candidates, tool_choice=tool_choice)
        if not has_agent_run_permission(context.auth):
            await self._record_bridge_audit(
                context=context,
                status=AuditStatus.DENIED,
                error_code=SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN,
                metadata=_bridge_audit_metadata(
                    candidates=candidates,
                    tool_choice=tool_choice,
                    requested_model=requested_model,
                    status="denied",
                    reason_code="missing_agent_run_permission",
                ),
            )
            raise DomainError(
                code=SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN,
                message="Service Token tool bridge permission is required.",
                details=_safe_error_details(context, SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN),
                status_code=403,
            )
        if selected_name not in self._registry.registered_tool_names:
            await self._record_bridge_audit(
                context=context,
                status=AuditStatus.DENIED,
                error_code=SERVICE_TOKEN_TOOL_NOT_AVAILABLE,
                metadata=_bridge_audit_metadata(
                    candidates=candidates,
                    tool_choice=tool_choice,
                    requested_model=requested_model,
                    status="denied",
                    reason_code="tool_unavailable",
                ),
            )
            raise DomainError(
                code=SERVICE_TOKEN_TOOL_NOT_AVAILABLE,
                message="Requested tool is not available.",
                details=_safe_error_details(context, SERVICE_TOKEN_TOOL_NOT_AVAILABLE),
                status_code=403,
            )

        started = self._perf_counter()
        agent_run = await self._agent_runs.create_run(
            AgentRunCreate(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                created_by=context.auth.user_id,
                status="running",
                max_steps=1,
                max_tool_calls=1,
                timeout_seconds=30.0,
                input_summary={"length": len(latest_user_message)},
                metadata={
                    "bridge_type": "service_token_tool",
                    "tool_candidate_names": [candidate.name for candidate in candidates],
                    "tool_candidate_count": len(candidates),
                    "tool_choice_mode": tool_choice.mode,
                    "requested_model": requested_model,
                },
            )
        )
        await self._agent_runs.commit()
        try:
            arguments = _tool_arguments(
                tool_name=selected_name,
                latest_user_message=latest_user_message,
            )
            try:
                result = await self._registry.execute(
                    name=selected_name,
                    arguments=arguments,
                    context=context,
                    agent_run_id=agent_run.id,
                )
            except AgentToolError as exc:
                await self._agent_runs.update_run_result(
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    run_id=agent_run.id,
                    update=AgentRunUpdate(
                        status="stopped",
                        termination_reason=SERVICE_TOKEN_TOOL_NOT_AVAILABLE,
                        steps_used=1,
                        tool_calls_used=1,
                        error_code=SERVICE_TOKEN_TOOL_NOT_AVAILABLE,
                        latency_ms=_elapsed_ms(self._perf_counter() - started),
                        metadata={"bridge_type": "service_token_tool", "reason_code": exc.code},
                    ),
                )
                await self._agent_runs.commit()
                await self._record_bridge_audit(
                    context=context,
                    status=AuditStatus.DENIED,
                    error_code=SERVICE_TOKEN_TOOL_NOT_AVAILABLE,
                    metadata=_bridge_audit_metadata(
                        candidates=candidates,
                        tool_choice=tool_choice,
                        requested_model=requested_model,
                        status="denied",
                        reason_code=exc.code,
                        agent_run_id=agent_run.id,
                    ),
                )
                raise DomainError(
                    code=SERVICE_TOKEN_TOOL_NOT_AVAILABLE,
                    message="Requested tool is not available.",
                    details=_safe_error_details(context, SERVICE_TOKEN_TOOL_NOT_AVAILABLE),
                    status_code=403,
                ) from exc

            tool_call_id = await self._latest_tool_call_id(
                context=context,
                agent_run_id=agent_run.id,
            )
            execution = _build_execution(
                context=context,
                session_id=session_id,
                agent_run_id=agent_run.id,
                tool_call_id=tool_call_id,
                tool_name=selected_name,
                result=result,
            )
            await self._agent_runs.update_run_result(
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                run_id=agent_run.id,
                update=AgentRunUpdate(
                    status="completed" if execution.status == "success" else "failed",
                    termination_reason="OPENWEBUI_TOOL_EXECUTED",
                    steps_used=1,
                    tool_calls_used=1,
                    error_code=execution.error_code,
                    latency_ms=execution.latency_ms,
                    metadata={
                        "bridge_type": "service_token_tool",
                        "tool_name": execution.tool_name,
                        "tool_call_id": execution.tool_call_id,
                        "status": execution.status,
                        "error_code": execution.error_code,
                    },
                ),
            )
            await self._agent_runs.commit()
            await self._record_bridge_audit(
                context=context,
                status=(
                    AuditStatus.SUCCESS
                    if execution.status == "success"
                    else AuditStatus.FAILURE
                ),
                error_code=execution.error_code,
                metadata=_bridge_audit_metadata(
                    candidates=candidates,
                    tool_choice=tool_choice,
                    requested_model=requested_model,
                    status=execution.status,
                    reason_code="tool_executed",
                    agent_run_id=execution.agent_run_id,
                    tool_call_id=execution.tool_call_id,
                    tool_name=execution.tool_name,
                ),
            )
            return execution
        except Exception:
            await self._agent_runs.rollback()
            raise

    def _select_tool_name(
        self,
        *,
        candidates: Sequence[ServiceTokenToolBridgeCandidate],
        tool_choice: ServiceTokenToolChoice,
    ) -> str:
        names = tuple(candidate.name for candidate in candidates)
        if tool_choice.mode == "tool":
            if tool_choice.tool_name is None or tool_choice.tool_name not in names:
                raise DomainError(
                    code=SERVICE_TOKEN_TOOL_SELECTION_UNSUPPORTED,
                    message="Requested tool selection is not supported.",
                    details={},
                    status_code=400,
                )
            return tool_choice.tool_name
        if tool_choice.mode in {"auto", "required"} and len(names) == 1:
            return names[0]
        raise DomainError(
            code=SERVICE_TOKEN_TOOL_SELECTION_UNSUPPORTED,
            message="Requested tool selection is not supported.",
            details={},
            status_code=400,
        )

    async def _latest_tool_call_id(
        self,
        *,
        context: AuthenticatedRequestContext,
        agent_run_id: str,
    ) -> str:
        records = await self._tool_calls.list_by_agent_run(
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            agent_run_id=agent_run_id,
        )
        if not records:
            return f"{agent_run_id}-tool-call"
        return records[-1].id

    async def _record_bridge_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        status: AuditStatus,
        error_code: str | None,
        metadata: Mapping[str, object],
    ) -> None:
        try:
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action="agent.service_token_bridge",
                    resource=AuditResource(
                        type="service_token_bridge",
                        id=context.request_id,
                        metadata={"request_id": context.request_id, "trace_id": context.trace_id},
                    ),
                    status=status,
                    latency_ms=0.0,
                    error_code=error_code,
                    metadata=dict(metadata),
                    created_at=datetime.now(tz=UTC),
                )
            )
        except Exception:
            return


def _tool_arguments(*, tool_name: str, latest_user_message: str) -> dict[str, object]:
    if tool_name == "rag_search":
        return {"query": latest_user_message}
    if tool_name == "calculator":
        return {"expression": latest_user_message}
    if tool_name == "file_reader":
        return {"path": latest_user_message}
    return {"input": latest_user_message}


def _build_execution(
    *,
    context: AuthenticatedRequestContext,
    session_id: str | None,
    agent_run_id: str,
    tool_call_id: str,
    tool_name: str,
    result: ToolExecutionResult,
) -> ServiceTokenToolBridgeExecution:
    citations = _citations_from_result(result)
    status: Literal["success", "error"] = (
        "success" if result.status is ToolInvocationStatus.SUCCESS else "error"
    )
    error_code = _error_code_from_result(result)
    metadata = {
        "tool_bridge_status": status,
        "agent_run_id": agent_run_id,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "status": status,
        "latency_ms": result.latency_ms,
        "error_code": error_code,
        "audit_ref": f"/governance?request_id={context.request_id}#audit-explorer",
        "review_ref": f"/governance?request_id={context.request_id}#review-queue",
    }
    return ServiceTokenToolBridgeExecution(
        request_id=context.request_id,
        trace_id=context.trace_id,
        session_id=session_id or f"{context.request_id}-tool",
        assistant_text=_assistant_text(tool_name=tool_name, result=result, citations=citations),
        citations=citations,
        agent_run_id=agent_run_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        status=status,
        latency_ms=result.latency_ms,
        error_code=error_code,
        metadata=metadata,
    )


def _citations_from_result(result: ToolExecutionResult) -> tuple[ServiceTokenToolCitation, ...]:
    if result.tool_name != "rag_search" or result.output is None:
        return ()
    results = result.output.get("results")
    if not isinstance(results, Sequence) or isinstance(results, str | bytes):
        return ()
    citations: list[ServiceTokenToolCitation] = []
    for item in results:
        if not isinstance(item, Mapping):
            continue
        try:
            citations.append(
                ServiceTokenToolCitation(
                    document_id=str(item["document_id"]),
                    version_id=str(item["version_id"]),
                    chunk_id=str(item["chunk_id"]),
                    source_display_name=str(item["source_display_name"]),
                    source_type=str(item["source_type"]),
                    page_start=(
                        item.get("page_start")
                        if isinstance(item.get("page_start"), int)
                        else None
                    ),
                    page_end=(
                        item.get("page_end")
                        if isinstance(item.get("page_end"), int)
                        else None
                    ),
                    title_path=tuple(
                        str(part)
                        for part in item.get("title_path", ())
                        if str(part).strip()
                    ),
                    retrieval_method=str(item["retrieval_method"]),
                    score=float(item["score"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(citations)


def _assistant_text(
    *,
    tool_name: str,
    result: ToolExecutionResult,
    citations: Sequence[ServiceTokenToolCitation],
) -> str:
    if tool_name == "rag_search":
        if citations:
            return (
                f"rag_search returned {len(citations)} authorized citation-safe "
                f"result(s) from {citations[0].source_display_name}."
            )
        return "rag_search returned 0 authorized citation-safe result(s)."
    if tool_name == "calculator":
        value = None
        if isinstance(result.output, Mapping):
            raw = result.output.get("result")
            if isinstance(raw, str) and len(raw) <= 64:
                value = raw
        if value is not None:
            return f"calculator completed with safe result {value}."
        return "calculator completed."
    if tool_name == "file_reader":
        return "file_reader completed with an allowlisted safe summary."
    return f"{tool_name} completed with a safe observation summary."


def _error_code_from_result(result: ToolExecutionResult) -> str | None:
    if result.output is None:
        return None
    value = result.output.get("error_code")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _bridge_audit_metadata(
    *,
    candidates: Sequence[ServiceTokenToolBridgeCandidate],
    tool_choice: ServiceTokenToolChoice,
    requested_model: str,
    status: str,
    reason_code: str,
    agent_run_id: str | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> dict[str, object]:
    return {
        "requested_model": requested_model,
        "tool_declaration_count": len(candidates),
        "allowed_tool_count": len(candidates),
        "denied_tool_count": 0 if status == "success" else 1,
        "tool_bridge_status": status,
        "tool_choice_mode": tool_choice.mode,
        "tool_candidate_names": [candidate.name for candidate in candidates],
        "reason_code": reason_code,
        "agent_run_id": agent_run_id,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "status": status,
    }


def _safe_error_details(context: AuthenticatedRequestContext, error_code: str) -> dict[str, object]:
    return {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "error_code": error_code,
        "next_step": "Review governance-safe audit details with the request_id.",
    }


def _elapsed_ms(seconds: float) -> float:
    return round(max(seconds, 0.0) * 1000, 3)
