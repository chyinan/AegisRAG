from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from apps.api.error_handlers import register_error_handlers
from apps.api.middleware import RequestLoggingMiddleware
from apps.api.rate_limit_middleware import RateLimitMiddleware
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
from apps.api.routes.openwebui import router as openwebui_router
from apps.api.routes.query import router as query_router
from apps.api.routes.retrieve import router as retrieve_router
from apps.api.routes.review_queue import router as review_queue_router
from apps.api.routes.sidecar import SIDECAR_ROOT
from apps.api.routes.sidecar import router as sidecar_router
from apps.api.routes.sources import router as sources_router
from apps.api.routes.upload import router as upload_router
from apps.api.routes.users import router as users_router
from packages.common.config import AppSettings, load_settings
from packages.common.logging import configure_logging
from packages.common.rate_limit import RateLimitConfig


def create_app() -> FastAPI:
    configure_logging()
    settings = load_settings()

    app = FastAPI(
        title="Local RAG Agent System",
        version="0.2.0",
    )

    # Order matters: outermost first
    # 1. Rate limiting (P0 — blocks excessive requests before any processing)
    rate_limit_config = _rate_limit_config(settings)
    login_rate_limit_config = RateLimitConfig(
        max_requests=5,
        window_seconds=60.0,
        key_prefix="rl_login",
    )
    app.add_middleware(
        RateLimitMiddleware,
        config=rate_limit_config,
        path_limits={
            "/auth/login": login_rate_limit_config,
            "/auth/refresh": RateLimitConfig(
                max_requests=10, window_seconds=60.0, key_prefix="rl_refresh"
            ),
        },
    )

    # 2. Request logging (existing)
    app.add_middleware(RequestLoggingMiddleware)

    register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(groups_router)
    app.include_router(users_router)
    app.include_router(upload_router)
    app.include_router(documents_router)
    app.include_router(eval_evidence_router)
    app.include_router(retrieve_router)
    app.include_router(query_router)
    app.include_router(chat_router)
    app.include_router(agent_router)
    app.include_router(audit_explorer_router)
    app.include_router(review_queue_router)
    app.include_router(openwebui_router)
    app.include_router(sources_router)
    app.include_router(diagnostics_router)
    app.include_router(sidecar_router)
    app.include_router(governance_router)

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
