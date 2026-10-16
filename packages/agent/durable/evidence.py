"""Framework/storage-neutral evidence revalidation contract for durable recovery."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from packages.agent.runtime import AgentObservationSummary
from packages.common.context import AuthenticatedRequestContext


@dataclass(frozen=True)
class RehydratedEvidence:
    observations: tuple[AgentObservationSummary, ...]
    fingerprints: dict[str, str]


class EvidenceRevalidator(Protocol):
    async def rehydrate(
        self,
        *,
        context: AuthenticatedRequestContext,
        observations: Sequence[AgentObservationSummary],
        fingerprints: dict[str, str],
    ) -> RehydratedEvidence: ...
