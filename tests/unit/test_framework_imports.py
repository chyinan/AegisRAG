"""Synthetic distribution/import cases never import an actual framework."""

from __future__ import annotations

import importlib.metadata
import sys
from types import ModuleType

import pytest

from tests import framework_imports


@pytest.mark.parametrize("namespace_present", [False, True])
@pytest.mark.parametrize("required", [False, True])
def test_only_absent_optional_distribution_skips(
    monkeypatch: pytest.MonkeyPatch, namespace_present: bool, required: bool
) -> None:
    monkeypatch.setenv("AEGISRAG_REQUIRE_FRAMEWORKS", "1" if required else "0")
    if namespace_present:
        namespace = ModuleType("langgraph")
        namespace.__path__ = []
        monkeypatch.setitem(sys.modules, "langgraph", namespace)
    else:
        monkeypatch.delitem(sys.modules, "langgraph", raising=False)
    requested: list[str] = []
    imports: list[str] = []

    def absent(name: str) -> str:
        requested.append(name)
        raise importlib.metadata.PackageNotFoundError(name)

    def unexpected_import(name: str) -> ModuleType:
        imports.append(name)
        raise AssertionError("Absent distribution must be handled before importing")

    monkeypatch.setattr(importlib.metadata, "version", absent)
    monkeypatch.setattr(framework_imports, "import_module", unexpected_import)
    if required:
        with pytest.raises(pytest.UsageError, match="distribution is absent: langgraph") as failure:
            framework_imports.require_langgraph_prebuilt()
        assert isinstance(failure.value.__cause__, importlib.metadata.PackageNotFoundError)
    else:
        with pytest.raises(pytest.skip.Exception, match="distribution is absent: langgraph"):
            framework_imports.require_langgraph_prebuilt()
    assert requested == ["langgraph"]
    assert imports == []


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize(
    "error",
    [
        ImportError("cannot import name 'ExecutionInfo' from 'langgraph.runtime'"),
        ModuleNotFoundError("No module named 'missing_transitive'", name="missing_transitive"),
        ModuleNotFoundError("No module named 'langgraph.prebuilt'", name="langgraph.prebuilt"),
    ],
    ids=["incompatible-api", "missing-transitive", "missing-prebuilt"],
)
def test_installed_distribution_import_errors_propagate(
    monkeypatch: pytest.MonkeyPatch, required: bool, error: ImportError
) -> None:
    monkeypatch.setenv("AEGISRAG_REQUIRE_FRAMEWORKS", "1" if required else "0")
    requested: list[str] = []
    imports: list[str] = []

    def installed(name: str) -> str:
        requested.append(name)
        return "1.0.10"

    def broken_import(name: str) -> ModuleType:
        imports.append(name)
        raise error

    monkeypatch.setattr(importlib.metadata, "version", installed)
    monkeypatch.setattr(framework_imports, "import_module", broken_import)
    with pytest.raises(type(error)) as failure:
        framework_imports.require_langgraph_prebuilt()
    assert failure.value is error
    assert requested == ["langgraph"]
    assert imports == ["langgraph.prebuilt"]


@pytest.mark.parametrize("required", [False, True])
def test_installed_distribution_returns_imported_prebuilt(
    monkeypatch: pytest.MonkeyPatch, required: bool
) -> None:
    monkeypatch.setenv("AEGISRAG_REQUIRE_FRAMEWORKS", "1" if required else "0")
    prebuilt = ModuleType("langgraph.prebuilt")
    requests: list[str] = []

    def installed(name: str) -> str:
        requests.append(name)
        return "1.0.10"

    def valid_import(name: str) -> ModuleType:
        requests.append(name)
        return prebuilt

    monkeypatch.setattr(importlib.metadata, "version", installed)
    monkeypatch.setattr(framework_imports, "import_module", valid_import)
    assert framework_imports.require_langgraph_prebuilt() is prebuilt
    assert requests == ["langgraph", "langgraph.prebuilt"]


def test_unexpected_metadata_failure_is_not_optional_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AEGISRAG_REQUIRE_FRAMEWORKS", raising=False)

    def broken_metadata(name: str) -> str:
        raise RuntimeError("Metadata lookup failed")

    monkeypatch.setattr(importlib.metadata, "version", broken_metadata)
    with pytest.raises(RuntimeError, match="Metadata lookup failed"):
        framework_imports.require_langgraph_prebuilt()
