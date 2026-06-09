from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from time import perf_counter as default_perf_counter
from typing import Protocol

from pydantic import BaseModel, ValidationError

from packages.agent.dto import (
    ToolCallCreate,
    ToolCallRecorderPort,
    ToolDefinition,
    ToolExecutionResult,
    ToolInvocationStatus,
    ToolRateLimit,
    ToolRateLimitDecision,
    ToolRateLimitKey,
)
from packages.agent.exceptions import (
    TOOL_ALREADY_REGISTERED,
    TOOL_CALL_AUDIT_FAILED,
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

_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SAFE_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SENSITIVE_NAME_PARTS = (
    "absolute_path",
    "access_token",
    "apikey",
    "api_key",
    "authorization",
    "content",
    "cookie",
    "credential",
    "file_path",
    "local_path",
    "password",
    "private_key",
    "prompt",
    "secret",
    "token",
)
_REDACTED_KEY = "redacted_key"
_UNKNOWN_TOOL_RESOURCE_ID = "unknown_tool"


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
        tool_call_recorder: ToolCallRecorderPort | None = None,
    ) -> None:
        self._audit = audit
        self._rate_limiter = rate_limiter or InMemoryToolRateLimiter()
        self._perf_counter = perf_counter or default_perf_counter
        self._tool_call_recorder = tool_call_recorder
        self._definitions: dict[str, ToolDefinition] = {}

    @property
    def registered_tool_names(self) -> frozenset[str]:
        return frozenset(self._definitions)

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise AgentToolError(
                code=TOOL_ALREADY_REGISTERED,
                message="Tool is already registered.",
                details={"tool_name": definition.name, "error_code": TOOL_ALREADY_REGISTERED},
                status_code=409,
            )
        self._definitions[definition.name] = definition

    async def get(
        self,
        *,
        name: str,
        context: AuthenticatedRequestContext,
    ) -> ToolDefinition:
        definition = self._definitions.get(name)
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
                latency_ms=0.0,
                error_code=error.code,
                metadata=_base_metadata(tool_name=name, arguments={}, definition=None),
            )
            raise error
        return definition

    async def execute(
        self,
        *,
        name: str,
        arguments: object,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None = None,
    ) -> ToolExecutionResult:
        started = self._perf_counter()
        if self._tool_call_recorder is not None and agent_run_id is None:
            raise AgentToolError(
                code=TOOL_CALL_AUDIT_FAILED,
                message="Tool call audit persistence requires an Agent run id.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "tool_name": _safe_tool_name(name),
                    "error_code": TOOL_CALL_AUDIT_FAILED,
                },
                status_code=500,
            )
        definition = self._definitions.get(name)
        base_metadata = _base_metadata(tool_name=name, arguments=arguments, definition=definition)

        if definition is None:
            error = AgentToolError(
                code=TOOL_NOT_REGISTERED,
                message="Tool is not registered.",
                details={"tool_name": name, "error_code": TOOL_NOT_REGISTERED},
                status_code=404,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=None,
                arguments=arguments,
                status=ToolInvocationStatus.DENIED,
                latency_ms=latency_ms,
                error_code=error.code,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.DENIED,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata=base_metadata,
            )
            raise error

        if not isinstance(arguments, Mapping):
            error = AgentToolError(
                code=TOOL_INPUT_VALIDATION_FAILED,
                message="Tool input validation failed.",
                details={
                    "tool_name": name,
                    "error_fields": ("arguments",),
                    "error_code": TOOL_INPUT_VALIDATION_FAILED,
                },
                status_code=422,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                error_fields=("arguments",),
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "error_fields": ("arguments",),
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error

        try:
            payload = _validate_input(arguments=arguments, schema=definition.input_schema)
        except ToolSchemaValidationError as exc:
            error = AgentToolError(
                code=TOOL_INPUT_VALIDATION_FAILED,
                message="Tool input validation failed.",
                details={
                    "tool_name": name,
                    "error_fields": exc.error_fields,
                    "error_code": TOOL_INPUT_VALIDATION_FAILED,
                },
                status_code=422,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                error_fields=exc.error_fields,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "error_fields": exc.error_fields,
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error from exc
        except ValidationError as exc:
            error_fields = _validation_error_fields(exc)
            error = AgentToolError(
                code=TOOL_INPUT_VALIDATION_FAILED,
                message="Tool input validation failed.",
                details={
                    "tool_name": name,
                    "error_fields": error_fields,
                    "error_code": TOOL_INPUT_VALIDATION_FAILED,
                },
                status_code=422,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                error_fields=error_fields,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "error_fields": error_fields,
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
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.DENIED,
                latency_ms=latency_ms,
                error_code=error.code,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.DENIED,
                latency_ms=latency_ms,
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
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.DENIED,
                latency_ms=latency_ms,
                error_code=error.code,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.DENIED,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "status": ToolInvocationStatus.DENIED.value,
                },
            )
            raise error

        task: asyncio.Task[object] = asyncio.create_task(definition.handler(payload, context))
        try:
            raw_output = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=definition.timeout_seconds,
            )
        except TimeoutError as exc:
            task.cancel()
            task.add_done_callback(_consume_task_result)
            error = AgentToolError(
                code=TOOL_TIMEOUT,
                message="Tool execution timed out.",
                details={"tool_name": name, "error_code": TOOL_TIMEOUT},
                status_code=504,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error from exc
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_task_result)
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=TOOL_TIMEOUT,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=TOOL_TIMEOUT,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise
        except Exception as exc:
            error = AgentToolError(
                code=TOOL_HANDLER_FAILED,
                message="Tool handler failed.",
                details={"tool_name": name, "error_code": TOOL_HANDLER_FAILED},
                status_code=502,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
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
        except ToolSchemaValidationError as exc:
            error = AgentToolError(
                code=TOOL_OUTPUT_VALIDATION_FAILED,
                message="Tool output validation failed.",
                details={
                    "tool_name": name,
                    "error_fields": exc.error_fields,
                    "error_code": TOOL_OUTPUT_VALIDATION_FAILED,
                },
                status_code=502,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                raw_output=raw_output,
                error_fields=exc.error_fields,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "error_fields": exc.error_fields,
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error from exc
        except ValidationError as exc:
            error_fields = _validation_error_fields(exc)
            error = AgentToolError(
                code=TOOL_OUTPUT_VALIDATION_FAILED,
                message="Tool output validation failed.",
                details={
                    "tool_name": name,
                    "error_fields": error_fields,
                    "error_code": TOOL_OUTPUT_VALIDATION_FAILED,
                },
                status_code=502,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_tool_call(
                context=context,
                agent_run_id=agent_run_id,
                tool_name=name,
                definition=definition,
                arguments=arguments,
                status=ToolInvocationStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                raw_output=raw_output,
                error_fields=error_fields,
            )
            await self._record_audit(
                context=context,
                tool_name=name,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata={
                    **base_metadata,
                    "rate_limit": rate_metadata,
                    "error_fields": error_fields,
                    "status": ToolInvocationStatus.FAILURE.value,
                },
            )
            raise error from exc

        output_data = output.model_dump(mode="json")
        latency_ms = _elapsed_ms(self._perf_counter() - started)
        invocation_status = _output_invocation_status(output_data)
        audit_status = (
            AuditStatus.FAILURE
            if invocation_status is ToolInvocationStatus.FAILURE
            else AuditStatus.SUCCESS
        )
        output_error_code = _output_error_code(output_data)
        metadata = {
            **base_metadata,
            "result_keys": tuple(sorted(output_data.keys())),
            "rate_limit": rate_metadata,
            "status": invocation_status.value,
        }
        await self._record_tool_call(
            context=context,
            agent_run_id=agent_run_id,
            tool_name=name,
            definition=definition,
            arguments=arguments,
            status=invocation_status,
            latency_ms=latency_ms,
            error_code=output_error_code,
            output=output_data,
        )
        await self._record_audit(
            context=context,
            tool_name=name,
            status=audit_status,
            latency_ms=latency_ms,
            error_code=output_error_code,
            metadata=metadata,
        )
        return ToolExecutionResult(
            tool_name=name,
            status=invocation_status,
            output=output_data,
            latency_ms=latency_ms,
            metadata=metadata,
        )

    async def _record_tool_call(
        self,
        *,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None,
        tool_name: str,
        definition: ToolDefinition | None,
        arguments: object,
        status: ToolInvocationStatus,
        latency_ms: float,
        error_code: str | None,
        output: Mapping[str, object] | None = None,
        raw_output: object | None = None,
        error_fields: tuple[str, ...] = (),
    ) -> None:
        if self._tool_call_recorder is None:
            return
        if agent_run_id is None:
            raise AgentToolError(
                code=TOOL_CALL_AUDIT_FAILED,
                message="Tool call audit persistence requires an Agent run id.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "tool_name": _safe_tool_name(tool_name),
                    "error_code": TOOL_CALL_AUDIT_FAILED,
                },
                status_code=500,
            )
        try:
            await self._tool_call_recorder.record_tool_call(
                ToolCallCreate(
                    agent_run_id=agent_run_id,
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    tool_name=_safe_tool_name(tool_name),
                    permission=definition.permission if definition is not None else None,
                    status=status.value,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    arguments_summary=_arguments_summary(arguments),
                    result_summary=_result_summary(
                        status=status,
                        error_code=error_code,
                        output=output,
                        raw_output=raw_output,
                        error_fields=error_fields,
                    ),
                )
            )
        except Exception as exc:
            logger.warning(
                "agent.tool_call.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "tool_name": _safe_tool_name(tool_name),
                    "exception_type": type(exc).__name__,
                    "error_code": TOOL_CALL_AUDIT_FAILED,
                },
            )
            raise AgentToolError(
                code=TOOL_CALL_AUDIT_FAILED,
                message="Tool call audit persistence failed.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "agent_run_id": agent_run_id,
                    "tool_name": _safe_tool_name(tool_name),
                    "error_code": TOOL_CALL_AUDIT_FAILED,
                },
                status_code=500,
            ) from exc

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
        try:
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action="agent.tool.execute",
                    resource=AuditResource(type="tool", id=_safe_tool_resource_id(tool_name)),
                    status=status,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    metadata=dict(metadata),
                )
            )
            return
        except Exception:
            logger.warning(
                "agent.tool.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "tool_name": _safe_tool_name(tool_name),
                    "audit_status": status.value,
                    "error_code": error_code,
                },
                exc_info=True,
            )


