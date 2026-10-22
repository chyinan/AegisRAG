"""CI must never report a green optional-framework job consisting of skips."""

from __future__ import annotations

import importlib
import os

import pytest

# Test transports never need a collector or background telemetry export.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")


@pytest.fixture(scope="session", autouse=True)
def _required_frameworks_are_installed() -> None:
    if os.environ.get("AEGISRAG_REQUIRE_FRAMEWORKS") == "1":
        for module in (
            "langchain_core", "langchain_openai", "langgraph.graph", "langgraph.prebuilt"
        ):
            try:
                importlib.import_module(module)
            except ImportError as exc:
                raise pytest.UsageError(f"Required framework import failed: {module}") from exc
