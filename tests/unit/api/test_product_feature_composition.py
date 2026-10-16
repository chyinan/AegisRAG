"""Normal shared configuration and canonical application route composition."""

from __future__ import annotations

import importlib

import pytest

from packages.common.config import AppSettings


def test_disabled_feature_configuration_accepts_compose_empty_optional_amounts() -> None:
    settings = AppSettings(
        COST_ACCOUNTING_ENABLED="false",
        COST_MONTHLY_BUDGET="",
        COST_MINIMUM_RESERVATION="",
        COST_MODEL_PRICES_JSON="",
        AGENT_DURABLE_BACKGROUND_ENABLED="false",
        SUPPORT_TICKET_ENABLED="false",
    )
    assert settings.cost_monthly_budget is settings.cost_minimum_reservation is None
    assert not settings.cost_accounting_enabled
    assert not settings.agent_durable_background_enabled and not settings.support_ticket_enabled


def test_application_registers_all_canonical_feature_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    module = importlib.import_module("apps.api.main")
    paths = module.app.openapi()["paths"]
    required = {
        "/v1/queue/jobs",
        "/v1/queue/jobs/{job_kind}/{queue_job_id}/retry",
        "/v1/sources/folder/sync/plan",
        "/v1/sources/folder/sync/plans/{plan_id}/reconcile",
        "/v1/costs/summary",
        "/v1/costs/budget",
        "/v1/support-tickets",
        "/v1/support-tickets/manage",
        "/v1/support-tickets/{ticket_id}/status",
        "/v1/agent/durable",
    }
    assert required.issubset(paths)
    command = paths["/v1/agent/durable"]["post"]
    assert command["requestBody"]["content"]["application/json"]["schema"]
