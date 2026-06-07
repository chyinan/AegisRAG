from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Mapping
from contextlib import suppress
from time import perf_counter as default_perf_counter
from typing import Protocol

from pydantic import BaseModel, ValidationError

from packages.agent.dto import (
    ToolDefinition,
    ToolExecutionResult,
    ToolInvocationStatus,
    ToolRateLimit,
    ToolRateLimitDecision,
    ToolRateLimitKey,
)
from packages.agent.exceptions import (
    TOOL_ALREADY_REGISTERED,
    TOOL_HANDLER_FAILED,
    TOOL_INPUT_VALIDATION_FAILED,
    TOOL_NOT_REGISTERED,
    TOOL_OUTPUT_VALIDATION_FAILED,
    TOOL_PERMISSION_DENIED,
    TOOL_RATE_LIMITED,
    TOOL_TIMEOUT,
    AgentToolError,
)
from packages.agent.policies import has_tool_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext

logger = logging.getLogger(__name__)


class ToolRateLimiter(Protocol):
    async def acquire(
        self,
        *,
        key: ToolRateLimitKey,
        limit: ToolRateLimit,
    ) -> ToolRateLimitDecision: ...


class InMemoryToolRateLimiter:
    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or default_perf_counter
        self._calls: dict[ToolRateLimitKey, list[float]] = defaultdict(list)

    async def acquire(
        self,
        *,
        key: ToolRateLimitKey,
        limit: ToolRateLimit,
    ) -> ToolRateLimitDecision:
        now = self._clock()
        window_start = now - limit.window_seconds
        calls = [timestamp for timestamp in self._calls[key] if timestamp > window_start]
        self._calls[key] = calls

        if len(calls) >= limit.max_calls:
            oldest = min(calls)
            return ToolRateLimitDecision(
                allowed=False,
                remaining=0,
                reset_after_seconds=max((oldest + limit.window_seconds) - now, 0.0),
            )

        calls.append(now)
        remaining = max(limit.max_calls - len(calls), 0)
        return ToolRateLimitDecision(
            allowed=True,
            remaining=remaining,
            reset_after_seconds=limit.window_seconds,
        )