class ToolSchemaValidationError(ValueError):
    def __init__(self, error_fields: tuple[str, ...]) -> None:
        self.error_fields = error_fields
        super().__init__("tool schema validation failed")


def _validate_input(
    *,
    arguments: Mapping[str, object],
    schema: type[BaseModel],
) -> BaseModel:
    extra_fields = _extra_fields(arguments, schema)
    if extra_fields:
        raise ToolSchemaValidationError(extra_fields)
    return schema.model_validate(dict(arguments))


def _validate_output(*, raw_output: object, schema: type[BaseModel]) -> BaseModel:
    if isinstance(raw_output, BaseModel):
        output_data = _model_field_data(raw_output)
    elif isinstance(raw_output, Mapping):
        output_data = {str(key): value for key, value in raw_output.items()}
    else:
        return schema.model_validate(raw_output)

    extra_fields = _extra_fields(output_data, schema)
    if extra_fields:
        raise ToolSchemaValidationError(extra_fields)
    return schema.model_validate(output_data)


def _model_field_data(model: BaseModel) -> dict[str, object]:
    data = {field_name: getattr(model, field_name) for field_name in model.__class__.model_fields}
    if model.model_extra:
        data.update(model.model_extra)
    return data


def _base_metadata(
    *,
    tool_name: str,
    arguments: object,
    definition: ToolDefinition | None,
) -> dict[str, object]:
    return {
        "tool_name": _safe_tool_name(tool_name),
        "permission": definition.permission if definition is not None else None,
        "argument_keys": _safe_argument_keys(arguments),
        "timeout_seconds": definition.timeout_seconds if definition is not None else None,
        "status": ToolInvocationStatus.FAILURE.value,
    }


