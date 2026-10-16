"""Skip absent optional distributions, never broken installed framework imports."""

from __future__ import annotations

import importlib.metadata
import os
from importlib import import_module
from types import ModuleType

import pytest


def require_langgraph(module: str = "langgraph.graph") -> ModuleType:
    """A shared namespace alone does not establish that LangGraph is installed."""
    try:
        importlib.metadata.version("langgraph")
    except importlib.metadata.PackageNotFoundError as exc:
        if os.environ.get("AEGISRAG_REQUIRE_FRAMEWORKS") == "1":
            raise pytest.UsageError("Required framework distribution is absent: langgraph") from exc
        pytest.skip("Optional framework distribution is absent: langgraph")

    # Missing transitive dependencies and incompatible installed APIs must fail.
    return import_module(module)


def require_langgraph_prebuilt() -> ModuleType:
    return require_langgraph("langgraph.prebuilt")