class ToolRegistry:
    def __init__(
        self,
        *,
        audit: AuditPort,
        rate_limiter: ToolRateLimiter | None = None,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._audit = audit
        self._rate_limiter = rate_limiter or InMemoryToolRateLimiter()
        self._perf_counter = perf_counter or default_perf_counter
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise AgentToolError(
                code=TOOL_ALREADY_REGISTERED,
                message="Tool is already registered.",
                details={"tool_name": definition.name, "error_code": TOOL_ALREADY_REGISTERED},
                status_code=409,
            )
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        definition = self._definitions.get(name)
        if definition is None:
            raise AgentToolError(
                code=TOOL_NOT_REGISTERED,
                message="Tool is not registered.",
                details={"tool_name": name, "error_code": TOOL_NOT_REGISTERED},
                status_code=404,
            )
        return definition

    async def execute(
        self,
        *,
        name: str,
        arguments: Mapping[str, object],
        context: AuthenticatedRequestContext,
    ) -> ToolExecutionResult:
        started = self._perf_counter()
        definition = self._definitions.get(name)
        base_metadata = _base_metadata(tool_name=name, arguments=arguments, definition=definition)

        if definition is None:
            error = AgentToolError(
                code=TOOL_NOT_REGISTERED,
                message="Tool is not registered.",
                details={"tool_name": name, "error_code": TOOL_NOT_REGISTERED},
                status_code=404,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.DENIED,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata=base_metadata,
            )
            raise error

        try:
            payload = definition.input_schema.model_validate(dict(arguments))
        except ValidationError as exc:
            error = AgentToolError(
                code=TOOL_INPUT_VALIDATION_FAILED,
                message="Tool input validation failed.",
                details={
                    "tool_name": name,
                    "error_fields": _validation_error_fields(exc),
                    "error_code": TOOL_INPUT_VALIDATION_FAILED,
                },
                status_code=422,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "error_fields": _validation_error_fields(exc),
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error from exc

        if not has_tool_permission(context.auth, definition.permission):
            error = AgentToolError(
                code=TOOL_PERMISSION_DENIED,
                message="Tool permission is required.",
                details={
                    "tool_name": name,
                    "required_permission": definition.permission,
                    "error_code": TOOL_PERMISSION_DENIED,
                },
                status_code=403,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.DENIED,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata={**base_metadata, "status": ToolInvocationStatus.DENIED.value},
            )
            raise error

        rate_key = ToolRateLimitKey(
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            tool_name=name,
        )
        decision = await self._rate_limiter.acquire(key=rate_key, limit=definition.rate_limit)
        rate_metadata = _rate_limit_metadata(definition.rate_limit, decision)
        if not decision.allowed:
            error = AgentToolError(
                code=TOOL_RATE_LIMITED,
                message="Tool rate limit exceeded.",
                details={"tool_name": name, "error_code": TOOL_RATE_LIMITED},
                status_code=429,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.DENIED,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "status": ToolInvocationStatus.DENIED.value,
                },
            )
            raise error

        try:
            raw_output = await asyncio.wait_for(
                definition.handler(payload, context),
                timeout=definition.timeout_seconds,
            )
        except TimeoutError as exc:
            error = AgentToolError(
                code=TOOL_TIMEOUT,
                message="Tool execution timed out.",
                details={"tool_name": name, "error_code": TOOL_TIMEOUT},
                status_code=504,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error from exc
        except Exception as exc:
            error = AgentToolError(
                code=TOOL_HANDLER_FAILED,
                message="Tool handler failed.",
                details={"tool_name": name, "error_code": TOOL_HANDLER_FAILED},
                status_code=502,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error from exc

        try:
            output = _validate_output(raw_output=raw_output, schema=definition.output_schema)
        except ValidationError as exc:
            error = AgentToolError(
                code=TOOL_OUTPUT_VALIDATION_FAILED,
                message="Tool output validation failed.",
                details={
                    "tool_name": name,
                    "error_fields": _validation_error_fields(exc),
                    "error_code": TOOL_OUTPUT_VALIDATION_FAILED,
                },
                status_code=502,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "error_fields": _validation_error_fields(exc),
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error from exc

        output_data = output.model_dump()
        latency_ms = _elapsed_ms(self._perf_counter() - started)
        metadata = {
            **base_metadata,
            "result_keys": tuple(sorted(output_data.keys())),
            "rate_limit": rate_metadata,
            "status": ToolInvocationStatus.SUCCESS.value,
        }
        await self._record_audit(
            context=context,
            tool_name=name,
            status=AuditStatus.SUCCESS,
            latency_ms=latency_ms,
            error_code=None,
            metadata=metadata,
        )
        return ToolExecutionResult(
            tool_name=name,
            status=ToolInvocationStatus.SUCCESS,
            output=output_data,
            latency_ms=latency_ms,
            metadata=metadata,
        )

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        tool_name: str,
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
                    action="agent.tool.execute",
                    resource=AuditResource(type="tool", id=tool_name),
                    status=status,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    metadata=dict(metadata),
                )
            )
            return
        logger.warning(
            "agent.tool.audit_failed",
            extra={
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "tenant_id": context.auth.tenant_id,
                "user_id": context.auth.user_id,
                "tool_name": tool_name,
                "audit_status": status.value,
                "error_code": error_code,
            },
            exc_info=True,
        )


def _validate_output(*, raw_output: object, schema: type[BaseModel]) -> BaseModel:
    if isinstance(raw_output, schema):
        return raw_output
    return schema.model_validate(raw_output)


def _base_metadata(
    *,
    tool_name: str,
    arguments: Mapping[str, object],
    definition: ToolDefinition | None,
) -> dict[str, object]:
    return {
        "tool_name": tool_name,
        "permission": definition.permission if definition is not None else None,
        "argument_keys": tuple(sorted(str(key) for key in arguments)),
        "timeout_seconds": definition.timeout_seconds if definition is not None else None,
        "status": ToolInvocationStatus.FAILURE.value,
    }


def _rate_limit_metadata(
    limit: ToolRateLimit,
    decision: ToolRateLimitDecision,
) -> dict[str, object]:
    return {
        "max_calls": limit.max_calls,
        "window_seconds": limit.window_seconds,
        "remaining": decision.remaining,
        "reset_after_seconds": decision.reset_after_seconds,
    }


def _validation_error_fields(error: ValidationError) -> tuple[str, ...]:
    fields = {".".join(str(part) for part in item["loc"]) for item in error.errors()}
    return tuple(sorted(field for field in fields if field))


def _elapsed_ms(elapsed_seconds: float) -> float:
    return round(max(elapsed_seconds, 0.0) * 1000, 3)
