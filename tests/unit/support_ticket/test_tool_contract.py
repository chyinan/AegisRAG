from __future__ import annotations

from uuid import UUID

from packages.agent.dto import ToolRateLimit
from packages.agent.tools.support_ticket import build_support_ticket_tool
from packages.common.config import AppSettings
from packages.support_ticket.dto import (
    SUPPORT_TICKET_CREATE_PERMISSION,
    CreateSupportTicketInput,
    CreateSupportTicketOutput,
)
from packages.support_ticket.service import SupportTicketService


def test_support_tool_v1_is_opt_in_approval_governed_and_local() -> None:
    assert AppSettings().support_ticket_enabled is False
    # Contract inspection never opens a SQL connection.
    definition = build_support_ticket_tool(
        service=SupportTicketService(None),  # type: ignore[arg-type]
        timeout_seconds=3,
        rate_limit=ToolRateLimit(max_calls=5, window_seconds=60),
    )
    assert definition.name == "create_support_ticket"
    assert definition.permission == SUPPORT_TICKET_CREATE_PERMISSION
    assert definition.requires_approval is True
    assert definition.durable_replay_policy == "never"
    assert definition.implementation_version == "1"
    assert definition.input_schema is CreateSupportTicketInput
    assert definition.output_schema is CreateSupportTicketOutput
    assert definition.input_json_schema["additionalProperties"] is False
    assert set(definition.input_json_schema["properties"]) == {
        "client_reference",
        "title",
        "description",
        "priority",
    }
    assert set(definition.output_json_schema["properties"]) == {"ticket_id", "status"}
    assert definition.prompt_projection is not None
    assert definition.prompt_projection(
        {
            "ticket_id": "d28b2019-39c4-4225-b064-42a90781e667",
            "status": "open",
            "title": "Workstation issue",
            "description": "Device details",
        }
    ) == {"ticket_id": "d28b2019-39c4-4225-b064-42a90781e667", "status": "open"}


def test_v1_normalizes_input_and_has_stable_canonical_fingerprint() -> None:
    arguments = {
        "client_reference": UUID("24bfa7b7-8524-4caf-9469-b05ea4d59029"),
        "title": "  Workstation issue  ",
        "description": " Please investigate ",
    }
    first = CreateSupportTicketInput(**arguments)
    restored = CreateSupportTicketInput.model_validate(first.model_dump(mode="json"))
    assert first.title == "Workstation issue"
    assert first.description == "Please investigate"
    assert restored.fingerprint() == first.fingerprint()
    assert len(first.fingerprint()) == 64
    assert CreateSupportTicketInput.model_config["frozen"] is True
