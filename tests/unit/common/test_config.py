import pytest
from pydantic import ValidationError

from packages.common.config import AppSettings, load_settings


def test_database_url_defaults_to_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = load_settings()

    assert isinstance(settings, AppSettings)
    assert settings.database_url is None


def test_database_url_is_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = "postgresql+asyncpg://user:password@db:5432/app"
    monkeypatch.setenv("DATABASE_URL", database_url)

    settings = load_settings()

    assert settings.database_url == database_url


def test_dependency_and_worker_settings_are_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "local-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "local-secret")
    monkeypatch.setenv("MINIO_BUCKET", "documents")
    monkeypatch.setenv("MINIO_DOCUMENT_PREFIX", "raw")
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "1024")
    monkeypatch.setenv("INGESTION_QUEUE_NAME", "ingestion")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_MODEL", "fake-embedding")
    monkeypatch.setenv("EMBEDDING_DIM", "16")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.example/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret-key")
    monkeypatch.setenv("EMBEDDING_PROVIDER_VERSION", "embedding-compatible-v1")
    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("EMBEDDING_RETRY_BUDGET", "4")
    monkeypatch.setenv("EMBEDDING_QUEUE_NAME", "embedding")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_MODEL", "fake-llm-local")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("LLM_RETRY_BUDGET", "3")
    monkeypatch.setenv("LLM_FAKE_RESPONSE_TEXT", "local fake answer")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "local-secret-key")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "512")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.3")
    monkeypatch.setenv("LLM_PROVIDER_VERSION", "compatible-v1")
    monkeypatch.setenv("VECTOR_STORE_TYPE", "pgvector")
    monkeypatch.setenv("VECTOR_INDEX_DIM", "16")
    monkeypatch.setenv("VECTOR_DISTANCE_METRIC", "cosine")
    monkeypatch.setenv("PGVECTOR_INDEX_TYPE", "hnsw")
    monkeypatch.setenv("PGVECTOR_HNSW_M", "32")
    monkeypatch.setenv("PGVECTOR_HNSW_EF_CONSTRUCTION", "128")
    monkeypatch.setenv("WORKER_QUEUE_NAME", "embedding")
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("TOOL_DEFAULT_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("TOOL_DEFAULT_RATE_LIMIT_MAX_CALLS", "7")
    monkeypatch.setenv("TOOL_DEFAULT_RATE_LIMIT_WINDOW_SECONDS", "30.0")
    monkeypatch.setenv("AGENT_DEFAULT_MAX_STEPS", "9")
    monkeypatch.setenv("AGENT_DEFAULT_MAX_TOOL_CALLS", "4")
    monkeypatch.setenv("AGENT_DEFAULT_TIMEOUT_SECONDS", "25.5")
    monkeypatch.setenv("AGENT_REPEATED_ACTION_THRESHOLD", "3")

    settings = load_settings()

    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.minio_endpoint == "http://minio:9000"
    assert settings.minio_access_key == "local-access"
    assert settings.minio_secret_key == "local-secret"
    assert settings.minio_bucket == "documents"
    assert settings.minio_document_prefix == "raw"
    assert settings.upload_max_bytes == 1024
    assert settings.ingestion_queue_name == "ingestion"
    assert settings.embedding_provider == "fake"
    assert settings.embedding_model == "fake-embedding"
    assert settings.embedding_dim == 16
    assert settings.embedding_base_url == "https://embedding.example/v1"
    assert settings.embedding_api_key is not None
    assert settings.embedding_api_key.get_secret_value() == "embedding-secret-key"
    assert settings.embedding_provider_version == "embedding-compatible-v1"
    assert settings.embedding_timeout_seconds == 3.5
    assert settings.embedding_retry_budget == 4
    assert settings.embedding_queue_name == "embedding"
    assert settings.llm_provider == "fake"
    assert settings.llm_model == "fake-llm-local"
    assert settings.llm_timeout_seconds == 5.5
    assert settings.llm_retry_budget == 3
    assert settings.llm_fake_response_text == "local fake answer"
    assert settings.llm_base_url == "https://llm.example/v1"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "local-secret-key"
    assert settings.llm_max_output_tokens == 512
    assert settings.llm_temperature == 0.3
    assert settings.llm_provider_version == "compatible-v1"
    assert settings.vector_store_type == "pgvector"
    assert settings.vector_index_dim == 16
    assert settings.vector_distance_metric == "cosine"
    assert settings.pgvector_index_type == "hnsw"
    assert settings.pgvector_hnsw_m == 32
    assert settings.pgvector_hnsw_ef_construction == 128
    assert settings.worker_queue_name == "embedding"
    assert settings.readiness_timeout_seconds == 2.5
    assert settings.tool_default_timeout_seconds == 4.5
    assert settings.tool_default_rate_limit_max_calls == 7
    assert settings.tool_default_rate_limit_window_seconds == 30.0
    assert settings.agent_default_max_steps == 9
    assert settings.agent_default_max_tool_calls == 4
    assert settings.agent_default_timeout_seconds == 25.5
    assert settings.agent_repeated_action_threshold == 3


def test_dependency_settings_default_to_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "REDIS_URL",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "MINIO_DOCUMENT_PREFIX",
        "UPLOAD_MAX_BYTES",
        "INGESTION_QUEUE_NAME",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIM",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_PROVIDER_VERSION",
        "EMBEDDING_TIMEOUT_SECONDS",
        "EMBEDDING_RETRY_BUDGET",
        "EMBEDDING_QUEUE_NAME",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
        "LLM_RETRY_BUDGET",
        "LLM_FAKE_RESPONSE_TEXT",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MAX_OUTPUT_TOKENS",
        "LLM_TEMPERATURE",
        "LLM_PROVIDER_VERSION",
        "VECTOR_STORE_TYPE",
        "VECTOR_INDEX_DIM",
        "VECTOR_DISTANCE_METRIC",
        "PGVECTOR_INDEX_TYPE",
        "PGVECTOR_HNSW_M",
        "PGVECTOR_HNSW_EF_CONSTRUCTION",
        "WORKER_QUEUE_NAME",
        "READINESS_TIMEOUT_SECONDS",
        "TOOL_DEFAULT_TIMEOUT_SECONDS",
        "TOOL_DEFAULT_RATE_LIMIT_MAX_CALLS",
        "TOOL_DEFAULT_RATE_LIMIT_WINDOW_SECONDS",
        "AGENT_DEFAULT_MAX_STEPS",
        "AGENT_DEFAULT_MAX_TOOL_CALLS",
        "AGENT_DEFAULT_TIMEOUT_SECONDS",
        "AGENT_REPEATED_ACTION_THRESHOLD",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.redis_url is None
    assert settings.minio_endpoint is None
    assert settings.minio_access_key is None
    assert settings.minio_secret_key is None
    assert settings.minio_bucket is None
    assert settings.minio_document_prefix == "raw-documents"
    assert settings.upload_max_bytes > 0
    assert settings.ingestion_queue_name == "ingestion"
    assert settings.embedding_provider == "fake"
    assert settings.embedding_model == "fake-embedding"
    assert settings.embedding_dim > 0
    assert settings.embedding_base_url is None
    assert settings.embedding_api_key is None
    assert settings.embedding_provider_version is None
    assert settings.embedding_timeout_seconds > 0
    assert settings.embedding_retry_budget >= 0
    assert settings.embedding_queue_name == "embedding"
    assert settings.llm_provider == "fake"
    assert settings.llm_model == "fake-llm"
    assert settings.llm_timeout_seconds > 0
    assert settings.llm_retry_budget >= 0
    assert settings.llm_fake_response_text
    assert settings.llm_base_url is None
    assert settings.llm_api_key is None
    assert settings.llm_max_output_tokens is None
    assert settings.llm_temperature is None
    assert settings.llm_provider_version is None
    assert settings.vector_store_type == "fake"
    assert settings.vector_index_dim > 0
    assert settings.vector_distance_metric == "cosine"
    assert settings.pgvector_index_type == "hnsw"
    assert settings.pgvector_hnsw_m > 0
    assert settings.pgvector_hnsw_ef_construction > 0
    assert settings.worker_queue_name == "ingestion"
    assert settings.readiness_timeout_seconds > 0
    assert settings.tool_default_timeout_seconds > 0
    assert settings.tool_default_rate_limit_max_calls > 0
    assert settings.tool_default_rate_limit_window_seconds > 0
    assert settings.agent_default_max_steps > 0
    assert settings.agent_default_max_tool_calls >= 0
    assert settings.agent_default_timeout_seconds > 0
    assert settings.agent_repeated_action_threshold > 0


def test_agent_runtime_config_rejects_invalid_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DEFAULT_MAX_STEPS", "0")

    with pytest.raises(ValidationError):
        load_settings()


def test_llm_api_key_is_secret_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key")

    settings = load_settings()

    assert settings.llm_api_key is not None
    assert "super-secret-key" not in repr(settings)
    assert "super-secret-key" not in str(settings.model_dump())


def test_embedding_api_key_is_secret_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-super-secret-key")

    settings = load_settings()

    assert settings.embedding_api_key is not None
    assert "embedding-super-secret-key" not in repr(settings)
    assert "embedding-super-secret-key" not in str(settings.model_dump())


def test_real_embedding_provider_requires_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai_compatible")
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)

    with pytest.raises(ValidationError):
        load_settings()


def test_real_llm_provider_requires_base_url_and_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        load_settings()
