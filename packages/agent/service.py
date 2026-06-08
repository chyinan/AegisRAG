from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from contextlib import suppress
from time import perf_counter as default_perf_counter
from typing import Protocol

from packages.agent.dto import (
    AgentRunCommand,
    AgentRunCreate,
    AgentRunRecord,
    AgentRunResponse,
    AgentRunUpdate,
)
from packages.agent.exceptions import (
    AGENT_RUN_FAILED,
    AGENT_RUN_FORBIDDEN,
    AGENT_RUN_STORAGE_FAILED,
    AgentRunError,
    agent_run_storage_failed,
)
from packages.agent.runtime import AgentRunConfig, AgentRunResult
from packages.auth.policies import has_agent_run_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError

_FORBIDDEN_METADATA_KEYS = {
    "absolute_path",
    "access_token",
    "answer",
    "api_key",
    "authorization",
    "content",
    "file_path",
    "hidden_reasoning",
    "local_path",
    "messages",
    "password",
    "prompt",
    "query",
    "raw_output",
    "raw_tool_arguments",
    "raw_tool_output",
    "secret",
    "thought",
    "token",
    "tool_results",
}


class AgentRunRepositoryPort(Protocol):
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


class AgentRuntimePort(Protocol):
    async def run(self, *, context: AuthenticatedRequestContext) -> AgentRunResult: ...


RuntimeFactory = Callable[[AgentRunConfig], AgentRuntimePort]


class AgentRunApplicationService:
    def __init__(
        self,
        *,
        repository: AgentRunRepositoryPort,
        runtime_factory: RuntimeFactory,
        audit: AuditPort,
        default_max_steps: int,
        default_max_tool_calls: int,
        default_timeout_seconds: float,
        repeated_action_threshold: int,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._repository = repository
        self._runtime_factory = runtime_factory
        self._audit = audit
        self._default_max_steps = default_max_steps
        self._default_max_tool_calls = default_max_tool_calls
        self._default_timeout_seconds = default_timeout_seconds
        self._repeated_action_threshold = repeated_action_threshold
        self._perf_counter = perf_counter or default_perf_counter

    async def run(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: AgentRunCommand,
    ) -> AgentRunResponse:
        started = self._perf_counter()
        if not has_agent_run_permission(context.auth):
            error = AgentRunError(
                code=AGENT_RUN_FORBIDDEN,
                message="Agent run permission is required.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "required_permissions": ["agent:run"],
                    "error_code": AGENT_RUN_FORBIDDEN,
                },
                status_code=403,
            )
            await self._record_audit(
                context=context,
                run_id=context.request_id,
                action="agent.run.denied",
                status=AuditStatus.DENIED,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata={"required_permissions": ["agent:run"]},
            )
            raise error

        config = self._config(command)
        try:
            created = await self._repository.create_run(
                AgentRunCreate(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    created_by=context.auth.user_id,
                    status="running",
                    max_steps=config.max_steps,
                    max_tool_calls=config.max_tool_calls,
                    timeout_seconds=config.timeout_seconds,
                    input_summary=_input_summary(command.input),
                    metadata=_create_metadata(command=command, config=config),
                )
            )
            runtime = self._runtime_factory(config)
            result = await runtime.run(context=context)
            updated = await self._update_from_result(
                context=context,
                run_id=created.id,
                result=result,
            )
            await self._repository.commit()
            await self._record_audit(
                context=context,
                run_id=updated.id,
                action="agent.run.complete",
                status=_audit_status(updated.status),
                latency_ms=updated.latency_ms or _elapsed_ms(self._perf_counter() - started),
                error_code=updated.error_code,
                metadata=_audit_metadata(updated.metadata, agent_run_id=updated.id),
            )
            return AgentRunResponse.from_record(updated)
        except AgentRunError as exc:
            await self._safe_rollback()
            if exc.code == AGENT_RUN_STORAGE_FAILED:
                raise agent_run_storage_failed(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    reason="run_storage_failed",
                ) from exc
            raise
        except DomainError:
            await self._safe_rollback()
            raise
        except Exception as exc:
            await self._safe_rollback()
            raise AgentRunError(
                code=AGENT_RUN_FAILED,
                message="Agent run failed.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "error_code": AGENT_RUN_FAILED,
                },
                status_code=500,
            ) from exc

    async def _update_from_result(
        self,
        *,
        context: AuthenticatedRequestContext,
        run_id: str,
        result: AgentRunResult,
    ) -> AgentRunRecord:
        return await self._repository.update_run_result(
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            run_id=run_id,
            update=AgentRunUpdate(
                status=result.status.value,
                termination_reason=result.termination_reason.value,
                steps_used=result.steps_used,
                tool_calls_used=result.tool_calls_used,
                error_code=result.error_code,
                latency_ms=result.latency_ms,
                metadata=_result_metadata(result),
            ),
        )

    def _config(self, command: AgentRunCommand) -> AgentRunConfig:
        return AgentRunConfig(
            max_steps=command.max_steps or self._default_max_steps,
            max_tool_calls=command.max_tool_calls
            if command.max_tool_calls is not None
            else self._default_max_tool_calls,
            timeout_seconds=command.timeout_seconds or self._default_timeout_seconds,
            repeated_action_threshold=self._repeated_action_threshold,
        )

    async def _safe_rollback(self) -> None:
        with suppress(Exception):
            await self._repository.rollback()

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        run_id: str,
        action: str,
        status: AuditStatus,
        latency_ms: float,
        error_code: str | None,
        metadata: Mapping[str, object],
    ) -> None:
        with suppress(Exception):
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action=action,
                    resource=AuditResource(
                        type="agent_run",
                        id=run_id,
                        metadata={"agent_run_id": run_id},
                    ),
                    status=status,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    metadata=dict(metadata),
                )
            )


