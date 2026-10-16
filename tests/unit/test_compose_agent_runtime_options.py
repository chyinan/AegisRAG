"""Static runtime-option projection contracts, without Docker or live services.

The test-only renderer accepts one scalar ${NAME:-default} expression, and only
for the allowlisted runtime settings below. It does not implement Compose env
file parsing, precedence, nested expressions, escaping or engine validation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter
from yaml.nodes import MappingNode, ScalarNode

from packages.common.config import AppSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_SERVICES = (
    "api",
    "worker-ingestion",
    "worker-embedding",
    "migration",
    "bootstrap",
)
# Explicit defaults prevent simultaneous config/example/Compose drift from
# accidentally changing the existing native, JSON and non-durable contract.
RUNTIME_OPTIONS = {
    "AGENT_ORCHESTRATION_BACKEND": ("agent_orchestration_backend", "native"),
    "AGENT_DECISION_MODE": ("agent_decision_mode", "json"),
    "AGENT_DURABLE_ENABLED": ("agent_durable_enabled", "false"),
    "AGENT_DURABLE_RETENTION_SECONDS": ("agent_durable_retention_seconds", "86400"),
    "AGENT_DURABLE_LEASE_SECONDS": ("agent_durable_lease_seconds", "30"),
    "AGENT_DEFAULT_MAX_STEPS": ("agent_default_max_steps", "8"),
    "AGENT_DEFAULT_MAX_TOOL_CALLS": ("agent_default_max_tool_calls", "5"),
    "AGENT_DEFAULT_TIMEOUT_SECONDS": ("agent_default_timeout_seconds", "30"),
    "AGENT_PERSISTENCE_PHASE_TIMEOUT_SECONDS": (
        "agent_persistence_phase_timeout_seconds",
        "5",
    ),
    "AGENT_AUXILIARY_IO_TIMEOUT_SECONDS": ("agent_auxiliary_io_timeout_seconds", "1"),
    "RAG_AUXILIARY_IO_TIMEOUT_SECONDS": ("rag_auxiliary_io_timeout_seconds", "1"),
    "AGENT_REPEATED_ACTION_THRESHOLD": ("agent_repeated_action_threshold", "3"),
}
CUSTOM_VALUES = {
    "AGENT_ORCHESTRATION_BACKEND": "langgraph",
    "AGENT_DECISION_MODE": "native_tools",
    "AGENT_DURABLE_ENABLED": "true",
    "AGENT_DURABLE_RETENTION_SECONDS": "172800",
    "AGENT_DURABLE_LEASE_SECONDS": "45",
    "AGENT_DEFAULT_MAX_STEPS": "12",
    "AGENT_DEFAULT_MAX_TOOL_CALLS": "0",
    "AGENT_DEFAULT_TIMEOUT_SECONDS": "45.5",
    "AGENT_PERSISTENCE_PHASE_TIMEOUT_SECONDS": "7.5",
    "AGENT_AUXILIARY_IO_TIMEOUT_SECONDS": "0.25",
    "RAG_AUXILIARY_IO_TIMEOUT_SECONDS": "0.5",
    "AGENT_REPEATED_ACTION_THRESHOLD": "4",
}
SCALAR_DEFAULT = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^${}]*)\}")


def _render_runtime_option(expression: str, values: Mapping[str, str]) -> str:
    """Project only an allowlisted scalar; this is not a Compose renderer."""
    match = SCALAR_DEFAULT.fullmatch(expression)
    if match is None or match[1] not in RUNTIME_OPTIONS:
        raise ValueError("Not an allowlisted scalar runtime default expression")
    return values.get(match[1]) or match[2]


def _mapping_value(node: MappingNode, name: str) -> Any:
    matches = [value for key, value in node.value if key.value == name]
    assert len(matches) == 1, f"Expected one YAML mapping entry for {name}"
    return matches[0]


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load((PROJECT_ROOT / "docker/compose.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def example_values() -> dict[str, str]:
    values = {}
    for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name in RUNTIME_OPTIONS:
            assert name not in values, f"Duplicate example runtime setting: {name}"
            values[name] = value
    return values


@pytest.mark.parametrize("name", RUNTIME_OPTIONS)
def test_runtime_alias_and_default_match_settings_and_example(
    name: str,
    compose: dict[str, Any],
    example_values: dict[str, str],
) -> None:
    field_name, default = RUNTIME_OPTIONS[name]
    field = AppSettings.model_fields[field_name]

    assert (field.validation_alias or field.alias) == name
    assert TypeAdapter(field.annotation).validate_python(default) == field.default
    assert example_values[name] == default
    assert compose["x-api-environment"].get(name) == f"${{{name}:-{default}}}"


def test_runtime_options_use_existing_shared_environment_anchor(
    compose: dict[str, Any],
) -> None:
    # Inspect YAML nodes before merge expansion to prove consumers still use the
    # shared anchor, rather than equal-looking per-service copies.
    document = yaml.compose((PROJECT_ROOT / "docker/compose.yaml").read_text(encoding="utf-8"))
    assert isinstance(document, MappingNode)
    shared = _mapping_value(document, "x-api-environment")
    services = _mapping_value(document, "services")
    consumers = set()
    for service_name, service in services.value:
        environment_nodes = [value for key, value in service.value if key.value == "environment"]
        if not environment_nodes:
            continue
        for key, value in environment_nodes[0].value:
            if key.tag == "tag:yaml.org,2002:merge" and value is shared:
                consumers.add(service_name.value)
    assert consumers == set(APPLICATION_SERVICES)

    for service in APPLICATION_SERVICES:
        environment = _mapping_value(_mapping_value(services, service), "environment")
        assert _mapping_value(environment, "<<") is shared
        assert not any(key.value in RUNTIME_OPTIONS for key, _ in environment.value)
        actual = compose["services"][service]["environment"]
        for name in RUNTIME_OPTIONS:
            assert actual[name] == compose["x-api-environment"][name]


@pytest.mark.parametrize("service", APPLICATION_SERVICES)
@pytest.mark.parametrize("scenario", ("missing", "empty", "custom"))
def test_runtime_scalar_projection_per_inherited_service(
    service: str,
    scenario: str,
    compose: dict[str, Any],
) -> None:
    values = {
        "missing": {},
        "empty": dict.fromkeys(RUNTIME_OPTIONS, ""),
        "custom": CUSTOM_VALUES,
    }[scenario]
    assert set(CUSTOM_VALUES) == set(RUNTIME_OPTIONS)
    environment = compose["services"][service]["environment"]
    for name, (_, default) in RUNTIME_OPTIONS.items():
        expected = CUSTOM_VALUES[name] if scenario == "custom" else default
        assert _render_runtime_option(environment[name], values) == expected
        if scenario == "custom":
            assert expected != default


@pytest.mark.parametrize(
    "expression",
    (
        "${AGENT_DECISION_MODE}",
        "${AGENT_DECISION_MODE-json}",
        "prefix-${AGENT_DECISION_MODE:-json}",
        "${AGENT_DECISION_MODE:-${FALLBACK}}",
        "${UNRELATED_OPTION:-value}",
    ),
)
def test_scalar_projection_rejects_unsupported_expressions(expression: str) -> None:
    with pytest.raises(ValueError, match="allowlisted scalar"):
        _render_runtime_option(expression, {})


def test_runtime_projection_is_explicit_and_limited_to_application_services(
    compose: dict[str, Any],
) -> None:
    for service_name, service in compose["services"].items():
        assert "env_file" not in service
        if service_name not in APPLICATION_SERVICES:
            assert not set(RUNTIME_OPTIONS).intersection(service.get("environment", {}))
    shared = yaml.compose((PROJECT_ROOT / "docker/compose.yaml").read_text(encoding="utf-8"))
    assert isinstance(shared, MappingNode)
    environment = _mapping_value(shared, "x-api-environment")
    for name in RUNTIME_OPTIONS:
        value = _mapping_value(environment, name)
        assert isinstance(value, ScalarNode)
        assert value.tag == "tag:yaml.org,2002:str"
