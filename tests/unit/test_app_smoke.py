from fastapi import FastAPI

from apps.api.main import app


def test_fastapi_app_is_importable_without_external_services() -> None:
    assert isinstance(app, FastAPI)
    assert app.title == "Local RAG Agent System"
