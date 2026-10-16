"""Versioned business snapshots. Never pass these payloads to framework tracing."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Literal
from uuid import UUID, uuid5

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.agent.dto import AgentCitationRef, AgentRunCommand
from packages.agent.exceptions import AgentRunError
from packages.agent.runtime import (
    AgentObservationSummary,
    AgentRunConfig,
    AgentRunResult,
    AgentStepDecision,
)

SNAPSHOT_VERSION: Literal[1] = 1
START_PROTOCOL_VERSION: Literal[1] = 1
# Immutable protocol namespace, independent of deployment settings and backends.
_START_NAMESPACE_V1 = UUID("a97959de-35c0-53a8-bdbb-1658dab6aa98")
MAX_SNAPSHOT_BYTES = 262_144
MAX_ARGUMENT_BYTES = 16_384
DurableStatus = Literal[
    "running",
    "awaiting_approval",
    "completed",
    "failed",
    "stopped",
    "cancelled",
    "intervention_required",
]
DurablePhase = Literal["decision", "tool", "validation", "done"]
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "stopped", "cancelled", "intervention_required"}
)


def durable_error(code: str, *, status_code: int = 409) -> AgentRunError:
    return AgentRunError(
        code=code,
        message="Durable agent operation could not proceed.",
        details={"error_code": code},
        status_code=status_code,
    )


class DurableStartCommand(AgentRunCommand):
    idempotency_key: UUID4 | None = None
    approval_tools: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("approval_tools")
    @classmethod
    def exact_unique_tool_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in value
        ):
            raise ValueError("approval_tools must contain unique exact registered tool names")
        return value


def keyed_run_id(*, tenant_id: str, user_id: str, key: UUID) -> str:
    owner_key = json.dumps([tenant_id, user_id, str(key)], ensure_ascii=True, separators=(",", ":"))
    return str(uuid5(_START_NAMESPACE_V1, owner_key))


def start_command_fingerprint(command: DurableStartCommand) -> str:
    # Explicit protocol fields, including null budgets, capture the validated
    # request rather than deployment defaults. Never persist the raw key/body.
    canonical = {
        "protocol_version": START_PROTOCOL_VERSION,
        "input": command.input,
        "max_steps": command.max_steps,
        "max_tool_calls": command.max_tool_calls,
        "timeout_seconds": command.timeout_seconds,
        "approval_tools": command.approval_tools,
        "metadata": command.metadata,
    }
    return hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


class DurableStartup(BaseModel):
    """Private optional protocol extension; old unkeyed snapshots remain readable."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol_version: Literal[1] = START_PROTOCOL_VERSION
    command_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class DurableRecoverCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: UUID4


class DurableResumeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_revision: int = Field(ge=0)
    approval_id: str | None = Field(default=None, min_length=1, max_length=36)
    approve: bool | None = None

    @model_validator(mode="after")
    def approval_is_explicit(self) -> DurableResumeCommand:
        if (self.approval_id is None) != (self.approve is None):
            raise ValueError("approval_id and approve must be supplied together")
        return self


class DurableCancelCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_revision: int = Field(ge=0)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approval_id: str
    tool_name: str
    arguments: dict[str, object]
    action_digest: str
    definition_digest: str
    expires_at: float


class DurableSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = SNAPSHOT_VERSION
    run_id: str
    tenant_id: str
    user_id: str
    request_id: str
    trace_id: str
    backend: Literal["native", "langgraph"]
    startup: DurableStartup | None = None
    revision: int = Field(default=0, ge=0)
    config: AgentRunConfig
    status: DurableStatus = "running"
    phase: DurablePhase = "decision"
    started_at: float
    deadline_at: float
    last_observed_at: float
    expires_at: float
    steps_used: int = Field(default=0, ge=0, le=20)
    tool_calls_used: int = Field(default=0, ge=0, le=20)
    repeated_counts: dict[str, int] = Field(default_factory=dict)
    observations: tuple[AgentObservationSummary, ...] = Field(default=(), max_length=20)
    evidence_fingerprints: dict[str, str] = Field(default_factory=dict)
    decision: AgentStepDecision | None = None
    approval_tools: tuple[str, ...] = ()
    approval: ApprovalRequest | None = None
    approved_action_digest: str | None = None
    approved_definition_digest: str | None = None
    pending_call_id: str | None = None
    decision_inflight: bool = False
    result: AgentRunResult | None = None
    error_code: str | None = None

    @field_validator("started_at", "deadline_at", "last_observed_at", "expires_at")
    @classmethod
    def finite_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timestamp must be finite")
        return value

    @model_validator(mode="after")
    def bounded_snapshot(self) -> DurableSnapshot:
        if self.config.max_steps > 20 or self.config.max_tool_calls > 20:
            raise ValueError("durable execution supports at most 20 decisions and tool calls")
        if self.config.timeout_seconds > 120:
            raise ValueError("durable execution supports at most 120 seconds")
        if len(self.config.input_text) > 4000:
            raise ValueError("input is too large")
        if self.deadline_at < self.started_at or self.expires_at < self.deadline_at:
            raise ValueError("invalid durable deadlines")
        if len(self.repeated_counts) > 20 or any(v < 1 for v in self.repeated_counts.values()):
            raise ValueError("invalid repeated action counts")
        return self

    def persisted(self) -> dict[str, object]:
        # Pydantic exclusions deliberately strip prompt_context at every depth.
        payload = self.model_dump(mode="json")
        if self.startup is None:
            # Preserve the original wire shape for unkeyed runs during upgrades.
            payload.pop("startup", None)
        encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode()) > MAX_SNAPSHOT_BYTES:
            raise durable_error("DURABLE_SNAPSHOT_TOO_LARGE", status_code=422)
        return payload


class DurableRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    schema_version: int = SNAPSHOT_VERSION
    status: DurableStatus
    revision: int
    backend: Literal["native", "langgraph"]
    steps_used: int
    tool_calls_used: int
    deadline_at: float
    approval: ApprovalRequest | None = None
    final_answer: str | None = None
    final_citations: tuple[AgentCitationRef, ...] = ()
    error_code: str | None = None
    termination_reason: str | None = None

    @classmethod
    def from_snapshot(cls, value: DurableSnapshot) -> DurableRunResponse:
        result = value.result
        return cls(
            run_id=value.run_id,
            status=value.status,
            revision=value.revision,
            backend=value.backend,
            steps_used=value.steps_used,
            tool_calls_used=value.tool_calls_used,
            deadline_at=value.deadline_at,
            approval=value.approval if value.status == "awaiting_approval" else None,
            final_answer=result.final_answer if result and value.status == "completed" else None,
            final_citations=result.final_citations
            if result and value.status == "completed"
            else (),
            error_code=value.error_code or (result.error_code if result else None),
            termination_reason=result.termination_reason.value if result else value.error_code,
        )


class ToolLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    call_id: str
    tool_name: str
    action_digest: str
    replay_policy: Literal["read_only", "never"]
    status: Literal["started", "completed", "unknown"]
    observation: AgentObservationSummary | None = None


def decision_digest(decision: AgentStepDecision) -> str:
    return hashlib.sha256(
        json.dumps(
            decision.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def validate_private_arguments(arguments: Mapping[str, object]) -> None:
    """Reject credential-shaped input; private persistence is not a secret vault."""
    forbidden = {
        "password",
        "secret",
        "token",
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "private_key",
    }

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if any(part in normalized for part in forbidden):
                    raise durable_error("DURABLE_SENSITIVE_ARGUMENT", status_code=422)
                visit(item)
        elif isinstance(value, list | tuple):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            lowered = value.lower()
            if any(
                marker in lowered
                for marker in (
                    "-----begin",
                    "bearer ",
                    "password=",
                    "api_key=",
                    "secret=",
                    "access_token=",
                )
            ):
                raise durable_error("DURABLE_SENSITIVE_ARGUMENT", status_code=422)

    visit(arguments)
    try:
        encoded = json.dumps(dict(arguments), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise durable_error("DURABLE_ARGUMENTS_INVALID", status_code=422) from exc
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise durable_error("DURABLE_ARGUMENTS_TOO_LARGE", status_code=422)
