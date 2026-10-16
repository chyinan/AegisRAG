"""Recovery keys stay in request bodies, never access-log URLs or responses."""

from __future__ import annotations

from uuid import uuid1, uuid4

import httpx
import pytest

from tests.integration.api.test_durable_agent_routes import _headers
from tests.integration.api.test_durable_agent_routes import durable_client as durable_client


async def test_start_recover_duplicate_mismatch_and_no_store(durable_client, capsys):
    key = str(uuid4())
    body = {"input": "Calculate", "approval_tools": ["calculator"], "idempotency_key": key}
    first = await durable_client.post("/agent/durable", headers=_headers(), json=body)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    original = first.json()["data"]
    for prefix in ("", "/v1"):
        duplicate = await durable_client.post(
            f"{prefix}/agent/durable", headers=_headers(), json=body
        )
        recovered = await durable_client.post(
            f"{prefix}/agent/durable/recover", headers=_headers(), json={"idempotency_key": key}
        )
        for response in (duplicate, recovered):
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["data"] == original
            assert key not in response.text
    conflict = await durable_client.post(
        "/agent/durable", headers=_headers(), json={**body, "input": "Different request"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "DURABLE_START_KEY_CONFLICT"
    assert conflict.headers["cache-control"] == "no-store"
    logs = capsys.readouterr().out
    assert key not in logs
    assert "path=/agent/durable/recover" in logs
    assert original["approval"] is not None and original["tool_calls_used"] == 0


async def test_recover_unknown_isolated_invalid_and_forbidden_no_store(durable_client):
    key = str(uuid4())
    started = await durable_client.post(
        "/agent/durable", headers=_headers(), json={"input": "Calculate", "idempotency_key": key}
    )
    assert started.status_code == 200
    cases = [
        (_headers(), {"idempotency_key": str(uuid4())}, 404),
        ({**_headers(), "X-User-ID": "other"}, {"idempotency_key": key}, 404),
        ({**_headers(), "X-Tenant-ID": "other"}, {"idempotency_key": key}, 404),
        (_headers(""), {"idempotency_key": key}, 403),
        (_headers("agent:run"), {"idempotency_key": key}, 403),
        (_headers(), {"idempotency_key": "invalid"}, 422),
        (_headers(), {"idempotency_key": str(uuid1())}, 422),
        (_headers(), {"idempotency_key": key, "approve": True}, 422),
        (_headers(), {}, 422),
    ]
    for headers, payload, status in cases:
        response = await durable_client.post(
            "/agent/durable/recover", headers=headers, json=payload
        )
        assert response.status_code == status, response.text
        assert response.headers["cache-control"] == "no-store"
        assert key not in response.text


async def test_disabled_recover_stays_unavailable_and_no_store(monkeypatch):
    from apps.api.main import app

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    monkeypatch.setenv("AGENT_DURABLE_ENABLED", "false")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/agent/durable/recover", headers=_headers(), json={"idempotency_key": str(uuid4())}
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DURABLE_AGENT_DISABLED"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/agent/durable", "no-store"),
        ("/v1/agent/durable/recover", "no-store"),
        ("/agent/run", "private, max-age=60"),
        ("/v1/agent/durable-preview", "private, max-age=60"),
    ],
)
async def test_error_envelope_only_overrides_durable_cache_policy(path, expected):
    from fastapi import FastAPI, HTTPException

    from apps.api.error_handlers import register_error_handlers
    from apps.api.middleware import RequestLoggingMiddleware

    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    register_error_handlers(app)

    @app.post(path)
    async def unavailable():
        raise HTTPException(status_code=503, headers={"cache-control": "private, max-age=60"})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(path)
    assert response.status_code == 503
    assert response.headers.get_list("cache-control") == [expected]
