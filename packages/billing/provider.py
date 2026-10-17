from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from packages.billing.exceptions import BillingError
from packages.billing.service import ChargeTicket, CostAccountingService
from packages.llm.decisions import DecisionRequest, DecisionResponse
from packages.llm.dto import GenerateChunk, GenerateRequest, GenerateResponse, GenerationMetadata
from packages.llm.ports import DecisionProvider, LLMProvider


class AccountingLLMProvider:
    """Account once per real provider call, including each individual agent step."""

    def __init__(self, provider: LLMProvider, accounting: CostAccountingService) -> None:
        self._provider = provider
        self._accounting = accounting

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        ticket = await self._accounting.begin(request)
        try:
            response = await self._provider.generate(request)
        except BaseException:
            await self._finish(ticket, metadata=None, status="failed")
            raise
        await self._finish(ticket, metadata=response.metadata)
        return response

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        ticket = await self._accounting.begin(request)
        stream = self._provider.stream(request)
        settled = False
        failed = False
        try:
            async for chunk in stream:
                if chunk.is_final and not settled:
                    assert chunk.response is not None
                    # Persist before forwarding final, even if the caller closes here.
                    await self._finish(ticket, metadata=chunk.response.metadata)
                    settled = True
                yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except BaseException:
            failed = True
            raise
        finally:
            try:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
            finally:
                if not settled:
                    await self._finish(
                        ticket,
                        metadata=None,
                        status="failed" if failed else "incomplete",
                    )

    async def _finish(
        self,
        ticket: ChargeTicket,
        *,
        metadata: GenerationMetadata | None,
        status: str = "completed",
    ) -> None:
        # Cancellation must not cancel a settlement already sent to storage.
        task = asyncio.create_task(
            self._accounting.finish(ticket, metadata=metadata, status=status)
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise


class AccountingDecisionProvider(AccountingLLMProvider):
    def __init__(
        self,
        provider: LLMProvider,
        accounting: CostAccountingService,
        decisions: DecisionProvider,
    ) -> None:
        super().__init__(provider, accounting)
        self._decisions = decisions

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        ticket = await self._accounting.begin(request)
        try:
            response = await self._decisions.decide(request)
        except BaseException:
            await self._finish(ticket, metadata=None, status="failed")
            raise
        await self._finish(ticket, metadata=response.metadata)
        return response


def wrap_provider(provider: LLMProvider, accounting: CostAccountingService) -> LLMProvider:
    if isinstance(provider, DecisionProvider):
        return AccountingDecisionProvider(provider, accounting, provider)
    return AccountingLLMProvider(provider, accounting)


def accounting_disabled() -> BillingError:
    return BillingError("COST_ACCOUNTING_DISABLED", "Cost accounting is not configured.")
