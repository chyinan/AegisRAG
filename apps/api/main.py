import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from apps.api.dependencies import (
    close_cached_oidc_verifiers,
    get_token_revocation_store,
    validate_oidc_configuration,
)
from apps.api.error_handlers import register_error_handlers
from apps.api.middleware import DeprecationMiddleware, RequestLoggingMiddleware
from apps.api.rate_limit_middleware import RateLimitMiddleware, _build_limiter
from apps.api.routes.agent import router as agent_router
from apps.api.routes.audit_explorer import router as audit_explorer_router
from apps.api.routes.auth import router as auth_router
from apps.api.routes.chat import router as chat_router
from apps.api.routes.diagnostics import router as diagnostics_router
from apps.api.routes.documents import router as documents_router
from apps.api.routes.eval_evidence import router as eval_evidence_router
from apps.api.routes.governance import router as governance_router
from apps.api.routes.groups import router as groups_router
from apps.api.routes.health import router as health_router
from apps.api.routes.query import router as query_router
from apps.api.routes.retrieve import router as retrieve_router
from apps.api.routes.review_queue import router as review_queue_router
from apps.api.routes.service_token import router as service_token_router
from apps.api.routes.sidecar import SIDECAR_ROOT
from apps.api.routes.sidecar import router as sidecar_router
from apps.api.routes.sources import router as sources_router
from apps.api.routes.upload import router as upload_router
from apps.api.routes.users import router as users_router
from packages.common.config import AppSettings, load_settings
from packages.common.logging import configure_logging
from packages.common.rate_limit import RateLimitConfig
from packages.common.tracing import instrument_app, setup_tracing


def create_app() -> FastAPI:
    configure_logging()
    settings = load_settings()
    validate_oidc_configuration()

    # 0. Rate limiting (P0 — blocks excessive requests before any processing)
    rate_limit_config = _rate_limit_config(settings)
    login_rate_limit_config = RateLimitConfig(
        max_requests=5,
        window_seconds=60.0,
        key_prefix="rl_login",
    )
    rate_limiter = _build_limiter(
        config=rate_limit_config,
        redis_url=settings.redis_url,
        timeout_seconds=settings.rate_limit_backend_timeout_seconds,
    )
    path_limit_configs = {
        "/auth/login": login_rate_limit_config,
        "/auth/refresh": RateLimitConfig(
            max_requests=10, window_seconds=60.0, key_prefix="rl_refresh"
        ),
        "/v1/auth/login": login_rate_limit_config,
        "/v1/auth/refresh": RateLimitConfig(
            max_requests=10, window_seconds=60.0, key_prefix="rl_refresh"
        ),
    }
    path_limiters = {
        path: _build_limiter(
            config=path_config,
            redis_url=settings.redis_url,
            timeout_seconds=settings.rate_limit_backend_timeout_seconds,
        )
        for path, path_config in path_limit_configs.items()
    }
    revocation_store = get_token_revocation_store()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await rate_limiter.aclose()
            await asyncio.gather(
                *(limiter.aclose() for limiter in path_limiters.values()),
            )
            if revocation_store is not None:
                await revocation_store.aclose()
            await close_cached_oidc_verifiers()

    app = FastAPI(
        title="AegisRAG — Local RAG Agent System",
        version="0.2.0",
        lifespan=lifespan,
    )

    # -- Middleware stack (order matters: outermost first) -------------------

    # 1. API version deprecation — flags legacy (unprefixed) requests
    app.add_middleware(DeprecationMiddleware)

    app.add_middleware(
        RateLimitMiddleware,
        limiter=rate_limiter,
        path_limiters=path_limiters,
    )

    # 2. Request logging
    app.add_middleware(RequestLoggingMiddleware)

    # -------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------
    instrumentator = Instrumentator().instrument(app)
    instrumentator.expose(app, endpoint="/metrics", include_in_schema=True)

    setup_tracing(service_name="aegisrag-api")
    instrument_app(app, service_name="aegisrag-api")

    register_error_handlers(app)

    # -------------------------------------------------------------------
    # v1 router — canonical versioned API prefix
    # -------------------------------------------------------------------
    v1_router = APIRouter(prefix="/v1")

    # All application routers registered under /v1
    # (service_token is omitted — its paths already contain /v1/ as part of
    #  the OpenAI-compatible API contract.)
    _ROUTERS: tuple = (
        health_router,
        auth_router,
        groups_router,
        users_router,
        upload_router,
        documents_router,
        eval_evidence_router,
        retrieve_router,
        query_router,
        chat_router,
        agent_router,
        audit_explorer_router,
        review_queue_router,
        sources_router,
        diagnostics_router,
        sidecar_router,
        governance_router,
    )
    for r in _ROUTERS:
        v1_router.include_router(r)
        app.include_router(r)   # ← legacy (unprefixed) — receives deprecation headers

    # service_token — kept at root level only (paths are already /v1/…)
    app.include_router(service_token_router)

    app.include_router(v1_router)

    # -------------------------------------------------------------------
    # Version endpoint — discover current API version
    # -------------------------------------------------------------------
    @app.get("/api/version", include_in_schema=True, tags=["system"])
    def api_version():
        from packages.common.envelope import success_response
        return success_response(
            request_id="static",
            data={
                "api_version": "v1",
                "app_version": "0.2.0",
                "deprecated_routes_available": True,
            },
        )

    # -------------------------------------------------------------------
    # Static assets
    # -------------------------------------------------------------------
    app.mount(
        "/sidecar/assets",
        StaticFiles(directory=SIDECAR_ROOT, html=False),
        name="sidecar-assets",
    )
    return app


def _rate_limit_config(settings: AppSettings) -> RateLimitConfig:
    max_requests = getattr(settings, "rate_limit_max_requests", 100)
    window_seconds = getattr(settings, "rate_limit_window_seconds", 60.0)
    return RateLimitConfig(
        max_requests=max_requests,
        window_seconds=window_seconds,
    )


app = create_app()
