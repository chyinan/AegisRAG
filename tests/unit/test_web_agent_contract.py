"""Keep the Agent workbench's synthetic wires tied to backend contracts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from packages.agent.dto import AgentCitationRef, AgentFinalAnswer, AgentRunResponse
from packages.agent.durable.dto import DurableRunResponse, decision_digest
from packages.agent.runtime import AgentStepDecision
from packages.agent.streaming import AgentStreamEvent
from scripts.generate_web_agent_fixtures import (
    FIXTURE_PATH,
    agent_fixtures,
    serialized_fixtures,
)


def test_agent_workbench_fixture_matches_production_serializers() -> None:
    assert FIXTURE_PATH.read_text(encoding="utf-8") == serialized_fixtures(), (
        "Run python -m scripts.generate_web_agent_fixtures"
    )


def test_agent_fixture_streams_are_ordered_and_final_answer_is_terminal_only() -> None:
    fixtures = json.loads(serialized_fixtures())
    for name, wire in fixtures["streams"].items():
        events = []
        for frame in wire.strip().split("\n\n"):
            lines = frame.splitlines()
            event = AgentStreamEvent.model_validate_json(lines[2].removeprefix("data: "))
            assert lines[0] == f"id: {event.sequence}"
            assert lines[1] == f"event: {event.event}"
            assert event.sequence == len(events)
            if event.event != "final":
                assert "final_answer" not in event.data
                assert "arguments" not in event.data
            events.append(event)
        if name == "success":
            final = AgentRunResponse.model_validate(events[-1].data)
            assert final.status == "completed"
            assert final.final_answer and final.final_citations
            assert final.final_citations[0].version_id == "version-pilot"
        elif name in {"stopped", "limitBeforeDispatch", "repeatBeforeDispatch"}:
            final = AgentRunResponse.model_validate(events[-1].data)
            assert final.status == "stopped"
            assert final.final_answer is None
        elif name == "incomplete":
            assert all(event.event not in {"final", "error"} for event in events)
        elif name == "errorBeforeStarted":
            assert len(events) == 1
            assert events[0].event == "error"
            assert events[0].agent_run_id is None
        else:
            assert events[-1].event == "error"


def test_durable_fixture_retains_exact_approval_and_hides_private_snapshot() -> None:
    fixtures = json.loads(serialized_fixtures())
    for name, envelope in fixtures["durable"].items():
        response = DurableRunResponse.model_validate(envelope["data"])
        assert "config" not in envelope["data"]
        assert "evidence_fingerprints" not in envelope["data"]
        assert "Synthetic policy question" not in json.dumps(envelope)
        if name == "awaitingApproval":
            assert response.approval is not None
            assert response.approval.arguments == {
                "query": "Remote-work policy 🌍",
                "top_k": 3,
            }
            assert response.approval.action_digest == decision_digest(
                AgentStepDecision.tool_call(
                    response.approval.tool_name,
                    response.approval.arguments,
                )
            )
        else:
            assert response.approval is None
        if response.status != "completed":
            assert response.final_answer is None
            assert response.final_citations == ()


def test_agent_fixtures_are_deterministic_across_generation() -> None:
    assert agent_fixtures() == agent_fixtures()


@pytest.mark.parametrize("length", [6001, 12000])
def test_agent_answer_limit_counts_unicode_codepoints(length: int) -> None:
    answer = "🌍" * length
    assert AgentFinalAnswer(answer=answer).answer == answer
    with pytest.raises(ValidationError):
        AgentFinalAnswer(answer="🌍" * 12001)


@pytest.mark.parametrize("length", [101, 200])
def test_agent_source_limit_counts_unicode_codepoints(length: int) -> None:
    source = "🌍" * length
    identifiers = {"document_id": "d", "version_id": "v", "chunk_id": "c"}
    assert AgentCitationRef(**identifiers, source=source).source == source
    with pytest.raises(ValidationError):
        AgentCitationRef(**identifiers, source="🌍" * 201)
