from __future__ import annotations

import subprocess
import sys
from typing import TypedDict

import pytest
from pydantic import ValidationError

from apps.api.factories.agent import create_agent_runtime
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AgentActionType, AgentRunConfig, AgentRuntime, AgentStepDecision
from packages.common.audit import InMemoryAuditPort
from packages.common.config import AppSettings


class _FinalStepper:
    async def next_step(self, state: object) -> AgentStepDecision:
        return AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="Ready")


class _RuntimeArguments(TypedDict):
    registry: ToolRegistry
    stepper: _FinalStepper
    audit: None
    config: AgentRunConfig


def _settings(**values: object) -> AppSettings:
    return AppSettings.model_validate(values)


def _runtime_arguments() -> _RuntimeArguments:
    return {
        "registry": ToolRegistry(audit=InMemoryAuditPort()),
        "stepper": _FinalStepper(),
        "audit": None,
        "config": AgentRunConfig(
            max_steps=3,
            max_tool_calls=1,
            timeout_seconds=1,
            repeated_action_threshold=2,
        ),
    }


def test_native_defaults_do_not_import_optional_frameworks() -> None:
    code = """
import importlib.abc
import sys

class BlockFrameworks(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'langchain', 'langchain_core', 'langchain_openai', 'langgraph', 'langsmith'
        }:
            raise AssertionError('optional dependency imported: ' + fullname)

sys.meta_path.insert(0, BlockFrameworks())
from packages.common.config import AppSettings
from apps.api.factories.common import create_llm_provider, create_embedding_provider
from apps.api.factories.agent import create_agent_runtime
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AgentRunConfig, AgentStepDecision

class Stepper:
    async def next_step(self, state):
        return AgentStepDecision(action='final_answer', final_answer='Ready')

settings = AppSettings()
assert settings.llm_adapter == settings.embedding_adapter == 'native'
assert settings.agent_orchestration_backend == 'native'
assert type(create_llm_provider(settings)).__name__ == 'FakeLLMProvider'
assert type(create_embedding_provider(
    provider='fake', model='fake-embedding', dim=8, base_url=None, api_key=None, version=None
)).__name__ == 'FakeEmbeddingProvider'
assert type(create_agent_runtime(
    registry=ToolRegistry(audit=None), stepper=Stepper(), audit=None,
    config=AgentRunConfig(
        max_steps=2, max_tool_calls=1, timeout_seconds=1, repeated_action_threshold=2
    )
)).__name__ == 'AgentRuntime'
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_framework_choices_are_independent() -> None:
    settings = _settings(
        LLM_ADAPTER="langchain",
        LLM_PROVIDER="openai_compatible",
        LLM_MODEL="test-model",
        LLM_BASE_URL="https://example.invalid/v1",
        LLM_API_KEY="test-key",
        AGENT_ORCHESTRATION_BACKEND="native",
    )
    assert settings.embedding_adapter == "native"
    assert settings.llm_adapter == "langchain"
    assert settings.agent_orchestration_backend == "native"
    graph_only = _settings(AGENT_ORCHESTRATION_BACKEND=" LANGGRAPH ")
    assert graph_only.llm_adapter == "native"
    assert graph_only.agent_orchestration_backend == "langgraph"


@pytest.mark.parametrize("key", ["LLM_ADAPTER", "EMBEDDING_ADAPTER", "AGENT_ORCHESTRATION_BACKEND"])
def test_unknown_framework_choice_fails_configuration(key: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{key: "misspelled"})


@pytest.mark.parametrize("key", ["LLM_ADAPTER", "EMBEDDING_ADAPTER"])
def test_langchain_requires_a_compatible_provider(key: str) -> None:
    with pytest.raises(ValidationError, match="requires an OpenAI-compatible"):
        _settings(**{key: "langchain"})


def test_runtime_factory_defaults_and_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(create_agent_runtime(**_runtime_arguments()), AgentRuntime)
    with pytest.raises(ValueError, match="Unsupported agent orchestration"):
        create_agent_runtime(backend="unknown", **_runtime_arguments())
    monkeypatch.setitem(sys.modules, "langgraph.graph", None)
    with pytest.raises(RuntimeError, match="uv sync --extra langgraph"):
        create_agent_runtime(backend="langgraph", **_runtime_arguments())


def test_api_and_worker_use_the_same_embedding_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.factories.common import create_embedding_provider
    from apps.worker.jobs.embedding_jobs import _provider_from_settings
    from packages.embeddings.adapters import langchain

    calls: list[dict[str, object]] = []

    def capture(**kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(langchain, "LangChainEmbeddingProvider", capture)
    settings = _settings(
        EMBEDDING_ADAPTER="langchain",
        EMBEDDING_PROVIDER="openai_compatible",
        EMBEDDING_MODEL="test-embedding",
        EMBEDDING_BASE_URL="https://example.invalid/v1",
        EMBEDDING_API_KEY="test-key",
        EMBEDDING_PROVIDER_VERSION="version-1",
        EMBEDDING_DIM=8,
    )
    create_embedding_provider(
        adapter=settings.embedding_adapter,
        provider=settings.embedding_provider,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        version=settings.embedding_provider_version,
    )
    _provider_from_settings(settings)
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0]["provider"] == "openai_compatible"
    assert calls[0]["dim"] == 8


def test_llm_factory_selects_component_without_changing_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.factories.common import create_llm_provider
    from packages.llm.adapters import langchain

    captured: dict[str, object] = {}

    def capture(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(langchain, "LangChainChatProvider", capture)
    settings = _settings(
        LLM_ADAPTER="langchain",
        LLM_PROVIDER="qwen",
        LLM_MODEL="test-model",
        LLM_BASE_URL="https://example.invalid/v1",
        LLM_API_KEY="test-key",
        LLM_PROVIDER_VERSION="version-1",
    )
    create_llm_provider(settings)
    assert captured["provider"] == "qwen"
    assert captured["model"] == "test-model"
    assert captured["version"] == "version-1"
