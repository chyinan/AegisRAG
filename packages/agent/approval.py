"""Non-serializable one-dispatch approval capability, minted by the application.

This object is never a model/tool input and cannot be supplied through JSON or
framework configuration. Permission checks still apply independently.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from packages.common.context import AuthenticatedRequestContext


def argument_digest(arguments: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(arguments),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


@dataclass
class ToolApprovalGrant:
    run_id: str
    tenant_id: str
    user_id: str
    tool_name: str
    arguments_digest: str
    expires_at: float
    clock: Callable[[], float] = field(repr=False)
    _consumed: bool = field(default=False, init=False, repr=False)

    def consume(
        self,
        *,
        run_id: str | None,
        context: AuthenticatedRequestContext,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> bool:
        if (
            self._consumed
            or self.clock() >= self.expires_at
            or run_id != self.run_id
            or context.auth.tenant_id != self.tenant_id
            or context.auth.user_id != self.user_id
            or tool_name != self.tool_name
            or argument_digest(arguments) != self.arguments_digest
        ):
            return False
        self._consumed = True
        return True