def _input_summary(value: str) -> dict[str, object]:
    return {
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _create_metadata(*, command: AgentRunCommand, config: AgentRunConfig) -> dict[str, object]:
    return _safe_metadata(
        {
            **command.metadata,
            "max_steps": config.max_steps,
            "max_tool_calls": config.max_tool_calls,
            "timeout_seconds": config.timeout_seconds,
            "input_length": len(command.input),
        }
    )


def _result_metadata(result: AgentRunResult) -> dict[str, object]:
    return _safe_metadata(
        {
            **dict(result.metadata),
            "safe_counts": {
                "observation_count": len(result.observations),
                "metadata_count": len(result.metadata),
            },
            "termination_reason": result.termination_reason.value,
            "steps_used": result.steps_used,
            "tool_calls_used": result.tool_calls_used,
        }
    )


def _audit_metadata(metadata: Mapping[str, object], *, agent_run_id: str) -> dict[str, object]:
    return _safe_metadata({"agent_run_id": agent_run_id, **dict(metadata)})


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if _is_forbidden_key(key_text):
            continue
        if isinstance(value, Mapping):
            nested = _safe_metadata(value)
            if nested:
                safe[key_text] = nested
        elif isinstance(value, list | tuple):
            safe[key_text] = [
                item
                for item in value
                if item is None or isinstance(item, str | int | float | bool)
            ][:20]
        elif value is None or isinstance(value, str | int | float | bool):
            safe[key_text] = value
    return safe


def _is_forbidden_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return normalized in _FORBIDDEN_METADATA_KEYS or compact in {
        item.replace("_", "") for item in _FORBIDDEN_METADATA_KEYS
    }


def _audit_status(status: str) -> AuditStatus:
    if status == "completed":
        return AuditStatus.SUCCESS
    if status == "stopped":
        return AuditStatus.DENIED
    return AuditStatus.FAILURE


def _elapsed_ms(elapsed_seconds: float) -> float:
    return round(max(elapsed_seconds, 0.0) * 1000, 3)
