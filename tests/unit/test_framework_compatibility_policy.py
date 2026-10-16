import importlib.metadata
import tomllib
from pathlib import Path

import pytest

from scripts.framework_compat import framework_requirements, minimum_requirement, version_report


def test_minimum_matrix_uses_the_declared_supported_floors() -> None:
    requirements = framework_requirements()
    assert {item.name for item in requirements} == {
        "langchain-core",
        "langchain-openai",
        "langgraph",
        "langgraph-checkpoint",
        "langgraph-prebuilt",
    }
    assert {minimum_requirement(item) for item in requirements} == {
        "langchain-core==1.3.3",
        "langchain-openai==1.1.14",
        "langgraph==1.0.10",
        "langgraph-checkpoint==4.1.1",
        "langgraph-prebuilt==1.0.8",
    }
    for requirement in requirements:
        version = minimum_requirement(requirement).split("==", 1)[1]
        assert version in requirement.specifier


def test_declared_framework_ranges_include_the_locked_profile() -> None:
    lock = tomllib.loads(Path("uv.lock").read_text())
    locked = {package["name"]: package["version"] for package in lock["package"]}
    requirements = {item.name: item for item in framework_requirements()}
    assert "1.1.0" in requirements["langgraph-prebuilt"].specifier
    for name, requirement in requirements.items():
        assert locked[name] in requirement.specifier


def test_version_report_covers_all_five_declared_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = {
        requirement.name: minimum_requirement(requirement).split("==", 1)[1]
        for requirement in framework_requirements()
    }
    requested: list[str] = []

    def version(name: str) -> str:
        requested.append(name)
        return versions[name]

    monkeypatch.setattr(importlib.metadata, "version", version)
    assert version_report() == {"framework_versions": versions, "provider_network_calls": False}
    assert len(requested) == 5
    assert set(requested) == set(versions)


def test_version_report_rejects_prebuilt_outside_the_declared_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = {
        requirement.name: minimum_requirement(requirement).split("==", 1)[1]
        for requirement in framework_requirements()
    }
    versions["langgraph-prebuilt"] = "2.0.0"
    monkeypatch.setattr(importlib.metadata, "version", versions.__getitem__)
    with pytest.raises(ValueError, match="Installed langgraph-prebuilt is outside"):
        version_report()


def test_version_matrix_is_required_and_does_not_resync_resolved_versions() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text()
    job = workflow.split("  framework-compatibility:\n")[1].split("  web:\n")[0]
    assert "versions: [locked, minimum, latest-compatible]" in job
    assert 'AEGISRAG_REQUIRE_FRAMEWORKS: "1"' in job
    assert "uv pip check" in job
    assert "uv run --no-sync pytest" in job
    assert "tests/integration/framework" in job
    assert "continue-on-error" not in job
    assert "AEGISRAG_REAL_ENDPOINT_ALLOWED" not in workflow
    assert "needs: [test, web, framework-compatibility]" in workflow


def test_framework_jobs_include_full_review_and_new_application_contracts() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text()
    optional = workflow.split("      - name: Optional framework contracts\n", 1)[1].split(
        "      - name:", 1
    )[0]
    compatibility = workflow.split("  framework-compatibility:\n", 1)[1].split("  web:\n", 1)[0]
    for job in (optional, compatibility):
        for path in (
            "tests/unit/llm",
            "tests/unit/embeddings",
            "tests/unit/agent",
            "tests/unit/framework_exports",
            "tests/integration/framework",
            "tests/integration/framework_exports",
            "tests/review",
            "tests/integration/api/test_durable_agent_routes.py",
            "tests/integration/storage/test_agent_stream_persistence.py",
        ):
            assert f"          {path}\n" in job
        assert 'AEGISRAG_REQUIRE_FRAMEWORKS: "1"' in job
        assert "tests/unit/agent/test_durable_execution.py" not in job
