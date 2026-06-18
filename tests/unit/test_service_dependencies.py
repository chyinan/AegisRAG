from __future__ import annotations

import pytest
from pydantic import SecretStr

from apps.api.factories.common import (
    create_embedding_provider,
    create_llm_provider,
)
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
