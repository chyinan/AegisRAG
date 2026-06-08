from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import Callable, Coroutine, Mapping, Sequence
from enum import StrEnum
from time import perf_counter as default_perf_counter
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.agent.dto import (
    AGENT_FINAL_ANSWER_VALIDATION_FAILED,
    AgentCitationRef,
    FinalAnswerValidationRequest,
    FinalAnswerValidationResult,
    ToolExecutionResult,
    ToolInvocationStatus,
)
from packages.agent.exceptions import AgentToolError
from packages.agent.final_answer import (
    FINAL_ANSWER_VALIDATION_ACTION,
    FinalAnswerValidator,
    StrictFinalAnswerValidator,
)
from packages.agent.registry import ToolRegistry
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.source_metadata import safe_source_display_name

logger = logging.getLogger(__name__)

_TASK_CANCEL_GRACE_SECONDS = 0.05
MAX_STEPS_REACHED = "MAX_STEPS_REACHED"
MAX_TOOL_CALLS_REACHED = "MAX_TOOL_CALLS_REACHED"
AGENT_TIMEOUT = "AGENT_TIMEOUT"
REPEATED_ACTION_DETECTED = "REPEATED_ACTION_DETECTED"
AGENT_STEPPER_FAILED = "AGENT_STEPPER_FAILED"
AGENT_TOOL_FAILED = "AGENT_TOOL_FAILED"
TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
FINAL_ANSWER_VALIDATION_FAILED = "FINAL_ANSWER_VALIDATION_FAILED"

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
_T = TypeVar("_T")


class AgentActionType(StrEnum):
    TOOL_CALL = "tool_call"
    FINAL_ANSWER = "final_answer"


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class AgentTerminationReason(StrEnum):
    FINAL_ANSWER = "FINAL_ANSWER"
    FINAL_ANSWER_VALIDATION_FAILED = FINAL_ANSWER_VALIDATION_FAILED
    MAX_STEPS_REACHED = MAX_STEPS_REACHED
    MAX_TOOL_CALLS_REACHED = MAX_TOOL_CALLS_REACHED
    AGENT_TIMEOUT = AGENT_TIMEOUT
    REPEATED_ACTION_DETECTED = REPEATED_ACTION_DETECTED
    AGENT_STEPPER_FAILED = AGENT_STEPPER_FAILED
    AGENT_TOOL_FAILED = AGENT_TOOL_FAILED


class AgentRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    repeated_action_threshold: int = Field(gt=0)

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        return value


class AgentStepDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: AgentActionType
    tool_name: str | None = None
    arguments: Mapping[str, object] = Field(default_factory=dict)
    final_answer: str | None = None
    final_citations: tuple[AgentCitationRef, ...] = ()

    @classmethod
    def tool_call(cls, tool_name: str, arguments: Mapping[str, object]) -> AgentStepDecision:
        return cls(action=AgentActionType.TOOL_CALL, tool_name=tool_name, arguments=arguments)

    @model_validator(mode="after")
    def _decision_must_match_action(self) -> AgentStepDecision:
        if self.action is AgentActionType.TOOL_CALL:
            if self.tool_name is None or not self.tool_name.strip():
                raise ValueError("tool_name is required for tool_call decisions")
            if self.final_answer is not None:
                raise ValueError("final_answer is not allowed for tool_call decisions")
            if self.final_citations:
                raise ValueError("final_citations are not allowed for tool_call decisions")
            return self

        if self.final_answer is None:
            raise ValueError("final_answer is required for final_answer decisions")
        if self.tool_name is not None:
            raise ValueError("tool_name is not allowed for final_answer decisions")
        if self.arguments:
            raise ValueError("arguments are not allowed for final_answer decisions")
        return self


class AgentObservationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    status: ToolInvocationStatus
    output_keys: tuple[str, ...] = ()
    citation_refs: tuple[AgentCitationRef, ...] = ()
    error_code: str | None = None
    result_status: str | None = None
    latency_ms: float = Field(ge=0)
    metadata: Mapping[str, object] = Field(default_factory=dict)


class AgentRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    steps_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    observations: tuple[AgentObservationSummary, ...] = ()


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AgentRunStatus
    termination_reason: AgentTerminationReason
    steps_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    final_answer: str | None = None
    final_citations: tuple[AgentCitationRef, ...] = ()
    error_code: str | None = None
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    observations: tuple[AgentObservationSummary, ...] = ()
    latency_ms: float = Field(ge=0)
    metadata: Mapping[str, object] = Field(default_factory=dict)


class RepeatedActionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    triggered: bool
    repeat_count: int = Field(ge=1)
    threshold: int = Field(gt=0)
    action_hash: str
    metadata: Mapping[str, object]


class AgentStepper(Protocol):
    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision: ...


class RepeatedActionDetector:
    def __init__(self, *, threshold: int) -> None:
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        self._threshold = threshold
        self._counts: dict[str, int] = {}

    def observe(self, *, tool_name: str, arguments: Mapping[str, object]) -> RepeatedActionCheck:
        action_hash = self._action_hash(tool_name=tool_name, arguments=arguments)
        repeat_count = self._counts.get(action_hash, 0) + 1
        self._counts[action_hash] = repeat_count
        metadata: dict[str, object] = {
            "tool_name": _safe_tool_name(tool_name),
            "argument_keys": list(_safe_argument_keys(arguments)),
            "action_hash": action_hash,
            "repeat_count": repeat_count,
            "threshold": self._threshold,
            "repeated_action_detected": repeat_count >= self._threshold,
        }
        return RepeatedActionCheck(
            triggered=repeat_count >= self._threshold,
            repeat_count=repeat_count,
            threshold=self._threshold,
            action_hash=action_hash,
            metadata=metadata,
        )

    def _action_hash(self, *, tool_name: str, arguments: Mapping[str, object]) -> str:
        payload = {
            "tool_name": tool_name,
            "arguments": _canonicalize(arguments),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class AgentRuntime:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        stepper: AgentStepper,
        audit: AuditPort | None,
        config: AgentRunConfig,
        agent_run_id: str | None = None,
        final_answer_validator: FinalAnswerValidator | None = None,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._registry = registry
        self._stepper = stepper
        self._audit = audit
        self._config = config
        self._agent_run_id = agent_run_id
        self._final_answer_validator = final_answer_validator
        self._perf_counter = perf_counter or default_perf_counter

    async def run(self, *, context: AuthenticatedRequestContext) -> AgentRunResult:
        started = self._perf_counter()
        deadline = started + self._config.timeout_seconds
        steps_used = 0
        tool_calls_used = 0
        observations: list[AgentObservationSummary] = []
        repeated_detector = RepeatedActionDetector(
            threshold=self._config.repeated_action_threshold,
        )

        while True:
            now = self._perf_counter()
            if steps_used >= self._config.max_steps:
                return await self._finish(
                    context=context,
                    started=started,
                    now=now,
                    status=AgentRunStatus.STOPPED,
                    termination_reason=AgentTerminationReason.MAX_STEPS_REACHED,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    action="agent.runtime.limit",
                    audit_status=AuditStatus.DENIED,
                    metadata=_limit_metadata(
                        reason=MAX_STEPS_REACHED,
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                    ),
                )
            if now >= deadline:
                return await self._timeout_result(
                    context=context,
                    started=started,
                    now=now,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                )

            state = _build_state(
                context=context,
                steps_used=steps_used,
                tool_calls_used=tool_calls_used,
                observations=observations,
            )
            try:
                raw_decision = await _await_with_timeout(
                    self._stepper.next_step(state),
                    timeout_seconds=deadline - now,
                )
                decision = AgentStepDecision.model_validate(raw_decision)
            except TimeoutError:
                return await self._timeout_result(
                    context=context,
                    started=started,
                    now=deadline,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                )
            except Exception:
                return await self._finish(
                    context=context,
                    started=started,
                    now=self._perf_counter(),
                    status=AgentRunStatus.FAILED,
                    termination_reason=AgentTerminationReason.AGENT_STEPPER_FAILED,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    action="agent.runtime.run",
                    audit_status=AuditStatus.FAILURE,
                    metadata=_base_run_metadata(
                        error_code=AGENT_STEPPER_FAILED,
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                    ),
                )

            steps_used += 1
            if decision.action is AgentActionType.FINAL_ANSWER:
                validation = await self._validate_final_answer(
                    context=context,
                    answer=decision.final_answer or "",
                    citations=decision.final_citations,
                    observations=observations,
                    deadline=deadline,
                )
                if validation.error_code == AGENT_TIMEOUT:
                    return await self._timeout_result(
                        context=context,
                        started=started,
                        now=self._perf_counter(),
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                        observations=observations,
                        metadata={
                            **_base_run_metadata(
                                error_code=AGENT_TIMEOUT,
                                steps_used=steps_used,
                                tool_calls_used=tool_calls_used,
                            ),
                            "final_answer_validation": dict(validation.metadata),
                        },
                    )
                if validation.status in ("valid", "degraded"):
                    return await self._finish(
                        context=context,
                        started=started,
                        now=self._perf_counter(),
                        status=AgentRunStatus.COMPLETED,
                        termination_reason=AgentTerminationReason.FINAL_ANSWER,
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                        observations=observations,
                        action="agent.runtime.run",
                        audit_status=AuditStatus.SUCCESS,
                        final_answer=validation.answer,
                        final_citations=validation.citations,
                        metadata={
                            **_base_run_metadata(
                                error_code=None,
                                steps_used=steps_used,
                                tool_calls_used=tool_calls_used,
                            ),
                            "final_answer_validation": dict(validation.metadata),
                        },
                    )
                return await self._finish(
                    context=context,
                    started=started,
                    now=self._perf_counter(),
                    status=AgentRunStatus.FAILED,
                    termination_reason=AgentTerminationReason.FINAL_ANSWER_VALIDATION_FAILED,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    action="agent.runtime.run",
                    audit_status=AuditStatus.FAILURE,
                    error_code=validation.error_code or AGENT_FINAL_ANSWER_VALIDATION_FAILED,
                    metadata={
                        **_base_run_metadata(
                            error_code=validation.error_code
                            or AGENT_FINAL_ANSWER_VALIDATION_FAILED,
                            steps_used=steps_used,
                            tool_calls_used=tool_calls_used,
                        ),
                        "final_answer_validation": dict(validation.metadata),
                    },
                )

            if tool_calls_used >= self._config.max_tool_calls:
                return await self._finish(
                    context=context,
                    started=started,
                    now=self._perf_counter(),
                    status=AgentRunStatus.STOPPED,
                    termination_reason=AgentTerminationReason.MAX_TOOL_CALLS_REACHED,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    action="agent.runtime.limit",
                    audit_status=AuditStatus.DENIED,
                    metadata=_limit_metadata(
                        reason=MAX_TOOL_CALLS_REACHED,
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                    ),
                )

            now = self._perf_counter()
            if now >= deadline:
                return await self._timeout_result(
                    context=context,
                    started=started,
                    now=now,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    metadata=_limit_metadata(
                        reason=AGENT_TIMEOUT,
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                    ),
                )

            assert decision.tool_name is not None
            repeated_check = repeated_detector.observe(
                tool_name=decision.tool_name,
                arguments=decision.arguments,
            )
            if repeated_check.triggered:
                return await self._finish(
                    context=context,
                    started=started,
                    now=self._perf_counter(),
                    status=AgentRunStatus.STOPPED,
                    termination_reason=AgentTerminationReason.REPEATED_ACTION_DETECTED,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    action="agent.runtime.limit",
                    audit_status=AuditStatus.DENIED,
                    metadata={
                        **_limit_metadata(
                            reason=REPEATED_ACTION_DETECTED,
                            steps_used=steps_used,
                            tool_calls_used=tool_calls_used,
                        ),
                        **dict(repeated_check.metadata),
                    },
                )

            tool_calls_used += 1
            try:
                tool_result = await _await_with_timeout(
                    self._registry.execute(
                        name=decision.tool_name,
                        arguments=decision.arguments,
                        context=context,
                        agent_run_id=self._agent_run_id,
                    ),
                    timeout_seconds=deadline - now,
                )
            except TimeoutError:
                return await self._timeout_result(
                    context=context,
                    started=started,
                    now=deadline,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    metadata=_limit_metadata(
                        reason=AGENT_TIMEOUT,
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                        action_hash=repeated_check.action_hash,
                    ),
                )
            except AgentToolError as exc:
                return await self._finish(
                    context=context,
                    started=started,
                    now=self._perf_counter(),
                    status=AgentRunStatus.FAILED,
                    termination_reason=AgentTerminationReason.AGENT_TOOL_FAILED,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    action="agent.runtime.run",
                    audit_status=AuditStatus.FAILURE,
                    metadata=_tool_failure_metadata(
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                        tool_error_code=exc.code,
                    ),
                )
            except Exception:
                return await self._finish(
                    context=context,
                    started=started,
                    now=self._perf_counter(),
                    status=AgentRunStatus.FAILED,
                    termination_reason=AgentTerminationReason.AGENT_TOOL_FAILED,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    action="agent.runtime.run",
                    audit_status=AuditStatus.FAILURE,
                    metadata=_tool_failure_metadata(
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                        tool_error_code=TOOL_EXECUTION_FAILED,
                    ),
                )

            observation = _summarize_tool_result(tool_result)
            observations.append(observation)
            if tool_result.status is ToolInvocationStatus.FAILURE:
                return await self._finish(
                    context=context,
                    started=started,
                    now=self._perf_counter(),
                    status=AgentRunStatus.FAILED,
                    termination_reason=AgentTerminationReason.AGENT_TOOL_FAILED,
                    steps_used=steps_used,
                    tool_calls_used=tool_calls_used,
                    observations=observations,
                    action="agent.runtime.run",
                    audit_status=AuditStatus.FAILURE,
                    metadata=_tool_failure_metadata(
                        steps_used=steps_used,
                        tool_calls_used=tool_calls_used,
                        tool_name=decision.tool_name,
                        arguments=decision.arguments,
                        tool_error_code=_safe_error_code(tool_result.output),
                    ),
                )

    async def _timeout_result(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        now: float,
        steps_used: int,
        tool_calls_used: int,
        observations: Sequence[AgentObservationSummary],
        metadata: Mapping[str, object] | None = None,
    ) -> AgentRunResult:
        return await self._finish(
            context=context,
            started=started,
            now=now,
            status=AgentRunStatus.STOPPED,
            termination_reason=AgentTerminationReason.AGENT_TIMEOUT,
            steps_used=steps_used,
            tool_calls_used=tool_calls_used,
            observations=observations,
            action="agent.runtime.limit",
            audit_status=AuditStatus.DENIED,
            metadata=metadata
            or _limit_metadata(
                reason=AGENT_TIMEOUT,
                steps_used=steps_used,
                tool_calls_used=tool_calls_used,
            ),
        )

    async def _finish(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        now: float,
        status: AgentRunStatus,
        termination_reason: AgentTerminationReason,
        steps_used: int,
        tool_calls_used: int,
        observations: Sequence[AgentObservationSummary],
        action: str,
        audit_status: AuditStatus,
        metadata: Mapping[str, object],
        final_answer: str | None = None,
        final_citations: Sequence[AgentCitationRef] = (),
        error_code: str | None = None,
    ) -> AgentRunResult:
        if error_code is None and termination_reason is not AgentTerminationReason.FINAL_ANSWER:
            error_code = (
                termination_reason.value
                if termination_reason is not AgentTerminationReason.AGENT_TOOL_FAILED
                else AGENT_TOOL_FAILED
            )
        latency_ms = _elapsed_ms(now - started)
        result = AgentRunResult(
            status=status,
            termination_reason=termination_reason,
            steps_used=steps_used,
            tool_calls_used=tool_calls_used,
            final_answer=final_answer,
            final_citations=tuple(final_citations),
            error_code=error_code,
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            observations=tuple(observations),
            latency_ms=latency_ms,
            metadata=metadata,
        )
        await self._record_audit(
            context=context,
            action=action,
            status=audit_status,
            latency_ms=latency_ms,
            error_code=result.error_code,
            metadata={
                **dict(metadata),
                "run_status": status.value,
                "termination_reason": termination_reason.value,
            },
        )
        return result

    async def _validate_final_answer(
        self,
        *,
        context: AuthenticatedRequestContext,
        answer: str,
        citations: Sequence[AgentCitationRef],
        observations: Sequence[AgentObservationSummary],
        deadline: float,
    ) -> FinalAnswerValidationResult:
        started = self._perf_counter()
        try:
            request = FinalAnswerValidationRequest(
                agent_run_id=self._agent_run_id,
                answer=answer,
                citations=tuple(citations),
            )
        except Exception:
            result = _validation_failure_result(
                error_code=AGENT_FINAL_ANSWER_VALIDATION_FAILED,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
            )
            await self._record_final_answer_validation_audit(
                context=context,
                result=result,
            )
            return result
        if self._final_answer_validator is None:
            self._final_answer_validator = StrictFinalAnswerValidator(audit=self._audit)
        remaining_seconds = deadline - self._perf_counter()
        if remaining_seconds <= 0:
            result = _validation_failure_result(
                error_code=AGENT_TIMEOUT,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
            )
            await self._record_final_answer_validation_audit(
                context=context,
                result=result,
            )
            return result
        try:
            return await _await_with_timeout(
                self._final_answer_validator.validate(
                    context=context,
                    request=request,
                    observations=tuple(observations),
                ),
                timeout_seconds=remaining_seconds,
            )
        except TimeoutError:
            result = _validation_failure_result(
                error_code=AGENT_TIMEOUT,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
            )
            await self._record_final_answer_validation_audit(
                context=context,
                result=result,
            )
            return result
        except Exception:
            result = _validation_failure_result(
                error_code=AGENT_FINAL_ANSWER_VALIDATION_FAILED,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
            )
            await self._record_final_answer_validation_audit(
                context=context,
                result=result,
            )
            return result

    async def _record_final_answer_validation_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        result: FinalAnswerValidationResult,
    ) -> None:
        if self._audit is None:
            return
        resource_id = self._agent_run_id or context.request_id
        try:
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action=FINAL_ANSWER_VALIDATION_ACTION,
                    resource=AuditResource(
                        type="agent_run",
                        id=resource_id,
                        metadata={"agent_run_id": resource_id},
                    ),
                    status=AuditStatus.FAILURE,
                    latency_ms=result.latency_ms,
                    error_code=result.error_code,
                    metadata=dict(result.metadata),
                )
            )
        except Exception as exc:
            logger.warning(
                "agent.final_answer_validation.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "agent_run_id": resource_id,
                    "validation_status": result.status,
                    "error_code": result.error_code,
                    "audit_error_type": type(exc).__name__,
                },
            )

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        action: str,
        status: AuditStatus,
        latency_ms: float,
        error_code: str | None,
        metadata: Mapping[str, object],
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action=action,
                    resource=AuditResource(type="agent_run", id=context.request_id),
                    status=status,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    metadata=dict(metadata),
                )
            )
        except Exception as exc:
            logger.warning(
                "agent.runtime.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "action": action,
                    "audit_status": status.value,
                    "error_code": error_code,
                    "audit_error_type": type(exc).__name__,
                },
            )