def _arguments_summary(arguments: object) -> dict[str, object]:
    if not isinstance(arguments, Mapping):
        return {
            "argument_keys": [],
            "argument_count": 0,
            "argument_shape": "non_mapping",
        }
    return {
        "argument_keys": list(_safe_argument_keys(arguments)),
        "argument_count": len(arguments),
        "argument_shape": "mapping",
    }


def _result_summary(
    *,
    status: ToolInvocationStatus,
    error_code: str | None,
    output: Mapping[str, object] | None = None,
    raw_output: object | None = None,
    error_fields: tuple[str, ...] = (),
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status.value,
        "error_code": error_code,
    }
    if error_fields:
        result["error_fields"] = list(error_fields)
    output_mapping = (
        output
        if output is not None
        else raw_output if isinstance(raw_output, Mapping) else None
    )
    if isinstance(output_mapping, Mapping):
        result["result_keys"] = list(_safe_result_keys(output_mapping))
        result["result_count"] = len(output_mapping)
    else:
        result["result_keys"] = []
        result["result_count"] = 0
    return result


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


def _extra_fields(data: Mapping[str, object], schema: type[BaseModel]) -> tuple[str, ...]:
    allowed = set(schema.model_fields)
    extras = {str(key) for key in data if str(key) not in allowed}
    return tuple(sorted(_safe_argument_key(field) for field in extras))


