from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from packages.billing.dto import (
    BudgetSummary,
    BusinessMetrics,
    PriceCatalog,
    money_to_nanos,
    nanos_to_money,
)
from packages.billing.exceptions import BillingError
from packages.billing.metrics import PRICED_COST, UNKNOWN_USAGE_CALLS, UNPRICED_CALLS, USAGE_TOKENS
from packages.billing.models import UsageChargeModel
from packages.billing.repository import CostRepository
from packages.common.context import AuthenticatedRequestContext
from packages.common.tenant_context import current_tenant_id, tenant_scope
from packages.llm.decisions import DecisionRequest
from packages.llm.dto import GenerateRequest, GenerationMetadata, TokenUsage

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChargeTicket:
    call_id: str
    request: GenerateRequest
    started_at: datetime


class CostAccountingService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        catalog: PriceCatalog,
        currency: str = "USD",
        monthly_budget: Decimal | None = None,
        minimum_reservation: Decimal | None = None,
        provider_version: str | None = None,
        io_timeout_seconds: float = 5.0,
        operation: str = "llm",
    ) -> None:
        self._sessions = session_factory
        self._catalog = catalog
        self.currency = currency
        self._limit = money_to_nanos(monthly_budget) if monthly_budget is not None else None
        self._minimum = money_to_nanos(minimum_reservation or Decimal(0))
        self._provider_version = provider_version
        self._timeout = io_timeout_seconds
        self._operation = operation

    @asynccontextmanager
    async def _owned_session(self, tenant_id: str) -> AsyncIterator[AsyncSession]:
        inherited = current_tenant_id()
        if inherited is not None and inherited != tenant_id:
            raise BillingError(
                "COST_TENANT_CONTEXT_MISMATCH",
                "Cost context does not match the authenticated request.",
            )
        # Match SessionFactoryAuditPort: preserve inherited scope or explicitly
        # establish it for a trusted direct/background caller before DB begin.
        with tenant_scope(tenant_id):
            async with asyncio.timeout(self._timeout), self._sessions() as session:
                yield session

    async def begin(self, request: GenerateRequest) -> ChargeTicket:
        now = datetime.now(UTC)
        ticket = ChargeTicket(call_id=str(uuid4()), request=request, started_at=now)
        price = self._catalog.resolve(
            provider=request.provider,
            model=request.model,
            version=self._provider_version,
            currency=self.currency,
            at=now,
        )
        reserve = 0
        if self._limit is not None:
            if price is None:
                raise BillingError("COST_PRICE_MISSING", "Budgeted model has no configured price.")
            if request.max_output_tokens is None:
                raise BillingError(
                    "COST_OUTPUT_CAP_REQUIRED", "Monetary budgets require max_output_tokens."
                )
            # A conservative forecast, not a provider-tokenizer or invoice guarantee.
            input_estimate = sum(len(m.content.encode("utf-8")) for m in request.messages) + 256
            if isinstance(request, DecisionRequest):
                input_estimate += len(
                    json.dumps(
                        {
                            "tools": [tool.model_dump(mode="json") for tool in request.tools],
                            "final": request.final_answer_schema,
                        }
                    ).encode("utf-8")
                )
            reserve = max(
                self._minimum,
                price.cost_nanos(
                    TokenUsage(
                        input_tokens=input_estimate,
                        output_tokens=request.max_output_tokens,
                    )
                ),
            )
        charge = UsageChargeModel(
            id=ticket.call_id,
            created_at=now,
            updated_at=now,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            created_by=request.user_id,
            status="reserved",
            request_id=request.request_id,
            trace_id=request.trace_id,
            provider=request.provider,
            model=request.model,
            operation=self._operation,
            provider_version=self._provider_version,
            currency=self.currency,
            period=now.strftime("%Y-%m"),
            reserved_nanos=reserve,
            usage_known=False,
        )
        try:
            async with self._owned_session(request.tenant_id) as session:
                async with session.begin():
                    await CostRepository(session).reserve(charge, limit_nanos=self._limit)
        except (SQLAlchemyError, TimeoutError) as exc:
            raise BillingError(
                "COST_STORAGE_UNAVAILABLE", "Cost reservation could not be saved."
            ) from exc
        return ticket

    async def finish(
        self,
        ticket: ChargeTicket,
        *,
        metadata: GenerationMetadata | None,
        status: str = "completed",
    ) -> None:
        request = ticket.request
        if metadata is not None and any(
            (
                metadata.request_id != request.request_id,
                metadata.trace_id != request.trace_id,
                metadata.tenant_id != request.tenant_id,
                metadata.user_id != request.user_id,
                metadata.provider != request.provider,
                metadata.model != request.model,
            )
        ):
            raise BillingError("COST_USAGE_MISMATCH", "Provider usage metadata is inconsistent.")
        if metadata is not None and metadata.error_code is not None:
            status = "failed"
        price = self._catalog.resolve(
            provider=request.provider,
            model=request.model,
            version=metadata.version if metadata is not None else self._provider_version,
            currency=self.currency,
            at=ticket.started_at,
        )
        usage = metadata.usage if metadata is not None else None
        usage_known = metadata is not None and not metadata.metadata.get(
            "usage_unavailable_count", 0
        )
        # Some providers report total only; do not treat an unknown split as free.
        split_known = usage is not None and usage.total_tokens in {
            0,
            usage.input_tokens + usage.output_tokens,
        }
        amount = (
            price.cost_nanos(usage)
            if price and usage is not None and split_known and usage_known
            else None
        )
        try:
            async with self._owned_session(request.tenant_id) as session:
                async with session.begin():
                    changed = await CostRepository(session).settle(
                        call_id=ticket.call_id,
                        tenant_id=request.tenant_id,
                        status=status,
                        usage=usage,
                        amount_nanos=amount,
                        usage_known=bool(usage_known),
                        provider_version=metadata.version if metadata is not None else None,
                        price_snapshot=price.model_dump(mode="json") if price else None,
                        price_version=price.price_version if price else None,
                        latency_ms=metadata.latency_ms if metadata is not None else None,
                    )
        except (SQLAlchemyError, TimeoutError) as exc:
            raise BillingError(
                "COST_STORAGE_UNAVAILABLE", "Usage settlement could not be saved."
            ) from exc
        if changed:
            labels = (request.provider, request.model)
            if usage is not None:
                for kind in ("input_tokens", "output_tokens", "total_tokens"):
                    USAGE_TOKENS.labels(*labels, kind).inc(getattr(usage, kind))
            if not usage_known:
                UNKNOWN_USAGE_CALLS.labels(*labels).inc()
            elif amount is None:
                UNPRICED_CALLS.labels(*labels).inc()
            if amount is not None:
                PRICED_COST.labels(*labels, self.currency).inc(float(nanos_to_money(amount)))
            logger.info(
                "llm.usage.accounted",
                request_id=request.request_id,
                trace_id=request.trace_id,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                provider=request.provider,
                model=request.model,
                price_version=price.price_version if price else None,
                currency=self.currency,
                priced_cost=str(nanos_to_money(amount)) if amount is not None else None,
                usage_known=bool(usage_known),
                status=status,
                token_usage=usage.model_dump() if usage is not None else None,
            )

    async def summary(
        self,
        *,
        context: AuthenticatedRequestContext,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> BusinessMetrics:
        self._authorize(context)
        end = end or datetime.now(UTC)
        start = start or end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start >= end
            or end - start > timedelta(days=366)
        ):
            raise BillingError(
                "COST_PERIOD_INVALID", "Use an aware interval of at most 366 days.", status_code=422
            )
        try:
            async with self._owned_session(context.auth.tenant_id) as session:
                return await CostRepository(session).metrics(
                    tenant_id=context.auth.tenant_id,
                    currency=self.currency,
                    start=start,
                    end=end,
                )
        except (SQLAlchemyError, TimeoutError) as exc:
            raise BillingError(
                "COST_STORAGE_UNAVAILABLE", "Cost metrics could not be read."
            ) from exc

    async def budget(self, *, context: AuthenticatedRequestContext) -> BudgetSummary:
        self._authorize(context)
        try:
            async with self._owned_session(context.auth.tenant_id) as session:
                return await CostRepository(session).budget(
                    tenant_id=context.auth.tenant_id,
                    currency=self.currency,
                    period=datetime.now(UTC).strftime("%Y-%m"),
                    limit_nanos=self._limit,
                )
        except (SQLAlchemyError, TimeoutError) as exc:
            raise BillingError(
                "COST_STORAGE_UNAVAILABLE", "Cost budget could not be read."
            ) from exc

    @staticmethod
    def _authorize(context: AuthenticatedRequestContext) -> None:
        if not {"audit:read", "cost:read"}.intersection(context.auth.permissions):
            raise BillingError(
                "COST_FORBIDDEN", "Cost metrics permission is required.", status_code=403
            )
