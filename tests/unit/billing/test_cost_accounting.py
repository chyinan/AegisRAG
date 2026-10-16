from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.auth.context import AuthContext
from packages.billing.dto import ModelPrice, PriceCatalog
from packages.billing.exceptions import BillingError
from packages.billing.models import CostAccountModel, UsageChargeModel
from packages.billing.provider import AccountingLLMProvider, wrap_provider
from packages.billing.service import CostAccountingService
from packages.common.config import AppSettings
from packages.common.context import AuthenticatedRequestContext
from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.decisions import DecisionRequest, DecisionResponse
from packages.llm.dto import GenerateRequest, LLMMessage, TokenUsage
from packages.llm.ports import DecisionProvider


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(CostAccountModel.__table__.create)
        await connection.run_sync(UsageChargeModel.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def price(**overrides: object) -> ModelPrice:
    values: dict[str, object] = {
        "provider": "fake",
        "model": "fake-llm",
        "price_version": "test-2026-v1",
        "currency": "USD",
        "effective_from": "2026-01-01T00:00:00Z",
        "input_per_million": "1.25",
        "output_per_million": "5.00",
    }
    return ModelPrice.model_validate(values | overrides)


def request() -> GenerateRequest:
    return GenerateRequest(
        messages=(LLMMessage(role="user", content="hello world"),),
        provider="fake",
        model="fake-llm",
        timeout_seconds=1,
        retry_budget=0,
        request_id="same-request",
        trace_id="trace",
        tenant_id="tenant",
        user_id="user",
        max_output_tokens=100,
    )


def context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="metrics-request",
        trace_id="trace",
        auth=AuthContext(user_id="user", tenant_id="tenant", permissions=("cost:read",)),
    )


def service(sessions: async_sessionmaker[AsyncSession], **kwargs: object) -> CostAccountingService:
    return CostAccountingService(
        session_factory=sessions,
        catalog=PriceCatalog(prices=(price(),)),
        **kwargs,  # type: ignore[arg-type]
    )


def test_versioned_prices_and_decimal_rounding() -> None:
    old = price()
    newer = price(
        price_version="test-v2", effective_from="2026-09-01T00:00:00Z", input_per_million="2"
    )
    catalog = PriceCatalog(prices=(old, newer))
    assert (
        catalog.resolve(
            provider="fake",
            model="fake-llm",
            version="fake-v1",
            currency="USD",
            at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        == old
    )
    assert (
        catalog.resolve(
            provider="fake",
            model="fake-llm",
            version="fake-v1",
            currency="USD",
            at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        == newer
    )
    assert old.cost_nanos(TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5)) == 17500
    tiny = price(input_per_million="0.00001")
    assert tiny.cost_nanos(TokenUsage(input_tokens=1)) == 1
    with pytest.raises(ValidationError, match="unique"):
        PriceCatalog(prices=(old, old))


async def test_fake_calls_keep_raw_usage_and_aggregate_by_price(sessions) -> None:
    accounting = service(sessions)
    wrapped = AccountingLLMProvider(FakeLLMProvider(response_text="three output words"), accounting)
    first = await wrapped.generate(request())
    second = await wrapped.generate(request())
    assert first.usage == TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5)
    assert second.usage == first.usage
    metrics = await accounting.summary(context=context())
    assert metrics.calls == 2
    assert metrics.successful_calls == 2
    assert metrics.input_tokens == 4 and metrics.output_tokens == 6 and metrics.total_tokens == 10
    assert metrics.priced_cost == Decimal("0.000035")
    assert metrics.priced_cost_per_success == Decimal("0.0000175")
    assert metrics.breakdown[0].price_version == "test-2026-v1"
    async with sessions() as session:
        charges = (await session.scalars(select(UsageChargeModel))).all()
        assert len({row.id for row in charges}) == 2
        assert charges[0].price_snapshot["input_per_million"] == "1.25"


async def test_settlement_is_idempotent_and_releases_reservation(sessions) -> None:
    accounting = service(sessions, monthly_budget=Decimal("1"), minimum_reservation=Decimal("0.01"))
    ticket = await accounting.begin(request())
    before = await accounting.budget(context=context())
    assert before.reserved == Decimal("0.01") and before.spent == 0
    response = await FakeLLMProvider(response_text="three output words").generate(request())
    await accounting.finish(ticket, metadata=response.metadata)
    await accounting.finish(ticket, metadata=response.metadata)
    after = await accounting.budget(context=context())
    assert after.spent == Decimal("0.0000175") and after.reserved == 0
    assert after.remaining == Decimal("0.9999825")
    assert (await accounting.summary(context=context())).calls == 1


async def test_budget_guard_and_unknown_usage_hold(sessions) -> None:
    accounting = service(
        sessions, monthly_budget=Decimal("0.01"), minimum_reservation=Decimal("0.01")
    )
    ticket = await accounting.begin(request())
    await accounting.finish(ticket, metadata=None, status="incomplete")
    with pytest.raises(BillingError, match="COST_BUDGET_EXCEEDED"):
        await accounting.begin(request())
    budget = await accounting.budget(context=context())
    assert budget.spent == 0 and budget.reserved == Decimal("0.01")
    summary = await accounting.summary(context=context())
    assert summary.incomplete_calls == 1 and summary.unknown_usage_calls == 1


