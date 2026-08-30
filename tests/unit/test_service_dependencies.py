from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr

import apps.api.factories.domain as domain_factories
from apps.api.agent_stepper import LLMJsonAgentStepper
from apps.api.factories.common import (
    create_embedding_provider,
    create_llm_provider,
)
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AgentRunConfig, AgentRuntime
from packages.common.audit import InMemoryAuditPort
from packages.common.config import AppSettings
from packages.data.storage.exceptions import StorageConfigurationError
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider
from packages.llm.adapters import FakeLLMProvider, OpenAICompatibleChatProvider


def test_llm_provider_factory_keeps_fake_as_default() -> None:
    provider = create_llm_provider(AppSettings())

    assert isinstance(provider, FakeLLMProvider)


@pytest.mark.parametrize("provider_name", ["openai_compatible", "openai", "qwen", "deepseek"])
def test_llm_provider_factory_maps_real_aliases_to_openai_compatible_adapter(
    provider_name: str,
) -> None:
    provider = create_llm_provider(
        AppSettings(
            LLM_PROVIDER=provider_name,
            LLM_MODEL="configured-model",
            LLM_BASE_URL="https://llm.example/v1",
            LLM_API_KEY=SecretStr("test-secret"),
            LLM_PROVIDER_VERSION="compatible-v1",
        )
    )

    assert isinstance(provider, OpenAICompatibleChatProvider)
    assert "test-secret" not in repr(provider)


def test_llm_provider_factory_rejects_unknown_provider() -> None:
    with pytest.raises(StorageConfigurationError) as exc_info:
        create_llm_provider(AppSettings(LLM_PROVIDER="unknown-provider"))

    assert exc_info.value.code == "STORAGE_CONFIGURATION_ERROR"
    assert exc_info.value.details["provider"] == "unknown-provider"


@pytest.mark.asyncio
async def test_agent_factory_wires_real_llm_stepper_for_non_fake_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            del args

    class SessionFactory:
        def __call__(self) -> SessionContext:
            return SessionContext()

    class Registry(ToolRegistry):
        @property
        def registered_tool_names(self) -> frozenset[str]:
            return frozenset({"calculator"})

    settings = AppSettings(
        LLM_PROVIDER="openai_compatible",
        LLM_MODEL="configured-model",
        LLM_BASE_URL="https://llm.example/v1",
        LLM_API_KEY=SecretStr("test-secret"),
    )
    monkeypatch.setattr(domain_factories, "load_settings", lambda: settings)
    monkeypatch.setattr(domain_factories, "create_session_factory", lambda _url: SessionFactory())
    monkeypatch.setattr(
        domain_factories,
        "create_retrieval_service",
        lambda **_kwargs: (object(), None, None),
    )
    monkeypatch.setattr(
        domain_factories,
        "_build_agent_tool_registry",
        lambda **_kwargs: Registry(audit=InMemoryAuditPort()),
    )
    monkeypatch.setattr(domain_factories, "create_llm_provider", lambda _settings: object())

    generator: AsyncIterator[object] = domain_factories.get_agent_run_application_service()
    service = await anext(generator)
    runtime = service._runtime_factory(  # type: ignore[attr-defined]
        AgentRunConfig(
            max_steps=2,
            max_tool_calls=2,
            timeout_seconds=5,
            repeated_action_threshold=2,
            input_text="calculate 1+1",
        ),
        "agent-run-1",
    )

    assert isinstance(runtime, AgentRuntime)
    assert isinstance(runtime._stepper, LLMJsonAgentStepper)  # type: ignore[attr-defined]
    await generator.aclose()


def test_embedding_provider_factory_keeps_fake_as_default() -> None:
    provider = create_embedding_provider(
        provider="fake",
        model="fake-embedding",
        dim=8,
        base_url=None,
        api_key=None,
        version=None,
    )

    assert isinstance(provider, FakeEmbeddingProvider)


@pytest.mark.parametrize("provider_name", ["openai_compatible", "openai", "qwen", "ollama"])
def test_embedding_provider_factory_maps_real_aliases_to_openai_compatible_adapter(
    provider_name: str,
) -> None:
    provider = create_embedding_provider(
        provider=provider_name,
        model="configured-embedding",
        dim=768,
        base_url="http://ollama.local/v1",
        api_key=SecretStr("embedding-secret"),
        version="compatible-v1",
    )

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert "embedding-secret" not in repr(provider)


def test_embedding_provider_factory_rejects_real_provider_without_base_url() -> None:
    with pytest.raises(StorageConfigurationError) as exc_info:
        create_embedding_provider(
            provider="openai_compatible",
            model="configured-embedding",
            dim=768,
            base_url=None,
            api_key=None,
            version=None,
        )

    assert exc_info.value.code == "STORAGE_CONFIGURATION_ERROR"
    assert exc_info.value.details["provider"] == "openai_compatible"
