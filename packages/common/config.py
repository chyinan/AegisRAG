from __future__ import annotations

import math

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    minio_endpoint: str | None = Field(default=None, alias="MINIO_ENDPOINT")
    minio_access_key: str | None = Field(default=None, alias="MINIO_ACCESS_KEY")
    minio_secret_key: str | None = Field(default=None, alias="MINIO_SECRET_KEY")
    minio_bucket: str | None = Field(default=None, alias="MINIO_BUCKET")
    minio_document_prefix: str = Field(default="raw-documents", alias="MINIO_DOCUMENT_PREFIX")
    upload_max_bytes: int = Field(default=25 * 1024 * 1024, gt=0, alias="UPLOAD_MAX_BYTES")
    ingestion_queue_name: str = Field(default="ingestion", alias="INGESTION_QUEUE_NAME")
    embedding_provider: str = Field(default="fake", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="fake-embedding", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=8, gt=0, alias="EMBEDDING_DIM")
    embedding_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="EMBEDDING_TIMEOUT_SECONDS",
    )
    embedding_retry_budget: int = Field(default=2, ge=0, alias="EMBEDDING_RETRY_BUDGET")
    embedding_queue_name: str = Field(default="embedding", alias="EMBEDDING_QUEUE_NAME")
    llm_provider: str = Field(default="fake", alias="LLM_PROVIDER")
    llm_model: str = Field(default="fake-llm", alias="LLM_MODEL")
    llm_timeout_seconds: float = Field(default=10.0, gt=0, alias="LLM_TIMEOUT_SECONDS")
    llm_retry_budget: int = Field(default=2, ge=0, alias="LLM_RETRY_BUDGET")
    llm_fake_response_text: str = Field(
        default="Fake LLM response.",
        min_length=1,
        alias="LLM_FAKE_RESPONSE_TEXT",
    )
    vector_store_type: str = Field(default="fake", alias="VECTOR_STORE_TYPE")
    vector_index_dim: int = Field(default=8, gt=0, alias="VECTOR_INDEX_DIM")
    vector_distance_metric: str = Field(default="cosine", alias="VECTOR_DISTANCE_METRIC")
    pgvector_index_type: str = Field(default="hnsw", alias="PGVECTOR_INDEX_TYPE")
    pgvector_hnsw_m: int = Field(default=16, gt=0, alias="PGVECTOR_HNSW_M")
    pgvector_hnsw_ef_construction: int = Field(
        default=64,
        gt=0,
        alias="PGVECTOR_HNSW_EF_CONSTRUCTION",
    )
    worker_queue_name: str = Field(default="ingestion", alias="WORKER_QUEUE_NAME")
    readiness_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        alias="READINESS_TIMEOUT_SECONDS",
    )
    tool_default_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        alias="TOOL_DEFAULT_TIMEOUT_SECONDS",
    )
    tool_default_rate_limit_max_calls: int = Field(
        default=30,
        gt=0,
        alias="TOOL_DEFAULT_RATE_LIMIT_MAX_CALLS",
    )
    tool_default_rate_limit_window_seconds: float = Field(
        default=60.0,
        gt=0,
        alias="TOOL_DEFAULT_RATE_LIMIT_WINDOW_SECONDS",
    )
    agent_default_max_steps: int = Field(
        default=8,
        gt=0,
        alias="AGENT_DEFAULT_MAX_STEPS",
    )
    agent_default_max_tool_calls: int = Field(
        default=5,
        ge=0,
        alias="AGENT_DEFAULT_MAX_TOOL_CALLS",
    )
    agent_default_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="AGENT_DEFAULT_TIMEOUT_SECONDS",
    )
    agent_repeated_action_threshold: int = Field(
        default=3,
        gt=0,
        alias="AGENT_REPEATED_ACTION_THRESHOLD",
    )
    eval_report_dir: str = Field(default="tests/eval/reports", alias="EVAL_REPORT_DIR")

    @field_validator("agent_default_timeout_seconds")
    @classmethod
    def _agent_timeout_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("agent_default_timeout_seconds must be finite")
        return value


def load_settings() -> AppSettings:
    return AppSettings()