async def test_unknown_price_is_visible_and_does_not_claim_free_usage(sessions) -> None:
    accounting = CostAccountingService(session_factory=sessions, catalog=PriceCatalog())
    response = await AccountingLLMProvider(FakeLLMProvider(), accounting).generate(request())
    summary = await accounting.summary(context=context())
    assert summary.unpriced_calls == 1 and summary.priced_cost == 0
    assert not summary.cost_complete and summary.priced_cost_per_success is None
    assert summary.total_tokens == response.usage.total_tokens
    assert summary.breakdown[0].price_version is None


async def test_adapter_unknown_usage_flag_preserves_raw_values(sessions) -> None:
    accounting = service(sessions)
    ticket = await accounting.begin(request())
    response = await FakeLLMProvider().generate(request())
    metadata = response.metadata.model_copy(update={"metadata": {"usage_unavailable_count": 1}})
    await accounting.finish(ticket, metadata=metadata)
    summary = await accounting.summary(context=context())
    assert summary.unknown_usage_calls == 1 and summary.priced_cost == 0
    assert not summary.cost_complete and summary.priced_cost_per_success is None
    async with sessions() as session:
        row = await session.scalar(select(UsageChargeModel))
        assert row.input_tokens == response.usage.input_tokens
        assert row.amount_nanos is None


async def test_reported_failure_keeps_actual_cost_and_failed_call_indicator(sessions) -> None:
    accounting = service(sessions)
    ticket = await accounting.begin(request())
    response = await FakeLLMProvider().generate(request())
    metadata = response.metadata.model_copy(update={"error_code": "MODEL_REPORTED_FAILURE"})
    await accounting.finish(ticket, metadata=metadata)
    summary = await accounting.summary(context=context())
    assert summary.failed_calls == 1 and summary.successful_calls == 0
    assert summary.total_tokens == response.usage.total_tokens
    assert summary.priced_cost > 0 and summary.cost_complete


async def test_stream_final_is_billed_once_and_can_be_closed(sessions) -> None:
    accounting = service(sessions)
    provider = AccountingLLMProvider(FakeLLMProvider(), accounting)
    stream = provider.stream(request())
    chunks = [chunk async for chunk in stream]
    assert chunks[-1].is_final
    summary = await accounting.summary(context=context())
    assert summary.calls == 1 and summary.successful_calls == 1
    assert summary.total_tokens == chunks[-1].response.usage.total_tokens


async def test_stream_close_before_final_records_unknown_usage(sessions) -> None:
    accounting = service(sessions, monthly_budget=Decimal("1"), minimum_reservation=Decimal("0.01"))
    provider = AccountingLLMProvider(FakeLLMProvider(), accounting)
    stream = provider.stream(request())
    await anext(stream)
    await stream.aclose()
    summary = await accounting.summary(context=context())
    assert summary.calls == 1 and summary.unknown_usage_calls == 1
    assert summary.pending_reserved_cost == Decimal("0.01")


async def test_native_decisions_preserve_provider_capability(sessions) -> None:
    class FakeDecisions(FakeLLMProvider):
        async def decide(self, req: DecisionRequest) -> DecisionResponse:
            generated = await self.generate(req)
            return DecisionResponse(
                action="final_answer", final_answer={"answer": "done"}, metadata=generated.metadata
            )

    accounting = service(sessions)
    provider = wrap_provider(FakeDecisions(), accounting)
    assert isinstance(provider, DecisionProvider)
    native_request = DecisionRequest(**dict(request()), final_answer_schema={"type": "object"})
    response = await provider.decide(native_request)
    assert response.action == "final_answer"
    assert (await accounting.summary(context=context())).calls == 1
    assert not isinstance(wrap_provider(FakeLLMProvider(), accounting), DecisionProvider)