async def _await_with_timeout(
    awaitable: Coroutine[Any, Any, _T],
    *,
    timeout_seconds: float,
) -> _T:
    if timeout_seconds <= 0:
        raise TimeoutError
    task: asyncio.Task[_T] = asyncio.create_task(awaitable)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
    except TimeoutError:
        await _cancel_task(task)
        raise
    except asyncio.CancelledError:
        await _cancel_task(task)
        raise


def _build_state(
    *,
    context: AuthenticatedRequestContext,
    steps_used: int,
    tool_calls_used: int,
    observations: Sequence[AgentObservationSummary],
) -> AgentRuntimeState:
    return AgentRuntimeState(
        request_id=context.request_id,
        trace_id=context.trace_id,
        tenant_id=context.auth.tenant_id,
        user_id=context.auth.user_id,
        steps_used=steps_used,
        tool_calls_used=tool_calls_used,
        observations=tuple(observations),
    )


def _summarize_tool_result(result: ToolExecutionResult) -> AgentObservationSummary:
    output_keys = _safe_output_keys(result.output)
    result_status = _safe_result_status(result.output)
    error_code = _safe_error_code(result.output)
    citation_refs = _citation_refs_from_tool_result(result)
    return AgentObservationSummary(
        tool_name=_safe_tool_name(result.tool_name),
        status=result.status,
        output_keys=output_keys,
        citation_refs=citation_refs,
        error_code=error_code,
        result_status=result_status,
        latency_ms=result.latency_ms,
        metadata={
            "tool_name": _safe_tool_name(result.tool_name),
            "status": result.status.value,
            "output_keys": list(output_keys),
            "result_status": result_status,
            "error_code": error_code,
            "citation_ref_count": len(citation_refs),
        },
    )


