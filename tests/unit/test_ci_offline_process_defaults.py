"""Static process defaults for offline CI, without importing application SDKs."""

import re
from pathlib import Path

import pytest

_OFFLINE_DEFAULTS = {
    "OTEL_SDK_DISABLED": '"true"',
    "RAGAS_DO_NOT_TRACK": '"true"',
}


def _block(source: str, header: str, indent: int) -> str:
    """Read one block in the workflow's existing block-style indentation."""
    lines = source.splitlines()
    starts = [i for i, line in enumerate(lines) if line == " " * indent + header]
    assert len(starts) <= 1, f"Duplicate workflow block: {header}"
    if not starts:
        return ""
    start = starts[0] + 1
    end = start
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#"):
            if len(line) - len(line.lstrip()) <= indent:
                break
        end += 1
    return "\n".join(lines[start:end])


def _environment(source: str, indent: int) -> dict[str, str]:
    """Keep scalar spelling so flags must be quoted strings, not YAML booleans."""
    headers = [
        line for line in source.splitlines() if re.fullmatch(rf"{' ' * indent}env\s*:.*", line)
    ]
    # Reject alternate forms instead of silently overlooking inherited defaults.
    assert all(line == " " * indent + "env:" for line in headers)
    environment: dict[str, str] = {}
    for line in _block(source, "env:", indent).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert len(line) - len(line.lstrip()) == indent + 2
        key, separator, value = line.strip().partition(":")
        assert separator and key not in environment
        environment[key] = value.strip()
    return environment


def _workflow_and_job(name: str) -> tuple[str, str]:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = _block(_block(workflow, "jobs:", 0), f"{name}:", 2)
    assert job, f"Missing workflow job: {name}"
    return workflow, job


@pytest.mark.parametrize(
    ("phase", "existing_environment"),
    [
        ("Unit tests", {}),
        ("Integration mock tests", {}),
        ("Optional framework contracts", {"AEGISRAG_REQUIRE_FRAMEWORKS": '"1"'}),
        ("Actual Ragas offline contracts", {}),
    ],
)
def test_offline_backend_processes_declare_step_local_defaults(
    phase: str, existing_environment: dict[str, str]
) -> None:
    _, backend = _workflow_and_job("test")
    step = _block(backend, f"- name: {phase}", 6)
    assert step, f"Missing offline phase: {phase}"
    assert _environment(step, 8) == {**existing_environment, **_OFFLINE_DEFAULTS}


def test_framework_compatibility_processes_inherit_offline_defaults() -> None:
    _, compatibility = _workflow_and_job("framework-compatibility")
    assert _environment(compatibility, 4) == {
        "AEGISRAG_REQUIRE_FRAMEWORKS": '"1"',
        **_OFFLINE_DEFAULTS,
    }


@pytest.mark.parametrize(
    "phase",
    [
        "Initialize disposable PostgreSQL roles",
        "PostgreSQL pilot gate",
        "PostgreSQL durable gate",
    ],
)
def test_physical_postgres_processes_inherit_no_new_offline_flags(phase: str) -> None:
    workflow, backend = _workflow_and_job("test")
    step = _block(backend, f"- name: {phase}", 6)
    assert step, f"Missing physical phase: {phase}"
    # Global/job defaults would leak into physical steps even without step-local flags.
    for source, indent in ((workflow, 0), (backend, 4), (step, 8)):
        assert not _OFFLINE_DEFAULTS.keys() & _environment(source, indent).keys()


def test_offline_flag_declarations_are_limited_to_approved_scopes() -> None:
    workflow, _ = _workflow_and_job("test")
    for variable in _OFFLINE_DEFAULTS:
        declarations = re.findall(rf"{variable}[\"']?\s*:", workflow)
        # Four backend steps and one compatibility job; no other workflow defaults.
        assert len(declarations) == 5
    # The provider opt-in remains entirely absent under the existing CI contract.
    assert "AEGISRAG_REAL_ENDPOINT_ALLOWED" not in workflow