def _output_invocation_status(output_data: Mapping[str, object]) -> ToolInvocationStatus:
    if output_data.get("status") == ToolInvocationStatus.FAILURE.value:
        return ToolInvocationStatus.FAILURE
    if output_data.get("status") == "error":
        return ToolInvocationStatus.FAILURE
    return ToolInvocationStatus.SUCCESS


def _output_error_code(output_data: Mapping[str, object]) -> str | None:
    if _output_invocation_status(output_data) is not ToolInvocationStatus.FAILURE:
        return None
    error_code = output_data.get("error_code")
    if not isinstance(error_code, str):
        return None
    normalized = error_code.strip()
    if not _is_safe_observable_name(normalized):
        return None
    return normalized


def _safe_argument_keys(arguments: object) -> tuple[str, ...]:
    if not isinstance(arguments, Mapping):
        return ()
    return tuple(sorted({_safe_argument_key(str(key)) for key in arguments}))


def _safe_result_keys(output: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(sorted({_safe_argument_key(str(key)) for key in output}))


def _safe_argument_key(key: str) -> str:
    normalized = key.strip()
    if not _is_safe_observable_name(normalized):
        return _REDACTED_KEY
    return normalized


def _safe_tool_name(tool_name: str) -> str:
    normalized = tool_name.strip()
    if not _SAFE_TOOL_NAME_PATTERN.fullmatch(normalized) or _is_sensitive_name(normalized):
        return _UNKNOWN_TOOL_RESOURCE_ID
    return normalized


def _safe_tool_resource_id(tool_name: str) -> str:
    return _safe_tool_name(tool_name)


def _is_safe_observable_name(value: str) -> bool:
    return bool(_SAFE_IDENTIFIER_PATTERN.fullmatch(value)) and not _is_sensitive_name(value)


def _is_sensitive_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9_]", "_", value.strip().lower())
    compact = normalized.replace("_", "")
    return any(
        part in normalized or part.replace("_", "") in compact
        for part in _SENSITIVE_NAME_PARTS
    )


def _consume_task_result(task: asyncio.Task[object]) -> None:
    try:
        task.result()
    except BaseException:
        return


def _elapsed_ms(elapsed_seconds: float) -> float:
    return round(max(elapsed_seconds, 0.0) * 1000, 3)
