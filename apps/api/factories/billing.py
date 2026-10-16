from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.billing.dto import PriceCatalog
from packages.billing.provider import accounting_disabled
from packages.billing.service import CostAccountingService
from packages.common.config import AppSettings, load_settings


def create_cost_accounting(
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    operation: str = "llm",
) -> CostAccountingService:
    return CostAccountingService(
        session_factory=session_factory,
        catalog=PriceCatalog.model_validate_json(settings.cost_model_prices_json),
        currency=settings.cost_currency,
        monthly_budget=settings.cost_monthly_budget,
        minimum_reservation=settings.cost_minimum_reservation,
        provider_version=(
            "fake-v1" if settings.llm_provider == "fake" else settings.llm_provider_version
        ),
        io_timeout_seconds=settings.cost_io_timeout_seconds,
        operation=operation,
    )


def get_cost_accounting_service() -> CostAccountingService:
    from apps.api.factories.common import create_session_factory

    settings = load_settings()
    if not settings.cost_accounting_enabled:
        raise accounting_disabled()
    return create_cost_accounting(settings, create_session_factory(settings.database_url))


CostAccountingServiceDep = Annotated[
    CostAccountingService,
    Depends(get_cost_accounting_service),
]
