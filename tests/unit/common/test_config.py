import pytest

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
    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("EMBEDDING_RETRY_BUDGET", "4")
    monkeypatch.setenv("EMBEDDING_QUEUE_NAME", "embedding")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_MODEL", "fake-llm-local")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("LLM_RETRY_BUDGET", "3")
    monkeypatch.setenv("LLM_FAKE_RESPONSE_TEXT", "local fake answer")
    monkeypatch.setenv("VECTOR_STORE_TYPE", "pgvector")
    monkeypatch.setenv("VECTOR_INDEX_DIM", "16")
    monkeypatch.setenv("VECTOR_DISTANCE_METRIC", "cosine")
    monkeypatch.setenv("PGVECTOR_INDEX_TYPE", "hnsw")
    monkeypatch.setenv("PGVECTOR_HNSW_M", "32")
    monkeypatch.setenv("PGVECTOR_HNSW_EF_CONSTRUCTION", "128")
    monkeypatch.setenv("WORKER_QUEUE_NAME", "embedding")
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "2.5")

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
    assert settings.embedding_timeout_seconds == 3.5
    assert settings.embedding_retry_budget == 4
    assert settings.embedding_queue_name == "embedding"
    assert settings.llm_provider == "fake"
    assert settings.llm_model == "fake-llm-local"
    assert settings.llm_timeout_seconds == 5.5
    assert settings.llm_retry_budget == 3
    assert settings.llm_fake_response_text == "local fake answer"
    assert settings.vector_store_type == "pgvector"
    assert settings.vector_index_dim == 16
    assert settings.vector_distance_metric == "cosine"
    assert settings.pgvector_index_type == "hnsw"
    assert settings.pgvector_hnsw_m == 32
    assert settings.pgvector_hnsw_ef_construction == 128
    assert settings.worker_queue_name == "embedding"
    assert settings.readiness_timeout_seconds == 2.5


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
        "EMBEDDING_TIMEOUT_SECONDS",
        "EMBEDDING_RETRY_BUDGET",
        "EMBEDDING_QUEUE_NAME",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
        "LLM_RETRY_BUDGET",
        "LLM_FAKE_RESPONSE_TEXT",
        "VECTOR_STORE_TYPE",
        "VECTOR_INDEX_DIM",
        "VECTOR_DISTANCE_METRIC",
        "PGVECTOR_INDEX_TYPE",
        "PGVECTOR_HNSW_M",
        "PGVECTOR_HNSW_EF_CONSTRUCTION",
        "WORKER_QUEUE_NAME",
        "READINESS_TIMEOUT_SECONDS",
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
    assert settings.embedding_timeout_seconds > 0
    assert settings.embedding_retry_budget >= 0
    assert settings.embedding_queue_name == "embedding"
    assert settings.llm_provider == "fake"
    assert settings.llm_model == "fake-llm"
    assert settings.llm_timeout_seconds > 0
    assert settings.llm_retry_budget >= 0
    assert settings.llm_fake_response_text
    assert settings.vector_store_type == "fake"
    assert settings.vector_index_dim > 0
    assert settings.vector_distance_metric == "cosine"
    assert settings.pgvector_index_type == "hnsw"
    assert settings.pgvector_hnsw_m > 0
    assert settings.pgvector_hnsw_ef_construction > 0
    assert settings.worker_queue_name == "ingestion"
    assert settings.readiness_timeout_seconds > 0
