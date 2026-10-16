"""Bounded creation receipt, independent of the ticket's current lifecycle state."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID


def support_ticket_receipt(output: Mapping[str, object]) -> dict[str, object]:
    ticket_id, status = output.get("ticket_id"), output.get("status")
    if not isinstance(ticket_id, str) or len(ticket_id) != 36:
        return {}
    try:
        parsed = UUID(ticket_id)
    except ValueError:
        return {}
    if str(parsed) != ticket_id or status not in ("open", "in_progress", "resolved"):
        return {}
    return {"ticket_id": ticket_id, "status": status}
