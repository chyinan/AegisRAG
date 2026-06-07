from fastapi import FastAPI

from apps.api.error_handlers import register_error_handlers
from apps.api.middleware import RequestLoggingMiddleware
from apps.api.routes.chat import router as chat_router
from apps.api.routes.documents import router as documents_router
from apps.api.routes.health import router as health_router
from apps.api.routes.openwebui import router as openwebui_router
from apps.api.routes.query import router as query_router
from apps.api.routes.retrieve import router as retrieve_router
from apps.api.routes.sources import router as sources_router
from apps.api.routes.upload import router as upload_router
from packages.common.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Local RAG Agent System",
        version="0.1.0",
    )
    app.add_middleware(RequestLoggingMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(upload_router)
    app.include_router(documents_router)
    app.include_router(retrieve_router)
    app.include_router(query_router)
    app.include_router(chat_router)
    app.include_router(openwebui_router)
    app.include_router(sources_router)
    return app


app = create_app()