def _citation_refs_from_tool_result(result: ToolExecutionResult) -> tuple[AgentCitationRef, ...]:
    if result.tool_name != "rag_search":
        return ()
    if result.status is not ToolInvocationStatus.SUCCESS:
        return ()
    output = result.output
    if output is None or output.get("status") != "success":
        return ()
    results = output.get("results")
    if not isinstance(results, Sequence) or isinstance(results, str | bytes):
        return ()
    refs: list[AgentCitationRef] = []
    for item in results:
        if not isinstance(item, Mapping):
            continue
        document_id = item.get("document_id")
        version_id = item.get("version_id")
        chunk_id = item.get("chunk_id")
        if not (
            isinstance(document_id, str)
            and isinstance(version_id, str)
            and isinstance(chunk_id, str)
        ):
            continue
        page_start = item.get("page_start")
        page_end = item.get("page_end")
        source_display_name = item.get("source_display_name")
        source = (
            safe_source_display_name(source_display_name)
            if isinstance(source_display_name, str)
            else None
        )
        try:
            refs.append(
                AgentCitationRef(
                    document_id=document_id,
                    version_id=version_id,
                    chunk_id=chunk_id,
                    source=source,
                    page_start=page_start if isinstance(page_start, int) else None,
                    page_end=page_end if isinstance(page_end, int) else None,
                    tool_name="rag_search",
                )
            )
        except (KeyError, ValueError):
            continue
    return tuple(refs)


