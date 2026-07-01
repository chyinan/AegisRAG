from __future__ import annotations

import math

from pydantic import Field, SecretStr, field_validator, model_validator
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
    embedding_base_url: str | None = Field(default=None, alias="EMBEDDING_BASE_URL")
    embedding_api_key: SecretStr | None = Field(default=None, alias="EMBEDDING_API_KEY")
    embedding_provider_version: str | None = Field(
        default=None,
        alias="EMBEDDING_PROVIDER_VERSION",
    )
    embedding_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="EMBEDDING_TIMEOUT_SECONDS",
    )
    embedding_retry_budget: int = Field(default=2, ge=0, alias="EMBEDDING_RETRY_BUDGET")
    embedding_queue_name: str = Field(default="embedding", alias="EMBEDDING_QUEUE_NAME")
    chunk_size: int = Field(default=800, gt=0, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=120, ge=0, alias="CHUNK_OVERLAP")
    semantic_chunking_enabled: bool = Field(
        default=False, alias="SEMANTIC_CHUNKING_ENABLED"
    )
    semantic_threshold: float = Field(
        default=0.65, ge=0.0, le=1.0, alias="SEMANTIC_THRESHOLD"
    )

    # OCR provider (pluggable — tesseract | paddle | surya)
    ocr_provider: str = Field(default="tesseract", alias="OCR_PROVIDER")
    ocr_executor_max_workers: int = Field(default=2, gt=0, alias="OCR_EXECUTOR_MAX_WORKERS")
    ocr_timeout_seconds: float = Field(default=60.0, gt=0, alias="OCR_TIMEOUT_SECONDS")
    ocr_max_pdf_pages: int = Field(default=500, gt=0, alias="OCR_MAX_PDF_PAGES")

    llm_provider: str = Field(default="fake", alias="LLM_PROVIDER")
    llm_model: str = Field(default="fake-llm", alias="LLM_MODEL")
    llm_timeout_seconds: float = Field(default=10.0, gt=0, alias="LLM_TIMEOUT_SECONDS")
    llm_retry_budget: int = Field(default=2, ge=0, alias="LLM_RETRY_BUDGET")
    llm_fake_response_text: str = Field(
        default="Fake LLM response.",
        min_length=1,
        alias="LLM_FAKE_RESPONSE_TEXT",
    )
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_api_key: SecretStr | None = Field(default=None, alias="LLM_API_KEY")
    llm_max_output_tokens: int | None = Field(default=None, gt=0, alias="LLM_MAX_OUTPUT_TOKENS")
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0, alias="LLM_TEMPERATURE")
    llm_provider_version: str | None = Field(default=None, alias="LLM_PROVIDER_VERSION")
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
    milvus_uri: str = Field(
        default="http://localhost:19530", alias="MILVUS_URI"
    )
    milvus_token: str = Field(default="", alias="MILVUS_TOKEN")
    milvus_index_type: str = Field(default="HNSW", alias="MILVUS_INDEX_TYPE")
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
    file_reader_allowlist_roots: str = Field(default="docs", alias="FILE_READER_ALLOWLIST_ROOTS")
    file_reader_max_file_bytes: int = Field(
        default=32768,
        gt=0,
        alias="FILE_READER_MAX_FILE_BYTES",
    )
    file_reader_max_return_bytes: int = Field(
        default=4096,
        gt=0,
        alias="FILE_READER_MAX_RETURN_BYTES",
    )


    # --- T1/T2 New Settings ---
    # Rate limiting (P0)
    rate_limit_max_requests: int = Field(
        default=100, gt=0, alias="RATE_LIMIT_MAX_REQUESTS"
    )
    rate_limit_window_seconds: float = Field(
        default=60.0, gt=0, alias="RATE_LIMIT_WINDOW_SECONDS"
    )

    # Real Reranker (P0 - replaces FakeReranker)
    rerank_provider: str = Field(default="fake", alias="RERANK_PROVIDER")
    rerank_model: str = Field(
        default="bge-reranker-v2-m3", alias="RERANK_MODEL"
    )
    rerank_base_url: str | None = Field(default=None, alias="RERANK_BASE_URL")
    rerank_api_key: SecretStr | None = Field(default=None, alias="RERANK_API_KEY")
    rerank_timeout_seconds: float = Field(
        default=30.0, gt=0, alias="RERANK_TIMEOUT_SECONDS"
    )

    # Retrieval Cache (P0 - Redis LRU)
    retrieval_cache_enabled: bool = Field(
        default=True, alias="RETRIEVAL_CACHE_ENABLED"
    )
    retrieval_cache_ttl_seconds: float = Field(
        default=300.0, gt=0, alias="RETRIEVAL_CACHE_TTL_SECONDS"
    )
    retrieval_cache_max_size: int = Field(
        default=1024, gt=0, alias="RETRIEVAL_CACHE_MAX_SIZE"
    )

    # CoT + Few-Shot Prompt (P0)
    cot_enabled: bool = Field(default=True, alias="COT_ENABLED")
    few_shot_enabled: bool = Field(default=True, alias="FEW_SHOT_ENABLED")

    # Circuit Breaker
    circuit_breaker_failure_threshold: int = Field(
        default=5, gt=0, alias="CIRCUIT_BREAKER_FAILURE_THRESHOLD"
    )
    circuit_breaker_timeout_seconds: float = Field(
        default=30.0, gt=0, alias="CIRCUIT_BREAKER_TIMEOUT_SECONDS"
    )

    # --- Adaptive Retrieval Routing (P3) ---
    adaptive_routing_enabled: bool = Field(
        default=False, alias="ADAPTIVE_ROUTING_ENABLED"
    )
    adaptive_routing_llm_fallback: bool = Field(
        default=False, alias="ADAPTIVE_ROUTING_LLM_FALLBACK"
    )
    adaptive_routing_factual_top_k: int = Field(
        default=5, ge=1, le=100, alias="ADAPTIVE_ROUTING_FACTUAL_TOP_K"
    )
    adaptive_routing_factual_score_threshold: float | None = Field(
        default=0.3, ge=0.0, le=1.0, alias="ADAPTIVE_ROUTING_FACTUAL_SCORE_THRESHOLD"
    )
    adaptive_routing_complex_top_k: int = Field(
        default=10, ge=1, le=100, alias="ADAPTIVE_ROUTING_COMPLEX_TOP_K"
    )
    adaptive_routing_complex_score_threshold: float | None = Field(
        default=0.3, ge=0.0, le=1.0, alias="ADAPTIVE_ROUTING_COMPLEX_SCORE_THRESHOLD"
    )
    adaptive_routing_comparison_top_k: int = Field(
        default=20, ge=1, le=100, alias="ADAPTIVE_ROUTING_COMPARISON_TOP_K"
    )
    adaptive_routing_comparison_score_threshold: float | None = Field(
        default=None, alias="ADAPTIVE_ROUTING_COMPARISON_SCORE_THRESHOLD"
    )
    adaptive_routing_confidence_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, alias="ADAPTIVE_ROUTING_CONFIDENCE_THRESHOLD"
    )

    # Query Rewriting (P1 - HyDE-based retrieval recall improvement)
    query_rewrite_enabled: bool = Field(
        default=True, alias="QUERY_REWRITE_ENABLED"
    )
    query_rewrite_model: str = Field(
        default="", alias="QUERY_REWRITE_MODEL"
    )

    # Graph RAG (knowledge-graph-augmented retrieval)
    graph_rag_enabled: bool = Field(
        default=False, alias="GRAPH_RAG_ENABLED"
    )
    graph_rag_model: str = Field(
        default="deepseek-v4-flash", alias="GRAPH_RAG_MODEL"
    )
    graph_rag_max_hops: int = Field(
        default=2, ge=1, le=5, alias="GRAPH_RAG_MAX_HOPS"
    )

    @field_validator("agent_default_timeout_seconds")
    @classmethod
    def _agent_timeout_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("agent_default_timeout_seconds must be finite")
        return value

    @field_validator("embedding_provider", "embedding_model", "llm_provider", "llm_model")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator(
        "embedding_base_url",
        "embedding_provider_version",
        "llm_base_url",
        "llm_provider_version",
    )
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _real_providers_require_endpoint_and_secret(self) -> AppSettings:
        embedding_provider = self.embedding_provider.strip().lower()
        if embedding_provider in {"openai_compatible", "openai", "qwen", "deepseek", "ollama"}:
            if self.embedding_base_url is None:
                raise ValueError("EMBEDDING_BASE_URL is required for real embedding providers")

        llm_provider = self.llm_provider.strip().lower()
        if (
            llm_provider != "fake"
            and llm_provider in {"openai_compatible", "openai", "qwen", "deepseek"}
        ):
            if self.llm_base_url is None:
                raise ValueError("LLM_BASE_URL is required for real LLM providers")
            if self.llm_api_key is None or not self.llm_api_key.get_secret_value().strip():
                raise ValueError("LLM_API_KEY is required for real LLM providers")
        return self


def load_settings() -> AppSettings:
    return AppSettings()
