from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.billing.dto import (
    BudgetSummary,
    BusinessMetrics,
    CostBreakdown,
    nanos_to_money,
)
from packages.billing.exceptions import BillingError
from packages.billing.models import CostAccountModel, UsageChargeModel
from packages.llm.dto import TokenUsage


class CostRepository:
    """One transaction owns a reservation/settlement; call IDs deduplicate writes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(self, charge: UsageChargeModel, *, limit_nanos: int | None) -> None:
        account_key = (
            CostAccountModel.tenant_id == charge.tenant_id,
            CostAccountModel.currency == charge.currency,
            CostAccountModel.period == charge.period,
        )
        if not await self._session.scalar(select(CostAccountModel.id).where(*account_key)):
            try:
                async with self._session.begin_nested():
                    self._session.add(
                        CostAccountModel(
                            tenant_id=charge.tenant_id,
                            created_by=charge.user_id,
                            currency=charge.currency,
                            period=charge.period,
                        )
                    )
                    await self._session.flush()
            except IntegrityError:
                # Another call created this month's account. No money moved yet.
                if not await self._session.scalar(select(CostAccountModel.id).where(*account_key)):
                    raise
        statement = update(CostAccountModel).where(*account_key)
        if limit_nanos is not None:
            statement = statement.where(
                CostAccountModel.spent_nanos
                + CostAccountModel.reserved_nanos
                + charge.reserved_nanos
                <= limit_nanos
            )
        result = await self._session.execute(
            statement.values(
                reserved_nanos=CostAccountModel.reserved_nanos + charge.reserved_nanos,
            ).returning(CostAccountModel.id)
        )
        if result.scalar_one_or_none() is None:
            raise BillingError(
                "COST_BUDGET_EXCEEDED",
                "The monthly monetary budget is exhausted.",
                status_code=429,
            )
        self._session.add(charge)
        await self._session.flush()

    async def settle(
        self,
        *,
        call_id: str,
        tenant_id: str,
        status: str,
        usage: TokenUsage | None,
        amount_nanos: int | None,
        usage_known: bool = True,
        provider_version: str | None = None,
        price_snapshot: dict[str, object] | None = None,
        price_version: str | None = None,
        latency_ms: float | None = None,
    ) -> bool:
        # Atomic claim makes duplicate final chunks/retried persistence harmless.
        result = await self._session.execute(
            update(UsageChargeModel)
            .where(
                UsageChargeModel.id == call_id,
                UsageChargeModel.tenant_id == tenant_id,
                UsageChargeModel.status == "reserved",
            )
            .values(
                status=status,
                usage_known=usage is not None and usage_known,
                input_tokens=usage.input_tokens if usage is not None else None,
                output_tokens=usage.output_tokens if usage is not None else None,
                total_tokens=usage.total_tokens if usage is not None else None,
                amount_nanos=amount_nanos,
                provider_version=provider_version,
                price_snapshot=price_snapshot,
                price_version=price_version,
                latency_ms=latency_ms,
            )
            .returning(
                UsageChargeModel.currency,
                UsageChargeModel.period,
                UsageChargeModel.reserved_nanos,
            )
        )
        row = result.first()
        if row is None:
            return False
        currency, period, reserved_nanos = row
        # Unknown/unpriced usage retains its hold. It is never silently free.
        if amount_nanos is not None:
            await self._session.execute(
                update(CostAccountModel)
                .where(
                    CostAccountModel.tenant_id == tenant_id,
                    CostAccountModel.currency == currency,
                    CostAccountModel.period == period,
                )
                .values(
                    spent_nanos=CostAccountModel.spent_nanos + amount_nanos,
                    reserved_nanos=CostAccountModel.reserved_nanos - reserved_nanos,
                )
            )
        return True

    async def budget(
        self,
        *,
        tenant_id: str,
        currency: str,
        period: str,
        limit_nanos: int | None,
    ) -> BudgetSummary:
        account = await self._session.scalar(
            select(CostAccountModel).where(
                CostAccountModel.tenant_id == tenant_id,
                CostAccountModel.currency == currency,
                CostAccountModel.period == period,
            )
        )
        spent = account.spent_nanos if account is not None else 0
        reserved = account.reserved_nanos if account is not None else 0
        return BudgetSummary(
            currency=currency,
            period=period,
            limit=nanos_to_money(limit_nanos) if limit_nanos is not None else None,
            spent=nanos_to_money(spent),
            reserved=nanos_to_money(reserved),
            remaining=(
                nanos_to_money(limit_nanos - spent - reserved) if limit_nanos is not None else None
            ),
            over_budget=limit_nanos is not None and spent + reserved > limit_nanos,
        )

    async def metrics(
        self,
        *,
        tenant_id: str,
        currency: str,
        start: datetime,
        end: datetime,
    ) -> BusinessMetrics:
        c = UsageChargeModel
        scope = (
            c.tenant_id == tenant_id,
            c.currency == currency,
            c.created_at >= start,
            c.created_at < end,
        )
        unpriced = and_(c.usage_known.is_(True), c.amount_nanos.is_(None))
        result = (
            await self._session.execute(
                select(
                    func.count(c.id),
                    func.sum(case((c.status == "completed", 1), else_=0)),
                    func.sum(case((c.status == "failed", 1), else_=0)),
                    func.sum(case((c.status.in_(("reserved", "incomplete")), 1), else_=0)),
                    func.sum(case((c.usage_known.is_(False), 1), else_=0)),
                    func.sum(case((unpriced, 1), else_=0)),
                    func.sum(c.input_tokens),
                    func.sum(c.output_tokens),
                    func.sum(c.total_tokens),
                    func.sum(c.amount_nanos),
                    func.sum(case((c.amount_nanos.is_(None), c.reserved_nanos), else_=0)),
                    func.avg(c.latency_ms),
                ).where(*scope)
            )
        ).one()
        calls, successes, failures, incomplete, unknown, unpriced_count = (
            int(value or 0) for value in result[:6]
        )
        total_cost = int(result[9] or 0)
        cost_complete = unknown == 0 and unpriced_count == 0 and incomplete == 0
        groups = (
            await self._session.execute(
                select(
                    c.provider,
                    c.model,
                    c.price_version,
                    func.count(c.id),
                    func.sum(c.input_tokens),
                    func.sum(c.output_tokens),
                    func.sum(c.total_tokens),
                    func.sum(c.amount_nanos),
                    func.sum(case((unpriced, 1), else_=0)),
                    c.operation,
                )
                .where(*scope)
                .group_by(c.provider, c.model, c.price_version)
                .group_by(c.operation)
                .order_by(c.provider, c.model, c.price_version, c.operation)
            )
        ).all()
        return BusinessMetrics(
            currency=currency,
            start=start,
            end=end,
            calls=calls,
            successful_calls=successes,
            failed_calls=failures,
            incomplete_calls=incomplete,
            unknown_usage_calls=unknown,
            unpriced_calls=unpriced_count,
            input_tokens=int(result[6] or 0),
            output_tokens=int(result[7] or 0),
            total_tokens=int(result[8] or 0),
            priced_cost=nanos_to_money(total_cost),
            pending_reserved_cost=nanos_to_money(int(result[10] or 0)),
            cost_complete=cost_complete,
            success_rate=successes / calls if calls else None,
            average_latency_ms=float(result[11]) if result[11] is not None else None,
            priced_cost_per_success=(
                nanos_to_money(total_cost) / successes if successes and cost_complete else None
            ),
            breakdown=tuple(
                CostBreakdown(
                    operation=row[9],
                    provider=row[0],
                    model=row[1],
                    price_version=row[2],
                    calls=int(row[3]),
                    input_tokens=int(row[4] or 0),
                    output_tokens=int(row[5] or 0),
                    total_tokens=int(row[6] or 0),
                    priced_cost=nanos_to_money(int(row[7] or 0)),
                    unpriced_calls=int(row[8] or 0),
                )
                for row in groups
            ),
        )
