"""Ordinary durable serialization failures and their unchanged storage contract."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.agent.dto import ToolInvocationStatus
from packages.agent.durable.dto import (
    MAX_ARGUMENT_BYTES,
    MAX_SNAPSHOT_BYTES,
    DurableSnapshot,
    validate_private_arguments,
)
from packages.agent.runtime import AgentObservationSummary, AgentRunConfig
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import DurableAgentRunModel
from packages.common.errors import DomainError
from tests.unit.agent.test_durable_execution import database as database

__all__ = ["database"]


def snapshot() -> DurableSnapshot:
    return DurableSnapshot(
        run_id="snapshot-bounds",
        tenant_id="tenant-a",
        user_id="user-a",
        request_id="request-a",
        trace_id="trace-a",
        backend="native",
        config=AgentRunConfig(
            max_steps=2,
            max_tool_calls=1,
            timeout_seconds=10,
            repeated_action_threshold=3,
            input_text="Calculate",
        ),
        started_at=100,
        deadline_at=110,
        last_observed_at=100,
        expires_at=200,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("started_at", float("inf")),
        ("config.max_steps", 21),
        ("config.max_tool_calls", 21),
        ("config.timeout_seconds", 121),
        ("config.input_text", "x" * 4001),
        ("deadline_at", 99),
        ("expires_at", 109),
        ("repeated_counts", {"action": 0}),
        ("repeated_counts", {str(index): 1 for index in range(21)}),
    ],
    ids=[
        "nonfinite-time",
        "decision-budget",
        "tool-budget",
        "time-budget",
        "input-size",
        "deadline-order",
        "retention-order",
        "nonpositive-repeat-count",
        "repeat-count-size",
    ],
)
async def test_invalid_stored_snapshot_bounds_are_rejected_without_rewrite(
    database: async_sessionmaker[AsyncSession],
    field: str,
    value: Any,
) -> None:
    store = SqlAlchemyDurableStore(database)
    original = snapshot()
    scope = {
        "tenant_id": original.tenant_id,
        "user_id": original.user_id,
        "run_id": original.run_id,
    }
    await store.create(original)
    assert await store.load(**scope) == original

    payload = original.persisted()
    target: Any = payload
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    async with database.begin() as session:
        await session.execute(
            update(DurableAgentRunModel)
            .where(DurableAgentRunModel.id == original.run_id)
            .values(snapshot=payload)
        )

    with pytest.raises(DomainError) as caught:
        await store.load(**scope)
    assert caught.value.code == "DURABLE_SNAPSHOT_INVALID"
    assert caught.value.status_code == 409
    async with database() as session:
        row = await session.scalar(
            select(DurableAgentRunModel).where(DurableAgentRunModel.id == original.run_id)
        )
        assert row is not None
        assert row.revision == original.revision and row.status == original.status
        assert json.dumps(row.snapshot, sort_keys=True) == json.dumps(payload, sort_keys=True)


@pytest.mark.parametrize("invalid", [float("nan"), object()], ids=["nan", "non-json-object"])
def test_arguments_require_json_serializable_values(invalid: object) -> None:
    validate_private_arguments({"items": [1, None, {"value": "ordinary"}]})
    with pytest.raises(DomainError) as caught:
        validate_private_arguments({"items": [{"value": invalid}]})
    assert caught.value.code == "DURABLE_ARGUMENTS_INVALID"
    assert caught.value.status_code == 422


def test_argument_size_limit_applies_to_actual_encoded_json() -> None:
    overhead = len(json.dumps({"text": ""}).encode())
    accepted = {"text": "x" * (MAX_ARGUMENT_BYTES - overhead)}
    assert len(json.dumps(accepted).encode()) == MAX_ARGUMENT_BYTES
    validate_private_arguments(accepted)

    oversized = {"text": accepted["text"] + "x"}
    with pytest.raises(DomainError) as caught:
        validate_private_arguments(oversized)
    assert caught.value.code == "DURABLE_ARGUMENTS_TOO_LARGE"
    assert caught.value.status_code == 422
    assert accepted["text"] == "x" * (MAX_ARGUMENT_BYTES - overhead)


def test_snapshot_size_limit_applies_to_actual_encoded_json() -> None:
    def with_observation(size: int) -> DurableSnapshot:
        observation = AgentObservationSummary(
            tool_name="calculator",
            status=ToolInvocationStatus.SUCCESS,
            latency_ms=0,
            output_summary={"result": "x" * size},
        )
        return DurableSnapshot.model_validate(
            {**snapshot().model_dump(), "observations": (observation,)}
        )

    empty_payload = with_observation(0).persisted()
    overhead = len(json.dumps(empty_payload, separators=(",", ":")).encode())
    accepted = with_observation(MAX_SNAPSHOT_BYTES - overhead)
    payload = accepted.persisted()
    assert len(json.dumps(payload, separators=(",", ":")).encode()) == MAX_SNAPSHOT_BYTES

    oversized = with_observation(MAX_SNAPSHOT_BYTES - overhead + 1)
    with pytest.raises(DomainError) as caught:
        oversized.persisted()
    assert caught.value.code == "DURABLE_SNAPSHOT_TOO_LARGE"
    assert caught.value.status_code == 422
    assert accepted.persisted() == payload