async def test_cancelled_provider_keeps_reservation_and_records_failure(sessions) -> None:
    entered = asyncio.Event()

    class SlowFake(FakeLLMProvider):
        async def generate(self, req):
            entered.set()
            await asyncio.Event().wait()

    accounting = service(sessions, monthly_budget=Decimal("1"), minimum_reservation=Decimal("0.01"))
    task = asyncio.create_task(AccountingLLMProvider(SlowFake(), accounting).generate(request()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    summary = await accounting.summary(context=context())
    assert summary.failed_calls == 1 and summary.unknown_usage_calls == 1
    assert summary.pending_reserved_cost == Decimal("0.01")


def test_configuration_requires_explicit_budget_accounting() -> None:
    settings = AppSettings(COST_ACCOUNTING_ENABLED=True, COST_MODEL_PRICES_JSON='{"prices":[]}')
    assert settings.cost_currency == "USD"
    with pytest.raises(ValidationError, match="COST_MINIMUM_RESERVATION"):
        AppSettings(COST_MONTHLY_BUDGET="1")
    assert not AppSettings().cost_accounting_enabled


async def test_explicit_interval_includes_known_calls(sessions) -> None:
    accounting = service(sessions)
    await AccountingLLMProvider(FakeLLMProvider(), accounting).generate(request())
    end = datetime.now(UTC) + timedelta(seconds=1)
    summary = await accounting.summary(context=context(), start=end - timedelta(days=1), end=end)
    assert summary.calls == 1


async def test_operation_breakdown_and_real_factory_rag_agent_paths(sessions, monkeypatch) -> None:
    from apps.api.agent_stepper import LLMJsonAgentStepper
    from apps.api.factories import common
    from packages.agent.runtime import AgentRuntimeState
    from packages.rag import (
        PromptBuildResult,
        PromptBuildTrace,
        PromptMessage,
        RagGenerationService,
    )

    monkeypatch.setattr(common, "create_session_factory", lambda _: sessions)
    settings = AppSettings(
        COST_ACCOUNTING_ENABLED=True,
        COST_MODEL_PRICES_JSON=PriceCatalog(prices=(price(),)).model_dump_json(),
    )
    prompt = PromptBuildResult(
        messages=(PromptMessage(role="user", name="user_question", content="hello world"),),
        trace=PromptBuildTrace(
            request_id="metrics-request",
            trace_id="trace",
            tenant_id="tenant",
            user_id="user",
            context_item_count=0,
            source_chunk_count=0,
            input_char_count=11,
            prompt_part_count=1,
            detected_risk_count=0,
        ),
        citation_source_ids=(),
        metadata={},
    )
    rag = RagGenerationService(provider=common.create_llm_provider(settings, operation="rag.query"))
    await rag.generate(prompt=prompt, context=context())
    agent_settings = settings.model_copy(
        update={
            "llm_fake_response_text": '{"action":"final_answer","final_answer":"done"}',
        }
    )
    stepper = LLMJsonAgentStepper(
        llm_provider=common.create_llm_provider(agent_settings, operation="agent"),
        model="fake-llm",
        provider="fake",
        tool_names=(),
    )
    await stepper.next_step(
        AgentRuntimeState(
            request_id="metrics-request",
            trace_id="trace",
            tenant_id="tenant",
            user_id="user",
            input_text="hello",
            steps_used=0,
            tool_calls_used=0,
        )
    )
    metrics = await service(sessions).summary(context=context())
    assert metrics.calls == 2
    assert {row.operation for row in metrics.breakdown} == {"rag.query", "agent"}


async def test_cost_metrics_and_budget_http_api(sessions) -> None:
    from apps.api.dependencies import get_authenticated_request_context
    from apps.api.factories.billing import get_cost_accounting_service
    from apps.api.routes.costs import router

    accounting = service(sessions, monthly_budget=Decimal("1"), minimum_reservation=Decimal("0.01"))
    await AccountingLLMProvider(FakeLLMProvider(), accounting).generate(request())
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_authenticated_request_context] = context
    app.dependency_overrides[get_cost_accounting_service] = lambda: accounting
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        metrics = await client.get("/v1/costs/summary")
        budget = await client.get("/v1/costs/budget")
    assert metrics.status_code == 200 and metrics.json()["data"]["calls"] == 1
    assert metrics.json()["data"]["currency"] == "USD"
    assert budget.status_code == 200 and budget.json()["data"]["limit"] == "1"
    assert budget.json()["data"]["reserved"] == "0"


async def test_actual_overage_is_reported_and_blocks_further_calls(sessions) -> None:
    accounting = service(sessions, monthly_budget=Decimal("1"), minimum_reservation=Decimal("0.01"))
    ticket = await accounting.begin(request())
    response = await FakeLLMProvider().generate(request())
    metadata = response.metadata.model_copy(
        update={
            "usage": TokenUsage(input_tokens=1_000_000, output_tokens=0, total_tokens=1_000_000),
        }
    )
    await accounting.finish(ticket, metadata=metadata)
    budget = await accounting.budget(context=context())
    assert budget.over_budget and budget.spent == Decimal("1.25")
    assert budget.remaining == Decimal("-0.25")
    with pytest.raises(BillingError, match="COST_BUDGET_EXCEEDED"):
        await accounting.begin(request())


async def test_concurrent_reservations_use_one_atomic_budget_counter(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'budget.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(CostAccountModel.__table__.create)
        await connection.run_sync(UsageChargeModel.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    accounting = service(
        sessions, monthly_budget=Decimal("0.01"), minimum_reservation=Decimal("0.01")
    )
    outcomes = await asyncio.gather(
        accounting.begin(request()),
        accounting.begin(request()),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, BillingError) for outcome in outcomes) == 1
    assert (await accounting.budget(context=context())).reserved == Decimal("0.01")
    await engine.dispose()


def test_usage_cost_migration_is_additive_and_reversible() -> None:
    path = Path("migrations/versions/20261016_0020_usage_costs.py")
    spec = importlib.util.spec_from_file_location("usage_cost_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_data (id INTEGER PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert {"usage_charges", "cost_accounts", "existing_data"} == set(
            inspect(connection).get_table_names()
        )
        migration.downgrade()
        assert inspect(connection).get_table_names() == ["existing_data"]
    engine.dispose()