def _base_run_metadata(
    *,
    error_code: str | None,
    steps_used: int,
    tool_calls_used: int,
) -> dict[str, object]:
    return {
        "steps_used": steps_used,
        "tool_calls_used": tool_calls_used,
        "error_code": error_code,
    }


def _limit_metadata(
    *,
    reason: str,
    steps_used: int,
    tool_calls_used: int,
    tool_name: str | None = None,
    arguments: Mapping[str, object] | None = None,
    action_hash: str | None = None,
) -> dict[str, object]:
    metadata = _base_run_metadata(
        error_code=reason,
        steps_used=steps_used,
        tool_calls_used=tool_calls_used,
    )
    if tool_name is not None:
        metadata["tool_name"] = _safe_tool_name(tool_name)
    if arguments is not None:
        metadata["argument_keys"] = list(_safe_argument_keys(arguments))
    if action_hash is not None:
        metadata["action_hash"] = action_hash
    return metadata


def _tool_failure_metadata(
    *,
    steps_used: int,
    tool_calls_used: int,
    tool_name: str,
    arguments: Mapping[str, object],
    tool_error_code: str | None,
) -> dict[str, object]:
    return {
        **_base_run_metadata(
            error_code=AGENT_TOOL_FAILED,
            steps_used=steps_used,
            tool_calls_used=tool_calls_used,
        ),
        "tool_name": _safe_tool_name(tool_name),
        "argument_keys": list(_safe_argument_keys(arguments)),
        "tool_error_code": tool_error_code,
    }


