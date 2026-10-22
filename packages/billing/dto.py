from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.llm.dto import TokenUsage

NANOS = Decimal(1_000_000_000)


def money_to_nanos(value: Decimal) -> int:
    return int((value * NANOS).to_integral_value(rounding=ROUND_CEILING))


def nanos_to_money(value: int) -> Decimal:
    return Decimal(value) / NANOS


class ModelPrice(BaseModel):
    """Immutable operator-supplied rate card; never a live vendor price claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    provider_version: str | None = None
    price_version: str = Field(min_length=1, max_length=64)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    effective_from: datetime
    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)

    @field_validator("provider", "model", "price_version")
    @classmethod
    def _trimmed_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("price identifiers must not be blank")
        return value.strip()

    @field_validator("effective_from")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("effective_from must include a timezone")
        return value.astimezone(UTC)

    def cost_nanos(self, usage: TokenUsage) -> int:
        return money_to_nanos(
            (
                self.input_per_million * usage.input_tokens
                + self.output_per_million * usage.output_tokens
            )
            / Decimal(1_000_000)
        )


class PriceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prices: tuple[ModelPrice, ...] = ()

    @model_validator(mode="after")
    def _unambiguous_versions(self) -> PriceCatalog:
        versions: set[tuple[str, str, str | None, str, str]] = set()
        dates: set[tuple[str, str, str | None, str, datetime]] = set()
        for price in self.prices:
            prefix = (price.provider, price.model, price.provider_version, price.currency)
            version_key = (*prefix, price.price_version)
            date_key = (*prefix, price.effective_from)
            if version_key in versions or date_key in dates:
                raise ValueError("model price versions and effective dates must be unique")
            versions.add(version_key)
            dates.add(date_key)
        return self

    def resolve(
        self,
        *,
        provider: str,
        model: str,
        version: str | None,
        currency: str,
        at: datetime,
    ) -> ModelPrice | None:
        eligible = [
            price
            for price in self.prices
            if price.provider == provider
            and price.model == model
            and price.currency == currency
            and price.effective_from <= at
            and price.provider_version in {None, version}
        ]
        if not eligible:
            return None
        # An exact provider version is preferred to its model-level fallback.
        return max(
            eligible,
            key=lambda price: (price.provider_version is not None, price.effective_from),
        )


class CostBreakdown(BaseModel):
    operation: str
    provider: str
    model: str
    price_version: str | None
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    priced_cost: Decimal
    unpriced_calls: int


class BusinessMetrics(BaseModel):
    currency: str
    start: datetime
    end: datetime
    calls: int
    successful_calls: int
    failed_calls: int
    incomplete_calls: int
    unknown_usage_calls: int
    unpriced_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    priced_cost: Decimal
    pending_reserved_cost: Decimal
    cost_complete: bool
    success_rate: float | None
    average_latency_ms: float | None
    priced_cost_per_success: Decimal | None = Field(
        description="Complete model-call cost per success; null for partial costs. Not answer cost."
    )
    breakdown: tuple[CostBreakdown, ...]


class BudgetSummary(BaseModel):
    currency: str
    period: str
    limit: Decimal | None
    spent: Decimal
    reserved: Decimal
    remaining: Decimal | None
    over_budget: bool