def _validation_failure_result(
    *,
    error_code: str,
    latency_ms: float,
) -> FinalAnswerValidationResult:
    return FinalAnswerValidationResult(
        status="invalid",
        answer=None,
        citations=(),
        latency_ms=latency_ms,
        error_code=error_code,
        validated_citation_count=0,
        unsupported_citation_count=0,
        failed_tool_reference_count=0,
        metadata={
            "validation_status": "invalid",
            "error_code": error_code,
            "validated_citation_count": 0,
            "unsupported_citation_count": 0,
            "failed_tool_reference_count": 0,
        },
    )


async def _cancel_task(task: asyncio.Task[Any]) -> None:
    if task.done():
        _consume_task_result(task)
        return
    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=_TASK_CANCEL_GRACE_SECONDS)
    except TimeoutError:
        task.add_done_callback(_consume_task_result)
        logger.warning("agent.runtime.task_cancel_timeout")
    except asyncio.CancelledError:
        task.add_done_callback(_consume_task_result)
        return
    except BaseException:
        return


def _safe_error_code(output: Mapping[str, object] | None) -> str | None:
    if output is None:
        return None
    error_code = output.get("error_code")
    if not isinstance(error_code, str):
        return None
    normalized = error_code.strip()
    if not _is_safe_observable_name(normalized):
        return None
    return normalized


def _safe_result_status(output: Mapping[str, object] | None) -> str | None:
    if output is None:
        return None
    status = output.get("status")
    if not isinstance(status, str):
        return None
    normalized = status.strip().lower()
    if normalized in {"success", "error", "failure"}:
        return normalized
    return None


def _safe_output_keys(output: Mapping[str, object] | None) -> tuple[str, ...]:
    if output is None:
        return ()
    return tuple(sorted({_safe_argument_key(str(key)) for key in output}))


def _canonicalize(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return {"type": type(value).__name__}


def _safe_argument_keys(arguments: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(sorted({_safe_argument_key(str(key)) for key in arguments}))


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
